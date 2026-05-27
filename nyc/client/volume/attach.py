from pathlib import Path


def run(vm_dir: Path, volume_path: Path) -> Path:
    """Symlink the data volume into the VM's directory as data.ext4.

    The firecracker config references vm_dir/data.ext4. Decoupling the on-disk
    location of volumes from where firecracker expects them keeps volume
    storage organized independently of VM lifecycles.
    """
    target = vm_dir / "data.ext4"
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(volume_path)
    return target


def detach(vm_dir: Path) -> None:
    target = vm_dir / "data.ext4"
    if target.exists() or target.is_symlink():
        target.unlink()
