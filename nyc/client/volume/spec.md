# client/volume

All VM-backing storage is **LVM thin volumes** in a per-node volume group's
thin pool. A data volume, a snapshot, a golden image, and a per-VM rootfs are
all thin LVs distinguished by a name-prefix role (`names.py`). The device node
`/dev/<vg>/<lv>` is what firecracker opens — block devices work as drives, so
`attach` symlinks the device under the fixed `data.ext4`/`rootfs.ext4` names.

Thin snapshots are **independent peers**: a snapshot/clone can be removed
without affecting its origin, and an origin without affecting its snapshots
(the pool refcounts shared blocks). So a golden can be deleted while VMs boot
off clones of it, and "delete the snapshot tomorrow" is always safe — no full
copy is ever needed for independence.

## Modules

| File | Public fns | Does |
|---|---|---|
| `lv.py` | `create_thin`, `snapshot`, `clone`, `remove`, `extend`, `format_ext4`, `exists`, `list_lvs`, `vg_exists`, `device_path` | Low-level LVM primitives, all via `privops`. `list_lvs` parses `lvs --reportformat json`. |
| `pool.py` | `ensure(node_id, cfg, rootfs_src) -> vg` | Idempotent node substrate: PV (device or loopback) → VG → thin pool → default golden. Run at startup. |
| `names.py` | `data`, `snap`, `gold`, `rootfs` + `DATA/SNAP/GOLD/ROOTFS` prefixes | LV name ↔ role mapping within one VG. |
| `create.py` | `run(vg, pool, name, size_mb)`, `from_snapshot(vg, snap_lv, name)` | New ext4 thin LV, or a writable clone of a snapshot. |
| `delete.py` | `run(vg, name)` | `lvremove -f` (idempotent). |
| `attach.py` | `run(vm_dir, device)`, `detach(vm_dir)` | Symlink the LV device into `vm_dir/data.ext4`. |
| `snapshot.py` | `create(vg, vol_id, snap_id)`, `golden(vg, snap_id, gold_id)`, `remove(vg, lv)` | Read-only snapshot of a volume; read-only golden derived from a snapshot. |

## pool.ensure — the substrate

`PV → VG → thin pool → default golden`, idempotent and safe to re-run (also the
self-heal-after-reboot path: it `vgchange -ay`s the VG):

- **PV**: `cfg.device` (prod, the one block device nyc owns) or a loopback over
  a sparse file in the node folder (`cfg.loop_file`, dev/staging). nyc
  `pvcreate`/`vgcreate`s only when the VG is absent and the device is empty (or
  `NYC_LVM_FORCE=1`), so it never silently wipes a populated disk.
- **VG name**: `cfg.vg` in prod; `<vg>-<node_id[:8]>` in loopback mode, so
  several staged nodes on one host get isolated VGs (their reconcilers never
  prune each other's LVs). `LvmConfig.vg_for(node_id)` / `config.volume_vg`.
- **default golden** (`gold-default`): a thin LV `base-rootfs` is created,
  `dd`-loaded from `assets/rootfs.ext4`, then snapshotted read-only. Spawns
  with no `image` clone this; `gold-default`/`base-rootfs` are reserved names
  the snapshots reconciler never prunes.

## Why the symlink (`attach`)

Firecracker's config references `vm_dir/data.ext4` (`vm/config.py` `_data_drive`)
and `vm_dir/rootfs.ext4`. Symlinking the LV's **stable** device node
(`/dev/<vg>/<lv>`, not the resolved `/dev/dm-N`) under those fixed names
decouples where the LV lives from where firecracker looks, and keeps `VmPaths`
and the JSON config unchanged from the file era. `attach` is idempotent.

## Backend notes

Every LVM op goes through `privops`. On `real` they `sudo` (the LV device nodes
are root-owned, so `mkfs.ext4`/`debugfs`/`dd` against them sudo too — only the
loopback-backing `truncate` stays unprivileged). On `fake`, `privops_fake`
keeps an in-memory LVM model (`STATE["lvm"]`: loops/pvs/vgs/lvs) and answers
`lvs`/`vgs`/`pvs` with the same JSON envelope real LVM emits, so the client's
parser is identical on both backends.
