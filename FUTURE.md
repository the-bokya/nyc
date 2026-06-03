# nyc — FUTURE

Known gaps and roadmap, ordered by how much each weakens a current invariant.
Each item: what's missing, why it matters, where it lives in code. Things
deliberately *not* done (and why) stay in the relevant `spec.md`.

## Close the reconciler loop (intent is not enforced, only excess is pruned)

`reconciler/` is one-directional: it tears down resources with no DB row
(`vms_pass`, `volumes_pass`) but never the reverse. Two holes:

- **Recreate missing.** A DB row whose backing VM/volume vanished is never
  rebuilt (`reconciler/pass_once.py` has no recreate step — punted, see
  `reconciler/spec.md`). The DB has the original ip/vpc/volume to re-run `vm_up`.
- **Restart dead-but-`running`.** A VM whose firecracker died (e.g. host reboot)
  stays `status=running` forever; nothing respawns it. `live_status`
  (`routers/vms.py:_with_status`) *observes* the divergence on read but no pass
  acts on it, so intent and reality drift and a node reboot loses every VM.

Fix: a pass that, per local row, ensures the VM is up (respawn via
`lifecycle/vm_start`) and reconciles `status` to the observed `live_status`.

## Bring-up failure leaves a wedged row + leaked kernel state

`routers/vms.py:_create_local` / `_spawn_local` insert the row (`pending`) then
call `_bring_up` → `vm_up.run` with **no rollback**. If `vm_up` raises partway
(netns made, veth fails), the row stays `pending`, the half-built netns/veth/
bridge leak, and the reconciler won't reap them (the dir *has* a row, so it is
not an orphan). Fix: wrap bring-up in a compensating `vm_down` + set
`status=failed` on failure. Pairs with the recreate loop above.

## IP allocation is racy

`pick_ip(cidr, used)` (`client/network/allocate.py`) reads the used set then the
router inserts — with no `UNIQUE(vpc_id, ip)` constraint (`tables/vms.py` has
none), so two concurrent creates in one VPC can pick the same IP. Fix: add the
composite unique index and retry `pick_ip` on conflict (cf.
`defaults.ensure_default_vpc`'s race-safe insert).

## Interface-name truncation can collide across VPCs

`bridge.name_for` / `vxlan.name_for` use only the first **4 hex chars** of the
node and vpc UUIDs (`br-<node[:4]>-<vpc[:4]>`, 16 bits/field) to fit IFNAMSIZ=15.
Two VPCs whose ids share a 4-hex prefix on the same node collapse onto one
bridge → silent cross-VPC L2 bleed (~256 VPCs/node for a coin-flip collision).
`vni_for` has the same family of risk in a larger space. VM-level names use 8
hex chars (safe). Fix: derive names from a short hash of the full id, or carry a
small dense per-(node,vpc) index in the DB.

## `spawn` auto-volume is orphaned on delete

`DELETE /vms` (`routers/vms.py:_delete_local`) does not cascade to the auto data
volume created by `/vms/spawn` (open question, `routers/spec.md`). The volume
row+file leak and `volumes_pass` won't reap them (they have a row). Decide:
cascade when `data_volume_id` points at an auto volume, or track ownership
explicitly and let TTL/delete clean it.

## Placement is random and unaware

`_random_node` (`routers/vms.py`) picks any *registered* node uniformly — no
check that it is alive, nor any capacity/VM-count signal. It can place on a dead
or full node. Fix: filter by recent liveness and bias by current VM/volume load.

## Per-VM rootfs: stop full-copying

Every VM gets a full CoW copy of the rootfs (`client/env/setup.py`), ~300 MB on
filesystems without reflink. Replace with an overlayfs upper layer over the
shared read-only base, or keep the base read-only and inject per-VM config via
MMDS + a tiny guest agent instead of offline `debugfs` (`client/vm/inject.py`).

## Volumes → LVM

A volume is a plain ext4 file today (`client/volume/`). LVM logical volumes
would give real allocation, resize, and snapshots. See `client/volume/spec.md`.

## VXLAN ARP/ND suppression

The anycast gateway is correct but a "who has gateway?" ARP still floods to
every peer (`NETWORKING.md` §4). `bridge link set dev <vx> neigh_suppress on`
plus per-VM neighbor entries keep that ARP off the tunnel. Not needed for
correctness at small scale.

## overlay-check: test the live datapath

`deploy.py overlay-check` (`scripts/spec.md`) audits kernel *state* only.
Cross-node UDP/4789 reachability and VM↔VM ping stay manual. Add an active probe
(send a frame / ping between two peer VMs and assert delivery).

## API authn/authz

The REST API (`routers/`) has no authentication — any client that can reach a
node can spawn/delete VMs (Caddy gives TLS, not identity). Add an auth dependency
(shared token at minimum) before any untrusted-network exposure.

## `GET /vms` fan-out scaling

Listing fans out one synchronous httpx call per owning node (30 s timeout),
swallowing a failed owner to `[]` so its VMs vanish from the merged view
(`routers/vms.py:_merge_remote_status`). Fix: parallelize, and surface
last-known DB status instead of dropping an unreachable owner's rows.
