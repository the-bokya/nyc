"""One method to set up everything a VM needs on disk.

The rootfs is always a per-VM CoW copy (--reflink=auto on btrfs/xfs,
full copy on ext4). Every VM gets a writable root so apt, cloud-init,
and first-boot work can run without touching the shared base image.
"""
from pathlib import Path

from nyc.client import privops
from nyc.client.env.paths import VmPaths, for_vm


def run(vms_dir: Path, vm_id: str, assets: dict[str, Path]) -> VmPaths:
    paths = for_vm(vms_dir, vm_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    _copy(paths.rootfs, assets["rootfs"])
    _link(paths.kernel,  assets["kernel"])
    _link(paths.ssh_key, assets["ssh_key"])
    return paths


def _link(target: Path, source: Path) -> None:
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(source.resolve())


def _copy(target: Path, source: Path) -> None:
    if target.exists() or target.is_symlink():
        target.unlink()
    privops.run(["cp", "--reflink=auto", str(source.resolve()), str(target)])
