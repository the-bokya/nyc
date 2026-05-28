# nyc bare-metal: rebuild spec (Parts B–F)

Authoritative, reproduction-grade plan for taking `nyc` from single-host
staging to a **multi-machine bare-metal cluster** with a VXLAN VPC overlay,
internet-connected VMs, a `spawn_vm` convenience API, full VM lifecycle, and a
one-command deploy/teardown. If the code for Parts B–F vanishes, this file
plus the existing per-directory `spec.md`s must reproduce it.

**Part A (the dadar substrate) is already done and lives in the `dadar`
submodule** — `NodeConfig.host/public_host/domain`, `dadar init/run --host`,
rqlite `0.0.0.0` bind + `-http-adv-addr`/`-raft-adv-addr` when routable,
uvicorn binds `cfg.host`, and `nodes.{host,public_host,domain}` columns written
by `register_self`. This spec consumes that and does **not** restate it.

---

## 0. Invariants (must stay true at every step)

- **Fake backend is the test contract.** Every privileged action goes through
  `nyc.client.privops.run`. New shellouts (`iptables`, `sysctl`, `bridge`,
  `ip link … type vxlan`) MUST be parseable by `privops_fake` so the unit
  suite passes with `NYC_BACKEND=fake` (no root, no `/dev/kvm`).
- **Single-host `scripts/stage.sh N` stays green**, fake and `--real`. The
  overlay/NAT code must be no-op-safe when `host=127.0.0.1` / one node.
- **Dependency direction:** `nyc` may import `dadar`; never the reverse. The
  `client/` layer stays pure (no `dadar`, no HTTP) — routers/reconciler resolve
  DB-derived values (peer IPs, vni) and pass them into `client`.
- **IFNAMSIZ = 15.** All interface names ≤ 15 chars.
- **Directory-level isolation:** a node's entire runtime state lives under its
  node folder; teardown is `rm -rf <node_folder>` + pattern-purge of kernel
  state by the exact regexes below.
- **smell.md:** file-per-action, functions ≤ 12 lines, tests alongside, each
  new directory gets a `spec.md`.

## 0.1 Locked decisions
Per-VM SSH key = **rootfs copy + `debugfs`** · code delivery = **git clone
`equator` recursively** at an inventory `ref` · supervision = **systemd** ·
inventory = **TOML** · git auth = **SSH agent forwarding** · node SSH user =
**passwordless sudo**.

