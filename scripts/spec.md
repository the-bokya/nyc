# scripts

Artifact fetch, single-host staging, and bare-metal preflight + **deploy**
(`deploy.py` driving the pyinfra `inventory.py` + `provision.py` / `teardown.py`).

| File | What it does |
|---|---|
| `install_firecracker.sh` | Idempotently download the pinned firecracker binary into `bin/`. |
| `fetch_artifacts.sh` | Idempotently fetch the kernel + rootfs into `assets/` and generate an ssh keypair. (Per-VM SSH/DNS/fstab are injected at boot by `vm.inject`; the shared key + resolv.conf are baked into the base rootfs by `provision.py`.) |
| `stage.sh [N] [--real] [--keep] [--no-tests]` | Single-host emulation: boot N dadar nodes in `./stage/`, run the e2e suite. `host` stays `127.0.0.1`, so the cross-node overlay isn't exercised here. `--real` builds each node a loopback-backed VG (sparse `pv.img` in its folder); on exit it kills firecracker first (so the LV devices close), then detaches the loops + removes those VGs, and a startup sweep reclaims any loops/VGs a prior SIGKILL'd run leaked. |
| `preflight.py <cluster.toml>` | **Read-only** bare-metal readiness probe (below). |
| `deploy.py {up,down,status,ssh,overlay-check} <cluster.toml> [--purge]` | Bare-metal orchestrator over the pyinfra deploys (below). |
| `inventory.py` | pyinfra inventory: turns `cluster.toml` (path in `$NYC_CLUSTER`) into hosts + per-host `host.data`. |
| `provision.py` / `teardown.py` | pyinfra deploys: idempotent per-node setup / reverse, driven by `deploy.py` over agent-forwarded ssh. |
| `templates/` | Jinja artifacts rendered by `provision.py`: `sudoers.j2`, `nyc-node.service.j2`, `nyc-caddy.service.j2`, `Caddyfile.j2`. |
| `cluster.toml.example` | Inventory schema for `preflight.py` / `deploy.py`. |

**Locked choices:** delivery = `git clone` (recursive) at the inventory `ref`;
supervision = systemd; inventory = TOML; git auth = SSH agent forwarding; node
login = passwordless-sudo user; per-VM key = rootfs clone + `debugfs`; storage =
LVM thin pool on one block device per machine (`lvm_device`). One node folder
per machine at `<remote_dir>/nyc/node`.

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

Thin orchestrator over the pyinfra deploys (`inventory.py` + `provision.py` /
`teardown.py`). Run it inside the nyc venv so `pyinfra` is on `PATH`
(`uv run scripts/deploy.py up cluster.toml`): it sets `$NYC_CLUSTER` and shells
out to `pyinfra`, then adds the app-level steps pyinfra doesn't model. API calls
(health, VPC, smoke, overlay probe) run **on a node via ssh → its own
`localhost`**, so the control box needs no private-network access.

- **`up`**: validate (one `bootstrap=true`; no CIDR overlap); generate-or-reuse
  a **shared VM keypair** at `<inventory_dir>/.nyc-deploy/` (one key ssh-jumps
  into any VM); `pyinfra provision.py --limit <bootstrap>` + wait `/health`, then
  `--limit <joiners>` + wait each; create the `default` VPC (idempotent); smoke
  (`spawn_vm` → running → delete).
- **`down`**: best-effort delete VMs via the API, then `pyinfra teardown.py`
  (`NYC_PURGE=1` for `--purge`, which also removes packages + checkout + the VG).
- **`status`**: per-node `/health` plus the cluster's `/nodes` count.
- **`ssh` / `overlay-check`**: read-only operational commands (below).

### provision.py / teardown.py

pyinfra deploys, idempotent, driven by `deploy.py`; config arrives as `host.data`
from `inventory.py`. Native operations (`apt.packages`, `server.sysctl`,
`files.template`, `files.put`, `systemd.service`) carry their own idempotency;
the inherently imperative steps (uv/Caddy installers, init-in-place repo sync,
the `debugfs` rootfs bake, the anchored kernel/iptables teardown) are
check-then-act `server.shell`. The literal unit/sudoers/Caddyfile artifacts live
in `templates/*.j2`.

**`provision.py`** sets a node up end to end: apt (incl. `lvm2`) + `uv` + Caddy →
init-in-place checkout at `ref` (recursive submodules) → `uv sync` → install
firecracker + rqlited → fetch artifacts, upload + bake the shared key +
resolv.conf into the base rootfs → `ip_forward` → sudoers (now also the
LVM/device toolchain) → `dadar init` → write nyc's `lvm_*` keys into the node's
`config.toml` (`lvm_device` is the one block device nyc owns; `lvm_vg`/
`lvm_thinpool` name the VG/pool nyc creates on first start) → enable+start
`nyc-node.service` + `nyc-caddy.service` (per-node Caddyfile, automatic HTTPS;
each restarts only when its unit template changed).

Two non-obvious implementation points: (1) `git fetch` and `submodule update`
both run with `GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new'`
because a freshly imaged node has an empty `~/.ssh/known_hosts` and would
otherwise stall waiting for interactive host-key confirmation; `accept-new`
auto-adds the key on first contact while still rejecting a *changed* key on
re-runs. (2) pyinfra v3's `files.template` passes template variables as plain
`**kwargs`, not a `data={}` dict — all four template calls in this file pass
their variables as keyword arguments accordingly. The VG/thin-pool/
default-golden are built lazily at node startup by `client/volume/pool.ensure`
(self-healing on reboot), not by `provision.py`. **`teardown.py`** reverses it:
stop+rm units → `ip netns/link del` the anchored name patterns
(`../NETWORKING.md` §7) → drop the `NYC-*` iptables chains → restore `ip_forward`
→ rm node folder + sudoers → **remove the LVM VG + PV** (unconditional: plain
`down` wipes the thin pool and returns the block device empty). `NYC_PURGE=1`
(`down --purge`) additionally removes caddy (`/usr/bin/caddy`) + uv
(`~/.local/bin/uv`) + apt packages added by `up` (diffed against the `dpkg`
snapshot) including `lvm2` + the checkout, so `up` after `down --purge` rebuilds
fully from scratch. Pre-`up` snapshots live in `$HOME/.nyc` (outside the
purge-able checkout) so teardown can always find them.

## VM TTL (auto-delete)

Optional `vm_ttl_minutes` in `[cluster]` (0 / omitted = off). `deploy.py` passes
it through `host.data`; `provision.py` bakes it into `nyc-node.service` as
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
