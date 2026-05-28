# scripts

Operational scripts: artifact fetch, single-host staging, bare-metal preflight,
and the bare-metal **deploy** orchestration (`deploy.py` + `provision.sh` +
`teardown.sh`).

| File | What it does |
|---|---|
| `install_firecracker.sh` | Idempotently download the pinned firecracker binary into `bin/firecracker`. |
| `fetch_artifacts.sh` | Idempotently fetch the kernel (`assets/vmlinux`) + rootfs (`assets/rootfs.ext4`) and generate an ssh keypair. Per-VM DNS/SSH/fstab are injected at boot via debugfs (`vm.inject`); the shared key + resolv.conf are baked into the base rootfs by `provision.sh`. |
| `stage.sh [N] [--real] [--keep] [--no-tests]` | Single-host emulation: boot N dadar nodes in `./stage/`, run the e2e suite. `host` stays `127.0.0.1`, so cross-node overlay is not exercised here. |
| `preflight.py <cluster.toml>` | **Read-only** bare-metal readiness probe (see below). |
| `deploy.py {up,down,status} <cluster.toml> [--purge]` | Bare-metal orchestrator (see below). |
| `provision.sh` | Idempotent per-node setup, run by `deploy.py` over `ssh -A`; env-driven. |
| `teardown.sh` | Idempotent reverse of `provision.sh`; `PURGE=1` also removes packages + checkout. |
| `cluster.toml.example` | Inventory schema for `preflight.py` / `deploy.py`. Copy to `cluster.toml`. |

## preflight.py