## 0.2 Assumptions / caveats (confirm before Part F run)
- Bare-metal nodes run **Ubuntu 24.04+** (observed: 26.04 LTS), have
  **`/dev/kvm`**, a **default-route uplink with internet egress** (required for
  VM internet — NAT can't conjure it), and a **private network** for inter-node
  traffic. firecracker runs as root via `sudo` so it reaches `/dev/kvm`
  regardless of the login user's group; the deploy still adds the user to
  `kvm`.
- Install **Caddy from its static binary** (GitHub release), not the apt repo —
  a fresh LTS codename may not be in the apt repo yet.
- Each node's **domain resolves to its public IP** and **80/443 are
  internet-reachable** (Caddy ACME). 
- **Default VPC CIDR must not overlap the underlay private network.** Default
  `172.16.0.0/16`, overridable; deploy hard-errors on overlap with any node
  `host`.
- iptables on 24.04 is the nft shim — fine; we use the `iptables` command.

## 0.3 Naming & regex conventions (used by setup AND teardown)
| Resource | Name | Teardown regex |
|---|---|---|
| VPC bridge (root ns) | `br-<node[:4]>-<vpc[:4]>` | `^br-[0-9a-f]{4}-[0-9a-f]{4}$` |
| VXLAN dev (root ns, enslaved to bridge) | `vx-<node[:4]>-<vpc[:4]>` | `^vx-[0-9a-f]{4}-[0-9a-f]{4}$` |
| VM netns | `vm-<vm[:8]>` | `^vm-[0-9a-f]{8}$` |
| veth pair | `vmh-<vm[:8]>` / `vmn-<vm[:8]>` | `^vm[hn]-[0-9a-f]{8}$` |
| in-netns bridge | `nbr0` (fixed) | n/a (dies with netns) |
| iptables chains | `NYC-POSTROUTING` (nat), `NYC-FORWARD` (filter) | delete jump + flush + `-X` |

**Never glob `br-*`** (collides with `virbr0`, docker `br-<hash>`, user `br0`).
Always use the anchored regexes.

## 0.4 Deterministic per-VPC derivations (identical on every node, no coordination)
```python
import hashlib
def vni_for(vpc_id: str) -> int:        # range [1, 2**24-1]
    return int(hashlib.sha256(vpc_id.encode()).hexdigest(), 16) % (2**24 - 1) + 1
def anycast_mac(vpc_id: str) -> str:    # locally-administered, same on all nodes
    h = hashlib.sha256(vpc_id.encode()).digest()
    return "02:" + ":".join(f"{b:02x}" for b in h[:5])
```

---

## Part B — cross-node HTTP proxy

`nyc/routers/_proxy.py`: replace the hardcoded loopback with the registry
`host`.
```python
def _base_url(client, node_id):
    row = Nodes(client).docs.get(where={"node_id": node_id}).__dict__
    return f"http://{row['host']}:{row['http_port']}"   # was 127.0.0.1
```
Single-host: `host=127.0.0.1` → unchanged. Inter-node traffic stays plaintext
on the private network (Caddy/TLS is external ingress only). No test change for
fake suite; covered live in Part F.

---

## Part C — VPC overlay (VXLAN) + internet (PRIORITY)

### Topology per VPC per node (root ns unless noted)
```
br-<n4>-<v4>     bridge: gateway IP (first host of CIDR) + anycast_mac(vpc)
vx-<n4>-<v4>     vxlan id=vni dstport 4789 local <node_host> nolearning, master <bridge>
                 FDB: head-end replication → 00:..:00 dst <each OTHER node host>
vmh-<vm8>        veth host side → master <bridge>
[netns vm-<vm8>] vmn-<vm8> + nbr0 + tap0   (unchanged from today)
guest eth0       kernel ip= boot arg (unchanged) + DNS (below)
```
Anycast gateway: every node's bridge carries the **same** gateway IP **and**
`anycast_mac(vpc)`. A Linux bridge terminates frames to its own MAC locally and
does not flood them, so each node is the local L3 gateway (→ local NAT egress)
with no duplicate-IP conflict across the overlay. Intra-VPC L2 flows over VXLAN;
internet egresses locally per node.

### New/changed `client/network` files
- `vxlan.py` (new):
  - `name_for(node_id, vpc_id) -> str`
  - `ensure(name, vni, local_ip, bridge)` →
    `ip link add <name> type vxlan id <vni> dstport 4789 local <local_ip> nolearning`;
    `ip link set <name> master <bridge>`; `ip link set <name> up`. Idempotent
    via an `exists(name)` check (mirror `bridge.exists`).
  - `set_fdb(name, peers: list[str])` → reconcile FDB to exactly `peers`:
    `bridge fdb append 00:00:00:00:00:00 dev <name> dst <peer>` for each (and
    `bridge fdb del` for removed ones; simplest correct impl re-reads
    `bridge fdb show dev <name>` and diffs).
  - `delete(name)` → `ip link del <name>` if exists.
- `bridge.py`: extend `ensure(bridge, host_ip_cidr, mac=None)` to also
  `ip link set <bridge> address <mac>` when `mac` given (anycast). Keep the
  gateway IP add. `name_for` unchanged.
- `nat.py` (new):
  - `ensure(cidr)` → `sysctl -w net.ipv4.ip_forward=1`; create chains if absent
    (`iptables -t nat -N NYC-POSTROUTING`, `iptables -N NYC-FORWARD`), jump from
    base chains once (`-C` guard), then in the nyc chains:
    `-t nat -A NYC-POSTROUTING -s <cidr> ! -d <cidr> -j MASQUERADE`,
    `-A NYC-FORWARD -s <cidr> -j ACCEPT`, `-A NYC-FORWARD -d <cidr> -j ACCEPT`.
    All `-C || -A` idempotent.
  - `delete(cidr)` → remove those rules (`-D`); leave chains (teardown removes).
- `allocate.py`: add `vni_for`, `anycast_mac` (or a sibling `overlay.py`). CIDR
  math already supports /16 unchanged.

### `client/lifecycle/vm_up.py` changes
- `VmSpec` gains: `node_host: str`, `peer_hosts: list[str]`.
- `_network(spec)` order becomes:
  1. `bridge.ensure(br, gateway_cidr(cidr), mac=anycast_mac(vpc_id))`
  2. `vxlan.ensure(vx, vni_for(vpc_id), spec.node_host, br)` then
     `vxlan.set_fdb(vx, spec.peer_hosts)`
  3. `nat.ensure(spec.cidr)`
  4. existing veth/netns/tap wiring.
- Skip VXLAN/FDB when `peer_hosts == []` or `node_host` is loopback (single
  host) — keeps stage green.

### `routers/vms.py` resolves DB-derived values (keeps client pure)
Before building `VmSpec`, compute from the `nodes` table:
`node_host = Nodes.get(this).host`,
`peer_hosts = [n.host for n in Nodes.get_all() if n.node_id != this and n.host not in (None,'127.0.0.1')]`.
Pass into `VmSpec`.

### Reconciler — overlay FDB sync (`reconciler/overlay_pass.py`, new)
Each pass: for each distinct `vpc_id` among local VMs (rows where
`node_id==self`), recompute `peer_hosts` from `nodes`, ensure `vxlan` exists,
`set_fdb`. Keeps membership correct as nodes join after VMs already exist.
Wire into `pass_once.run` alongside vms/volumes. Keep functions ≤12 lines.

### Internet — guest DNS
- `vm/config.py` `_boot_args`: append DNS to the kernel `ip=` arg →
  `ip=<gip>::<gw>:<mask>::eth0:off:<dns>` (dns default `1.1.1.1`, threaded
  through `VmConfig.dns`).
- Also **bake `/etc/resolv.conf`** (`nameserver <dns>`) into the rootfs at
  fetch time (belt-and-suspenders; some images ignore the kernel dns field).
  Add to `scripts/fetch_artifacts.sh` (or a new `scripts/inject_resolv.sh`)
  using the same offline `debugfs -w` write pattern as `inject_ssh_key.sh`.

### `privops_fake` additions (so fake suite parses the new argv)
- `sysctl` → no-op handler (record `STATE["sysctl"][key]=val` optional).
- `iptables` → record/remove rule tuples in `STATE["iptables"]`; support
  `-N`, `-C` (return success/fail via empty/raise — keep simple: `-C` returns ""
  meaning "exists" only if recorded, else the real `-C` would exit nonzero; for
  fake, model add/del and treat `-C` as "not present" → so `-C || -A` always
  adds once; acceptable).
- `bridge` → handle `fdb append|del|show` against `STATE["fdb"]`.
- `ip link add … type vxlan` already lands in the existing `_link` handler
  (records `kind="vxlan"`); `vxlan.exists` reads `STATE["links"]`.
Update `nyc/client/network/spec.md` and `client/spec.md` for the new topology.

---

## Part D — `spawn_vm`

Convenience API: auto data-volume + per-VM key, default VPC, random placement.

### `POST /vms/spawn`
Body: `{vm_name: str, ssh_key: str, size_mb?: int=1024, vcpu_count?: int=1, mem_mib?: int=512}`.
**No `vpc_id`, no `node_id` in the body.** Internal query param `pin` (not
public) carries the chosen node across the one proxy hop.

Handler (`routers/vms.py`):
1. If `pin` is None: pick `target = random.choice(Nodes.get_all()).node_id`.
   If `target != self`: `forward(... "POST", f"/vms/spawn?pin={target}", json=body)`; return.
2. (We are the target, or `pin==self`.) Resolve default VPC:
   `vpc = Vpcs.get(where={"name": "default"})` → 400 if missing.
3. Create a DB-tracked volume locally (reuse volume create logic): insert
   `volumes` row, `volume.create.run(path, size_mb)`.
4. Allocate IP from the VPC, insert `vms` row (status pending,
   `data_volume_id` = the new volume, `ssh_pubkey_path` = the injected key file).
5. `vm_up.run(spec)` with `ssh_pubkey=body.ssh_key`, `vcpu_count`, `mem_mib`.
6. status → running; return the row.

`random` import + `Query` param are the only router additions. The plain
`POST /vms` keeps explicit `node_id`/`vpc_id` for targeted placement.

### Per-VM key injection (rootfs copy + debugfs)
- `VmSpec` gains `ssh_pubkey: str | None`, `vcpu_count`, `mem_mib`.
- `client/env/setup.py`: if `ssh_pubkey` given → `cp --reflink=auto <shared
  rootfs> <vm_dir>/rootfs.ext4` (a real per-VM file, not a symlink) then
  `inject_key.run(vm_dir/rootfs.ext4, ssh_pubkey)`; else current symlink path.
  Split into helpers to stay ≤12 lines.
- `client/vm/inject_key.py` (new): `run(rootfs_path, pubkey_str)` — real mode
  runs the `debugfs -w` dance factored out of `scripts/inject_ssh_key.sh`
  (write authorized_keys, set modes/uid/gid, PermitRootLogin). Fake mode: no-op.
  Does NOT need sudo (just file write perms) — runs `debugfs` directly, not via
  privops. Guard on `privops.backend()=="real"`.
- Firecracker drive for the copied rootfs stays `is_read_only: true` (injection
  happens on the host file before boot).
- Trade-off (documented): spawned VMs do not share the rootfs (full copy,
  ~300 MB unless the FS supports reflink). Future: overlayfs / MMDS+agent.

### Config plumbing
`VmConfig` gains `vcpu_count`, `mem_mib`, `dns`; `vm_up._spawn` passes them.

---

## Part E — VM lifecycle (stop / start / reboot)

Semantics: **stop** keeps everything except the firecracker process (netns,
tap, veth, bridge, volume, IP, DB row preserved) so **start** is a cheap
respawn from the on-disk `config.json`. **reboot** = stop+start.

- `client/lifecycle/vm_stop.py` (new): `run(vms_dir, vm_id)` → `kill.run(paths)`
  only. status → `stopped`.
- `client/lifecycle/vm_start.py` (new): `run(vms_dir, vm_id, ns, firecracker_bin)`
  → `create.run(paths, vm_id, ns, bin)` + `boot.run(paths)` (config.json already
  on disk). status → `running`. `ns = f"vm-{vm_id[:8]}"`; `firecracker_bin` from
  `config.resolve()`.
- `routers/vms.py`: `POST /vms/{id}/stop|start|reboot`, each proxied to the
  owning node (same owner lookup as DELETE), flips `vms.status`.
- Reconciler note: a `stopped` row keeps its dir, so it is **not** an orphan;
  the reconciler must not tear it down (it only kills dirs with no DB row —
  already true). It still does not auto-restart `running`-but-dead VMs (existing
  documented limitation).
- Tests: `test_vms_crud.py` add stop→stopped, start→running, reboot, and a
  proxied-lifecycle case under fake backend.

---

## Part F — deploy (`nyc/scripts/`)

**Hybrid:** stdlib-only **Python orchestrator** `deploy.py` (no deps; `tomllib`,
`subprocess`, `argparse`, `concurrent.futures`) that uploads + runs **bash**
`provision.sh` / `teardown.sh` on each node over `ssh -A
-o StrictHostKeyChecking=accept-new`. Commands: `up`, `down`, `status`.

### Inventory `cluster.toml`
```toml
[cluster]
ssh_user   = "ubuntu"
ref        = "main"                                  # equator git ref to deploy
repo_url   = "git@github.com:the-bokya/equator.git"
remote_dir = "~/equator"
vpc_cidr   = "172.16.0.0/16"                          # default VPC; must not overlap underlay
dns        = "1.1.1.1"
http_port  = 8000
rqlite_http_port = 4001
rqlite_raft_port = 4002

[[nodes]]
name = "n1"; host = "10.0.0.11"; public_host = "203.0.113.1"; domain = "n1.example.com"; bootstrap = true
[[nodes]]
name = "n2"; host = "10.0.0.12"; public_host = "203.0.113.2"; domain = "n2.example.com"
```
`host` = private/underlay IP (raft + proxy + VXLAN). Exactly one `bootstrap`.

### Node folder
`<remote_dir>/nyc/node` (inside the checkout, so `config.resolve()` finds
`assets/`+`bin/` and `dadar` discovers `nyc.app` by walking up). One per machine.

### `up` (orchestrator: validate inventory + no-CIDR-overlap; bootstrap node first, then joiners; per node:)
`provision.sh` steps (idempotent, `sudo -n`):
1. preflight: `[ -e /dev/kvm ]`, arch `x86_64`.
2. `apt-get install -y git curl e2fsprogs iproute2 iptables ca-certificates`;
   install `uv` if missing; install Caddy (official apt repo).
3. `git clone --recurse-submodules <repo_url> <remote_dir>` (or `git -C … fetch
   && checkout <ref> && submodule update --init --recursive`). Uses forwarded
   agent.
4. `uv sync` in `dadar` and `nyc`.
5. `scripts/install_firecracker.sh`, `dadar/scripts/install_rqlite.sh`.
6. `scripts/fetch_artifacts.sh`; receive the **shared VM keypair** pushed by the
   orchestrator into `nyc/assets/`; inject pubkey + resolv.conf into rootfs.
7. snapshot `net.ipv4.ip_forward` → `<node_folder>/.pre_ip_forward`; write
   `/etc/sysctl.d/99-nyc.conf` (`net.ipv4.ip_forward=1`) + `sysctl --system`.
8. write `/etc/sudoers.d/nyc` (validate with `visudo -cf`):
   `<user> ALL=(root) NOPASSWD: /usr/sbin/ip,/usr/bin/ip,/usr/sbin/iptables,/usr/sbin/sysctl,/usr/sbin/bridge,/sbin/mkfs.ext4,/usr/bin/mount,/usr/bin/umount,/usr/bin/truncate,/usr/bin/kill,<remote_dir>/nyc/bin/firecracker`
   (adjust binary paths to the distro; `ip` covers firecracker since it is
   launched via `ip netns exec`).
9. `cd <node_folder> && uv run --project <remote_dir>/nyc dadar init --host <host>
   --public-host <public_host> --domain <domain> --http-port … --rqlite-http-port …
   --rqlite-raft-port …`.
10. install + start systemd unit `nyc-node.service` (below) with
    `--bootstrap` (bootstrap node) or `--join <bootstrap_host>:<raft_port>`.
11. install + start `nyc-caddy.service` (or use system Caddy) with the generated
    Caddyfile (below).
Orchestrator then: wait `/health` on each node, create the **single default
VPC** (`POST /vpcs {name:"default", cidr:vpc_cidr}` — idempotent: skip if a
`default` exists), run a smoke check (`spawn_vm`, assert running + visible on
another node, then delete).

### systemd unit (`nyc-node.service`, templated per node)
```ini
[Unit]
Description=nyc dadar node
After=network-online.target
Wants=network-online.target
[Service]
User=<ssh_user>
WorkingDirectory=<remote_dir>/nyc/node
ExecStart=/usr/bin/env uv run --project <remote_dir>/nyc dadar run <--bootstrap|--join HOST:RAFT>
Restart=on-failure
Environment=NYC_BACKEND=real
[Install]
WantedBy=multi-user.target
```

### Caddyfile (per node → automatic HTTPS)
```
<domain> {
    reverse_proxy <host>:<http_port>
}
```
External clients hit `https://<domain>/vms …`; raft + `_proxy.forward` stay on
the private network. uvicorn binds `<host>` (private), never a public plaintext
port.

### `down` (reverse + idempotent; orchestrator deletes VMs via API first if the
cluster is reachable, then per node `teardown.sh`):
1. `systemctl stop/disable nyc-node.service nyc-caddy.service`; rm units;
   `systemctl daemon-reload`.
