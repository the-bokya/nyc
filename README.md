# nyc

Distributed Firecracker VM manager. Downstream of [dadar](../dadar).

Named after New York City — like the borough, each node hosts its own population
of VMs, and they all share one connected transit map (the raft cluster).

## What it does

- Per-node Firecracker microVMs with isolated `/dev/net/tun` taps in Linux network namespaces.
- Cluster-wide VPCs (CIDR-scoped private networks). VMs in the same VPC see each other on a node-local Linux bridge today; cross-node overlay is on the roadmap.
- Read-only shared rootfs; per-VM read/write data volumes (files for now, LVM later).
- DB-is-source-of-truth: a background reconciler kills orphan VMs/taps/volumes and recreates rows whose backing resource went missing.

The full surface is exposed over a REST API at `/vpcs`, `/volumes`, `/vms` —
plus `POST /reconcile` for forcing an immediate convergence pass.

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
Firecracker (requires `/dev/kvm` and passwordless `sudo` for `ip`, `mkfs.ext4`,
`mount`, `firecracker`).

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
│   │   ├── network/      # netns, tap, bridge, IP allocator
│   │   ├── volume/       # data volume create/delete
│   │   └── privops.py    # sudo shim (real | fake), selected by NYC_BACKEND
│   └── reconciler/       # background convergence loop
├── tests/                # pytest, NYC_BACKEND=fake
├── scripts/
│   ├── install_firecracker.sh
│   ├── fetch_artifacts.sh
│   └── stage.sh
└── assets/               # populated by fetch_artifacts.sh
```

Each directory has its own `spec.md` documenting the contract and behaviour.
