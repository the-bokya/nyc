import shutil
from pathlib import Path


def run(vm_dir: Path) -> None:
    if vm_dir.exists():
        shutil.rmtree(vm_dir, ignore_errors=True)


def list_dirs(vms_dir: Path) -> list[Path]:
    if not vms_dir.exists():
        return []
    return [p for p in vms_dir.iterdir() if p.is_dir()]
