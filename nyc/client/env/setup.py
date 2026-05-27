"""One method to set up everything a VM needs on disk."""
from pathlib import Path

from nyc.client.env.paths import VmPaths, for_vm


def run(vms_dir: Path, vm_id: str, assets: dict[str, Path]) -> VmPaths:
    paths = for_vm(vms_dir, vm_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    _link(paths.rootfs,  assets["rootfs"])
    _link(paths.kernel,  assets["kernel"])
    _link(paths.ssh_key, assets["ssh_key"])
    return paths


def _link(target: Path, source: Path) -> None:
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(source.resolve())
