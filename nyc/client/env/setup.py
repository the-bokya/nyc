"""Per-VM on-disk setup: a writable rootfs as a thin clone of a golden image,
plus symlinked kernel + ssh key.

The rootfs is a thin CoW *clone* of the golden LV — VMs share the golden's
blocks until they write, so there is no per-VM full copy. The golden stays
read-only and is shared by every VM cloned from it. `vm.inject` then bakes
per-VM config (ssh key, DNS, fstab) into the clone via debugfs before boot.
The LV device node is symlinked in under the fixed `rootfs.ext4` name so the
firecracker config and `VmPaths` are unchanged.
"""
from pathlib import Path

from nyc.client.env.paths import VmPaths, for_vm
from nyc.client.volume import lv, names


def run(vms_dir: Path, vm_id: str, assets: dict, vg: str, rootfs_origin: str) -> VmPaths:
    paths = for_vm(vms_dir, vm_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    lv.remove(vg, names.rootfs(vm_id))  # idempotent: clear a stale clone before re-cloning
    dev = lv.clone(vg, rootfs_origin, names.rootfs(vm_id))
    _symlink(paths.rootfs, Path(dev))
    _symlink(paths.kernel, assets["kernel"].resolve())
    _symlink(paths.ssh_key, assets["ssh_key"].resolve())
    return paths


def _symlink(target: Path, source: Path) -> None:
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(source)
