# scripts

Artifact fetch, single-host staging, and bare-metal preflight + **deploy**
(`deploy.py` + `provision.sh` + `teardown.sh`).

| File | What it does |
|---|---|
| `install_firecracker.sh` | Idempotently download the pinned firecracker binary into `bin/`. |
| `fetch_artifacts.sh` | Idempotently fetch the kernel + rootfs into `assets/` and generate an ssh keypair. (Per-VM SSH/DNS/fstab are injected at boot by `vm.inject`; the shared key + resolv.conf are baked into the base rootfs by `provision.sh`.) |
| `stage.sh [N] [--real] [--keep] [--no-tests]` | Single-host emulation: boot N dadar nodes in `./stage/`, run the e2e suite. `host` stays `127.0.0.1`, so the cross-node overlay isn't exercised here. |
| `preflight.py <cluster.toml>` | **Read-only** bare-metal readiness probe (below). |
| `deploy.py {up,down,status,ssh,overlay-check} <cluster.toml> [--purge]` | Bare-metal orchestrator (below). |
| `provision.sh` / `teardown.sh` | Idempotent per-node setup / reverse, run by `deploy.py` over `ssh -A`; env-driven. Regenerated from `deploy.prompt.md`. |
| `deploy.prompt.md` | Reproduction prompt for `provision.sh`/`teardown.sh` + the literal sudoers/systemd/Caddyfile templates. |
| `cluster.toml.example` | Inventory schema for `preflight.py` / `deploy.py`. |

**Locked choices:** delivery = `git clone` (recursive) at the inventory `ref`;
supervision = systemd; inventory = TOML; git auth = SSH agent forwarding; node
login = passwordless-sudo user; per-VM key = rootfs copy + `debugfs`. One node
folder per machine at `<remote_dir>/nyc/node`.

## preflight.py

Over `ssh -A` (agent forwarding, matching the deploy's git path), run a bundled
read-only probe on each node in parallel — mutates nothing. `--local` runs on
this machine; `--print-script` dumps the probe. Per-node checks emit
`PASS`/`WARN`/`FAIL`/`INFO`: `os` (Ubuntu, WARN < 24.04), `arch` (x86_64), `kvm`
(WARN if not rw), `virt` (WARN: no `vmx`/`svm`), `sudo` (`sudo -n true`),
`internet`, `dns`, `git` (`git ls-remote`), `peer:<ip>` (tcp/22), `port:<n>`
(WARN: in use), `domain` (WARN: not → `public_host`, Caddy ACME), `clock` (WARN:
NTP), `tools` (INFO). Plus a top-level **CIDR overlap** check (`vpc_cidr` must
not contain any node `host`). Exit is non-zero if any node has a `FAIL`.

## deploy.py

Stdlib-only (`tomllib`/`subprocess`/`argparse`/`concurrent.futures`): uploads +
runs `provision.sh`/`teardown.sh` on each node over `ssh -A` (params as env on
the ssh line). API calls (health, VPC, smoke) run **on a node via ssh → its own
`localhost`**, so the control box needs no private-network access.

- **`up`**: validate (one `bootstrap=true`; no CIDR overlap); generate-or-reuse
  a **shared VM keypair** at `<inventory_dir>/.nyc-deploy/` (one key ssh-jumps
  into any VM); provision the bootstrap node first + wait `/health`, then joiners
  in parallel; create the `default` VPC (idempotent); smoke (`spawn_vm` → running
  → delete).
- **`down`**: best-effort delete VMs via the API, then parallel `teardown.sh`.
  `--purge` also removes packages + checkout.
- **`status`**: per-node `/health` plus the cluster's `/nodes` count.

### provision.sh / teardown.sh

Per-node bash, env-driven, idempotent (check-then-act), `sudo -n`.
**`provision.sh`** sets a node up end to end: apt + `uv` + Caddy → fetch/checkout
the repo at `REF` → `uv sync` → install firecracker + rqlited → fetch artifacts,
distribute the shared key, bake key + resolv.conf into the base rootfs →
`ip_forward` → sudoers → `dadar init` → start `nyc-node.service` +
`nyc-caddy.service` (per-node Caddyfile, automatic HTTPS). **`teardown.sh`**
reverses it by the anchored regexes in `teardown.sh` (name patterns:
`../NETWORKING.md` §7): rm units → `ip netns/link del` matches → drop the `NYC-*`
iptables chains → restore `ip_forward` → rm node folder + sudoers. `PURGE=1` also
removes added packages (diffed against a `dpkg` snapshot from `up`) + the
checkout, so `up` after `down` rebuilds cleanly. **Reproduction-grade ordered
steps + the literal sudoers / systemd / Caddyfile templates:
[`deploy.prompt.md`](deploy.prompt.md).**

## VM TTL (auto-delete)

Optional `vm_ttl_minutes` in `[cluster]` (0 / omitted = off). `deploy.py` passes
it as `VM_TTL_MINUTES`; `provision.sh` bakes it into `nyc-node.service` as
`NYC_VM_TTL_MINUTES=<n>`, read by `reconciler/ttl_pass.py` (deletes this node's
VMs older than the TTL — same path as `DELETE /vms`, auto volume not cascaded).

## SSH into a VM (jump through the hosting node)

A VM's IP is only routable from its hosting node, so we ProxyJump through that
node, reusing the cluster `ssh_user` for the jump and the **shared VM key**
(`.nyc-deploy/id_ed25519`, baked into every rootfs) to log in as root.
`deploy.py ssh <cluster.toml> <vm_id>` automates it (reads the VM IP + node from
the API, then execs):

```
ssh -J <ssh_user>@<node_domain> -i .nyc-deploy/id_ed25519 root@<vm_vpc_ip>
```

## overlay-check

`deploy.py overlay-check <cluster.toml> <vpc_id>` audits the VXLAN overlay for
one VPC. It reads `/nodes` + `/vms` from the bootstrap node, recomputes the
expected per-node names/`vni`/anycast MAC (the deterministic functions of
`../nyc/client/network/overlay.py`, reimplemented in stdlib so `deploy.py` stays
dependency-free), ssh-runs a **read-only** probe per node, and prints a table:
overlay dev present (STALE if no local VM), `vni`, VXLAN `local` = underlay IP,
bridge `master`, FDB peers == the other nodes' underlay IPs, bridge gateway IP +
anycast MAC (identical everywhere), `ip_forward=1`. Exits non-zero on any
`[BAD]`. It does **not** test the live datapath (4789 reachability + VM↔VM ping
stay manual — see [`../FUTURE.md`](../FUTURE.md)).
