"""One method to set up everything a VM needs on disk.

The rootfs is normally a symlink to the shared read-only image. `copy_rootfs`
instead makes the VM its **own** writable copy — needed when something must be
baked into that one VM's rootfs (e.g. a per-VM ssh key via `vm.inject_key`).
"""
from pathlib import Path

from nyc.client import privops
from nyc.client.env.paths import VmPaths, for_vm


def run(vms_dir: Path, vm_id: str, assets: dict[str, Path],
        copy_rootfs: bool = False) -> VmPaths:
    paths = for_vm(vms_dir, vm_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    _copy(paths.rootfs, assets["rootfs"]) if copy_rootfs else _link(paths.rootfs, assets["rootfs"])
    _link(paths.kernel,  assets["kernel"])
    _link(paths.ssh_key, assets["ssh_key"])
    return paths


def _link(target: Path, source: Path) -> None:
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(source.resolve())


def _copy(target: Path, source: Path) -> None:
    # Through privops so `fake` records intent (no real image on disk in tests)
    # and `real` does a CoW clone where the fs supports it, else a full copy.
    if target.exists() or target.is_symlink():
        target.unlink()
    privops.run(["cp", "--reflink=auto", str(source.resolve()), str(target)])
