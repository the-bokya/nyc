"""LV name <-> role mapping.

Every thin LV in a node's VG carries a role prefix so the reconciler can tell
data volumes, snapshots, goldens, and per-VM rootfs overlays apart within one
VG. The default golden and its base seed use fixed names (see `pool.py`).
"""
DATA = "data-"
SNAP = "snap-"
GOLD = "gold-"
ROOTFS = "rootfs-"


def data(vol_id: str) -> str:
    return f"{DATA}{vol_id}"


def snap(snap_id: str) -> str:
    return f"{SNAP}{snap_id}"


def gold(snap_id: str) -> str:
    return f"{GOLD}{snap_id}"


def rootfs(vm_id: str) -> str:
    return f"{ROOTFS}{vm_id}"
