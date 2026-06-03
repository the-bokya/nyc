# nyc

Distributed Firecracker VM manager. Downstream of [dadar](../dadar).

Named after New York City — like the borough, each node hosts its own population
of VMs, and they all share one connected transit map (the raft cluster).

## What it does

- Per-node Firecracker microVMs with isolated `/dev/net/tun` taps in Linux network namespaces.
- Cluster-wide VPCs (CIDR-scoped private networks). VMs in the same VPC reach each other across nodes over a per-VPC VXLAN overlay; the VPC bridge is an anycast gateway, so VMs also get internet access (NAT) through their local node.
- **LVM thin storage**: each node owns one block device as a thin pool. A VM's writable rootfs is a thin **clone of a golden image** (configured offline with `debugfs` — ssh key, DNS, fstab — never a full copy), and its data disk is a thin LV. Declarative **snapshot + golden-image** APIs are generic over root and data disks: freeze a data volume *or* a VM's root, promote it to an image, then `spawn {root_image, data_image}` clones both for near-instant, fully-baked deploys. Thin snapshots are independent, so deleting one never breaks the VMs cloned from it.
- DB-is-source-of-truth: a background reconciler kills orphan VMs/taps/volumes/snapshots, reaps TTL-expired VMs, and re-syncs each VPC's VXLAN flood list as nodes join/leave. (Recreating missing resources is not yet done — see [`FUTURE.md`](FUTURE.md).)

The cross-node networking is documented ground-up in [`NETWORKING.md`](NETWORKING.md).

The full surface is exposed over a REST API at `/vpcs`, `/volumes`, `/vms` —
plus `POST /reconcile` for forcing an immediate convergence pass. The turnkey
entrypoint is `POST /vms/spawn {vm_name, ssh_key}`: it places the VM in the
default VPC on a random node, auto-creates its data volume, and bakes the
given ssh key into that VM's own rootfs — no vpc/node/volume bookkeeping for
the caller.

## Quickstart (single host, three emulated nodes)

```sh
cd nyc
uv sync
scripts/stage.sh 3
```

`stage.sh` downloads the Firecracker binary, kernel and rootfs into `assets/`,
boots a 3-node `dadar` cluster in `./stage/`, then runs the e2e test that
drives every endpoint and asserts cross-node propagation. Defaults to
`NYC_BACKEND=fake` (no `sudo`, no `/dev/kvm`). Pass `--real` to flip to live
Firecracker (requires `/dev/kvm` and passwordless `sudo` for `ip`/`firecracker`,
the LVM toolchain + `losetup` for the loopback-backed volume group, and
`mkfs.ext4`/`debugfs`/`dd` against LV devices — `stage.sh --real` prints the
exact sudoers line).

## Layout

```
nyc/
├── nyc/
│   ├── app.py            # DadarApp(tables=[...], routers=[...])
│   ├── config.py         # paths to bin/, assets/, vms/ dirs per node folder
│   ├── tables/           # Vpcs, Volumes, Vms (ORM models)
│   ├── routers/          # FastAPI routers — HTTP plumbing only
│   ├── client/           # Firecracker client — no HTTP, callable from anywhere
│   │   ├── env/          # one-shot per-VM directory setup
│   │   ├── vm/           # boot, kill, status, ssh, config
│   │   ├── network/      # netns, tap, bridge, VXLAN overlay, NAT, IP allocator
│   │   ├── volume/       # LVM thin: lv primitives, pool substrate, volumes, snapshots/goldens
│   │   └── privops.py    # sudo shim (real | fake, incl. an LVM model), selected by NYC_BACKEND
│   └── reconciler/       # background convergence loop
├── tests/                # pytest, NYC_BACKEND=fake
├── scripts/             # stage.sh (single-host) + deploy.py (bare-metal)
│   ├── install_firecracker.sh, fetch_artifacts.sh  # binary + kernel/rootfs/ssh-key
│   ├── stage.sh             # single-host: boot N nodes, run the e2e suite
│   ├── preflight.py         # read-only SSH readiness probe for bare-metal nodes
│   ├── deploy.py            # bare-metal orchestrator: up/down/status/ssh/overlay-check
│   ├── inventory.py        # pyinfra inventory: cluster.toml -> hosts + host.data
│   ├── provision.py, teardown.py  # pyinfra deploys: per-node setup / reverse, driven by deploy.py
│   ├── templates/          # Jinja sudoers / systemd unit / Caddyfile artifacts
│   └── cluster.toml.example # bare-metal inventory schema
└── assets/               # populated by fetch_artifacts.sh
```

Each directory has its own `spec.md` documenting the contract and behaviour;
roadmap and known gaps are in [`FUTURE.md`](FUTURE.md).