2. purge kernel state by the **anchored regexes** (0.3): `ip netns del` each
   `vm-…`; `ip link del` each `br-…`, `vx-…`, `vmh-…`/`vmn-…`.
3. iptables: delete jumps to `NYC-*`, `-F` then `-X` both chains (nat+filter).
4. restore `net.ipv4.ip_forward` from `.pre_ip_forward`; rm
   `/etc/sysctl.d/99-nyc.conf`; `sysctl --system`.
5. `rm -rf <node_folder>` (wipes `vms/`, `volumes/`, `rqlite-data/`, `.node_id`).
6. rm `/etc/sudoers.d/nyc`, the Caddyfile.
7. `--purge` additionally: `apt-get remove` only packages we installed (tracked
   via a `dpkg -l` snapshot diff written on `up`), and `rm -rf <remote_dir>`.
**Reversibility:** default `down` returns the host to its pre-setup config and
leaves base packages; `up` after `down` rebuilds cleanly (every step is
check-then-act).

### SSH jump-server into a VM (deliverable)
The VPC bridge + gateway live in the node **root ns** and are L2-bridged to the
guest (locally and across nodes via VXLAN), so a guest's VPC IP is reachable
from the hosting node. Resolve the host from the DB
(`vms.node_id → nodes.domain`) and:
```
ssh -J <ssh_user>@<node_domain> -i <shared_vm_key> root@<vm_vpc_ip>
```
No blocker. The shared VM private key is generated on the control machine and
distributed to nodes' `assets/`; `spawn_vm` keys are layered per VM.

