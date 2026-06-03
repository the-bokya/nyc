import shutil
from pathlib import Path

from nyc.client.volume import lv, names


def run(vm_dir: Path, vg: str) -> None:
    if vm_dir.exists():
        shutil.rmtree(vm_dir, ignore_errors=True)
    lv.remove(vg, names.rootfs(vm_dir.name))  # the per-VM rootfs clone (idempotent)


def list_dirs(vms_dir: Path) -> list[Path]:
    if not vms_dir.exists():
        return []
    return [p for p in vms_dir.iterdir() if p.is_dir()]
