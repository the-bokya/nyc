# client/env

Per-VM on-disk layout. Decides *where* a VM's files live and sets them up
before firecracker is ever spawned. No networking, no process management.

## Filesystem contract

A VM directory is `<vms_dir>/<vm_id>/`. Its members are defined once, in
`paths.py`, by the frozen `VmPaths` dataclass — every other module asks
`VmPaths` for a path rather than re-joining strings:

```
<vms_dir>/<vm_id>/
├── rootfs.ext4   -> symlink to the per-VM rootfs LV device (a thin clone of a golden)
├── vmlinux       -> symlink to assets/vmlinux
├── id_ed25519    -> symlink to assets/id_ed25519 (ssh private key)
├── id_ed25519.pub
├── config.json   (firecracker config — written by vm.config.build)
├── api.sock      (firecracker API socket — created on boot)
├── data.ext4     -> symlink to a data volume LV device, if one is attached
├── pid           (firecracker pid — written on boot)
├── log.fifo      (firecracker log fifo)
└── firecracker.log
```

`paths.for_vm(vms_dir, vm_id) -> VmPaths` is the only constructor. (`rootfs.ext4`
/`data.ext4` are now symlinks to block devices, not files — the names are kept
so the firecracker config and `VmPaths` are unchanged from the file era.)

## Actions

| File | Public fn | Does |
|---|---|---|
| `paths.py` | `for_vm(vms_dir, vm_id)` | Build a `VmPaths` for a VM. |
| `setup.py` | `run(vms_dir, vm_id, assets, vg, rootfs_origin)` | `mkdir` the VM dir, thin-**clone** `rootfs_origin` (a golden LV) into the per-VM `rootfs-<vm_id>` LV, symlink its device + kernel + ssh-key in, return `VmPaths`. |
| `teardown.py` | `run(vm_dir, vg)` | `rmtree` the VM dir **and** `lvremove` the `rootfs-<vm_id>` clone (both idempotent). Also exposes `list_dirs(vms_dir)`. |

`assets` is a dict with keys `kernel`, `ssh_key` mapping to source `Path`s
(symlinked in, shared read-only). The rootfs no longer comes from `assets` — it
is a thin CoW **clone of a golden image LV** (`rootfs_origin`, default
`gold-default`), so there is no per-VM full copy and the golden's blocks are
shared until the VM writes. `vm.inject` then bakes per-VM config (ssh key, DNS,
fstab) into the clone offline before boot. `setup` is idempotent: it removes any
stale `rootfs-<vm_id>` LV before re-cloning. On `fake` the LVM ops are recorded
in `STATE`, not performed, so the rest of the path is unchanged.