### Parameterize the e2e test for remote nodes
`tests/test_stage_e2e.py`: accept explicit base URLs via env
(`NYC_E2E_URLS="http://10.0.0.11:8000,http://10.0.0.12:8000"`) falling back to
the current `NYC_STAGE_BASE_PORT` sequential-port scheme. `deploy up` runs it
where it can reach the nodes (control machine on the private net, else over ssh
on the bootstrap node).

---

## Verification plan
- **Local (no hardware):** full fake unit suite (`uv run pytest`), single-host
  `scripts/stage.sh 3` (fake) and `scripts/stage.sh 3 --real --keep` if
  `/dev/kvm` + sudo available.
- **Bare metal (needs node SSH access):** `deploy up cluster.toml` → assert N
  nodes in `/nodes`; VM-to-VM ping across nodes in the default VPC; `curl
  https://<domain>/vms`; VM `ping 1.1.1.1` + DNS resolve (internet);
  `ssh -J …` into a VM; stop/start/reboot via API; `deploy down` →
  host returns to pre-setup state; `deploy up` again succeeds (idempotent).

## Execution order (commit/push cadence — git-clone delivery needs pushed refs)
1. **[DONE] Part A (dadar)** → review + push dadar.
2. Part B proxy + `privops_fake` handlers → fake suite green.
3. Part C overlay + NAT + DNS → stage 3 (fake) green + new unit tests.
4. Part D spawn_vm + per-VM key.
5. Part E lifecycle endpoints + tests.
6. Part F `deploy.py`/`provision.sh`/`teardown.sh` + e2e parameterization +
   `scripts/spec.md`.
7. Update all touched `spec.md`/README → push nyc → bump+push equator submodule
   pointers. **Then** `deploy up` can pull the new code.