Run before any bare-metal deploy to shrink unknowns. Reads the inventory and,
over `ssh -A` (agent forwarding, matching the deploy's git path), runs a
bundled read-only probe on each node in parallel. Mutates nothing.

```
python3 scripts/preflight.py cluster.toml        # probe every node
python3 scripts/preflight.py --local cluster.toml # run checks on THIS machine
python3 scripts/preflight.py --print-script       # dump the remote probe
```

Per-node checks (`PASS` / `WARN` / `FAIL` / `INFO`):

| Check | FAIL means |
|---|---|
| `os` | not Ubuntu (WARN if Ubuntu < 24.04; 24.04+ passes) |
| `arch` | not `x86_64` |
| `kvm` | no `/dev/kvm` (WARN if present but not rw for the user) |
| `virt` | (WARN) no `vmx`/`svm` CPU flag |
| `sudo` | `sudo -n true` fails — passwordless sudo is required |
| `internet` | no egress (VM internet, apt, git all need it) |
| `dns` | no name resolution |
| `git` | `git ls-remote <repo_url>` fails — agent forwarding or repo access broken |
| `peer:<ip>` | a peer's private IP is unreachable on tcp/22 (private network broken) |
| `port:<n>` | (WARN) http/rqlite port already in use |
| `domain` | (WARN) domain missing / not pointing at `public_host` (Caddy ACME will fail) |
| `clock` | (WARN) NTP not synced (raft prefers low skew) |
| `tools` | (INFO) packages the deploy will install |

Plus a top-level **CIDR overlap** check: the default `vpc_cidr` must not
contain any node's underlay `host`, or NAT/routing breaks. Exit code is
non-zero if any node has a `FAIL`.

## deploy.py

Stdlib-only (`tomllib`/`subprocess`/`argparse`/`concurrent.futures`) hybrid
orchestrator: it uploads + runs the bash `provision.sh` / `teardown.sh` on each
node over `ssh -A -o StrictHostKeyChecking=accept-new`. No third-party deps so
it runs on any control box with python3 + ssh.

```
python3 scripts/deploy.py up     cluster.toml          # provision + smoke
python3 scripts/deploy.py down   cluster.toml [--purge] # teardown
python3 scripts/deploy.py status cluster.toml          # health + node count
python3 scripts/deploy.py ssh    cluster.toml <vm_id>  # ssh into a VM (see below)
python3 scripts/deploy.py overlay-check cluster.toml <vpc_id>  # VXLAN/FDB/NAT audit
```

- **`up`**: validate inventory (exactly one `bootstrap=true`; `vpc_cidr` must
  not overlap any node `host`); generate-or-reuse a **shared VM keypair** at
  `<inventory_dir>/.nyc-deploy/` (distributed to every node so one private key
  ssh-jumps into any VM); provision the **bootstrap node first**, wait
  `/health`, then provision joiners in parallel; create the single `default`
  VPC (idempotent); run a smoke test (`spawn_vm` → assert running → delete).
- **`down`**: best-effort delete all VMs via the API if reachable, then run
  `teardown.sh` on every node in parallel. `--purge` also removes installed
  packages + the checkout.
- **`status`**: per-node `/health` plus the cluster's `/nodes` count.

API calls (health, VPC, smoke) are issued by **ssh-ing into a node and curling
its own `localhost:http_port`**, so the control machine needs no
private-network access. `provision.sh`/`teardown.sh` receive all parameters via
environment variables on the ssh command line (a single round-trip per node).

### provision.sh (env-driven, idempotent, `sudo -n`)

preflight (`/dev/kvm`, `x86_64`, passwordless sudo) → apt packages + `uv` +
Caddy (static binary — a fresh LTS codename may not be in the apt repo) →
clone/fetch the repo at `REF` recursively → `uv sync` dadar+nyc → install
firecracker + rqlited → fetch artifacts, write the distributed shared keypair,
bake the shared pubkey + `resolv.conf` into the base rootfs (`debugfs`) →
snapshot+enable `ip_forward` → write `/etc/sudoers.d/nyc` (validated with
`visudo -cf`) → `dadar init --host …` in `<remote_dir>/nyc/node` → install +
start `nyc-node.service` (`--bootstrap` or `--join <boot_host>:<raft_port>`) →
install + start `nyc-caddy.service` (per-node Caddyfile, automatic HTTPS).

### teardown.sh (reverse, idempotent, `PURGE=1` for full purge)

stop/disable + remove both systemd units → `ip netns/link del` everything
matching the anchored regexes (REBUILD 0.3) → delete the `NYC-*` iptables jumps
+ flush/`-X` both chains → restore `ip_forward` from `.pre_ip_forward` + remove
the sysctl drop-in → remove the node folder → remove the sudoers drop-in.
`PURGE=1` additionally `apt-get remove`s the packages we added (diffed against a
`dpkg` snapshot taken on `up`) and removes the checkout. Default `down` returns
the host to its pre-`up` config and leaves base packages, so `up` after `down`
rebuilds cleanly.

## VM TTL (auto-delete)

Optional `vm_ttl_minutes` in `[cluster]` (0 / omitted = disabled). `deploy.py`
passes it as `VM_TTL_MINUTES`, which `provision.sh` bakes into the
`nyc-node.service` unit as `Environment=NYC_VM_TTL_MINUTES=<n>`. Each reconciler
pass then runs `reconciler/ttl_pass.py`, which deletes this node's VMs whose
`created_at` is older than the TTL (same teardown path as `DELETE /vms`; the
auto data volume is not cascaded, matching DELETE). No-op at 0, so single-host
staging and clusters that don't set it are unaffected.

## SSH into a VM (jump through the hosting node)

A VM's IP lives on the VPC overlay and is only routable from the node hosting
it, so we ProxyJump through that node. No special host account: we reuse the
cluster `ssh_user` (operators already have node login) for the jump and the
**shared VM key** (`<inventory_dir>/.nyc-deploy/id_ed25519`, generated by
`deploy up` and baked into every rootfs) to log into the VM as root:

```
ssh -J <ssh_user>@<node_domain> -i .nyc-deploy/id_ed25519 root@<vm_vpc_ip>
```

`deploy.py ssh <cluster.toml> <vm_id>` automates this: it reads the VM's IP and
hosting node from the API, then execs the above. (An earlier, more elaborate
design — a dedicated restricted `nycjump` host user — was dropped in favour of
this simpler reuse of the existing login.)

## overlay-check

`deploy.py overlay-check <cluster.toml> <vpc_id>` audits the VXLAN overlay for
one VPC across the cluster — automating the kernel-state half of the manual
diagnostic ladder. It reads `/nodes` + `/vms` from the bootstrap node, computes
the expected per-node resource names, `vni`, and anycast MAC (the same
deterministic functions as `nyc.client.network.overlay`, reimplemented in
stdlib so `deploy.py` stays dependency-free), then ssh-runs a **read-only**
probe on each node and prints a per-node table:

- overlay dev `vx-<n4>-<v4>` present (only expected on nodes hosting a VM in the
  VPC; a `vx-` with no VM is flagged STALE, not failed)
- `vni`, VXLAN `local` = the node's underlay IP, bridge `master`
- FDB head-end peers == exactly the other nodes' underlay IPs
- bridge gateway IP + anycast MAC (MAC identical on every node ⇒ each is
  validated against the same deterministic value)
- `ip_forward=1` (NAT/internet egress)

Exits non-zero if any node has a `[BAD]` row. It does **not** test the live
datapath (cross-node UDP/4789 reachability and VM↔VM ping stay manual — see the
overlay diagnostics in `nyc/nyc/client/network/spec.md` / the README).
