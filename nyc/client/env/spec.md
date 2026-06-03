# client/env

Per-VM on-disk layout. Decides *where* a VM's files live and sets them up
before firecracker is ever spawned. No networking, no process management.

## Filesystem contract

A VM directory is `<vms_dir>/<vm_id>/`. Its members are defined once, in
`paths.py`, by the frozen `VmPaths` dataclass — every other module asks
`VmPaths` for a path rather than re-joining strings:

```
<vms_dir>/<vm_id>/
├── rootfs.ext4   per-VM CoW copy of assets/rootfs.ext4 (writable)
├── vmlinux       -> symlink to assets/vmlinux
├── id_ed25519    -> symlink to assets/id_ed25519 (ssh private key)
├── id_ed25519.pub
├── config.json   (firecracker config — written by vm.config.build)
├── api.sock      (firecracker API socket — created on boot)
├── data.ext4     -> symlink to a volume file, if one is attached
├── pid           (firecracker pid — written on boot)
├── log.fifo      (firecracker log fifo)
└── firecracker.log
```

`paths.for_vm(vms_dir, vm_id) -> VmPaths` is the only constructor.

## Actions

| File | Public fn | Does |
|---|---|---|
| `paths.py` | `for_vm(vms_dir, vm_id)` | Build a `VmPaths` for a VM. |
| `setup.py` | `run(vms_dir, vm_id, assets)` | `mkdir` the VM dir, copy rootfs + symlink kernel/ssh-key from `assets`, return `VmPaths`. |
| `teardown.py` | `run(vm_dir)` | `rmtree` the VM dir (idempotent). Also exposes `list_dirs(vms_dir)`. |

`assets` is a dict with keys `rootfs`, `kernel`, `ssh_key` mapping to source
`Path`s (populated by `fetch_artifacts.sh` into `assets/`). `setup._link`
replaces any pre-existing target so re-running `setup` is idempotent.

The rootfs is **always** a per-VM copy via `privops` (`cp --reflink=auto`) — a
CoW clone where the fs supports it, a full copy otherwise. Every VM gets a
writable root so `vm.inject` can bake per-VM config (ssh key, DNS, fstab) into
it offline before boot. The kernel and ssh key are symlinked (shared,
read-only). On `fake` the copy is recorded, not performed (tests have no real
image), so the rest of the path is unchanged.
