"""Low-level LVM thin-volume primitives. One small verb each, all via privops.

Every volume / snapshot / rootfs overlay is a thin LV in a per-node VG's thin
pool. The LV name carries a role prefix (`data-`, `snap-`, `gold-`, `rootfs-`)
so the reconciler can tell them apart within one VG. The device node
`/dev/<vg>/<lv>` is what firecracker opens — block devices work as drives.

Thin snapshots are independent peers: a snapshot/clone can be removed without
affecting its origin, and an origin without affecting its snapshots (the pool
refcounts shared blocks). So `clone` is the CoW overlay primitive, and deleting
a golden never breaks the VMs booted from clones of it.
"""
import json

from nyc.client import privops

_LVS_COLS = "lv_name,vg_name,lv_size,pool_lv,origin,lv_attr"


def device_path(vg: str, name: str) -> str:
    return f"/dev/{vg}/{name}"


def create_thin(vg: str, pool: str, name: str, size_mb: int) -> str:
    privops.run(["lvcreate", "-T", f"{vg}/{pool}", "-V", f"{size_mb}m", "-n", name])
    return device_path(vg, name)


def snapshot(vg: str, origin: str, name: str, readonly: bool = True) -> str:
    """Read-only thin snapshot (a point-in-time image). `-kn` clears activation skip."""
    argv = ["lvcreate", "-s", "-kn", "-n", name, f"{vg}/{origin}"]
    privops.run(argv + (["--permission", "r"] if readonly else []))
    return device_path(vg, name)


def clone(vg: str, origin: str, name: str) -> str:
    """Writable thin snapshot — the CoW overlay for a rootfs or a from-snapshot volume."""
    privops.run(["lvcreate", "-s", "-kn", "-n", name, f"{vg}/{origin}"])
    return device_path(vg, name)


def remove(vg: str, name: str) -> None:
    if exists(vg, name):
        privops.run(["lvremove", "-f", f"{vg}/{name}"])


def extend(vg: str, name: str, size_mb: int) -> None:
    privops.run(["lvextend", "-L", f"{size_mb}m", f"{vg}/{name}"])
    privops.run(["resize2fs", device_path(vg, name)])


def format_ext4(device: str) -> None:
    privops.run(["mkfs.ext4", "-F", device])


def exists(vg: str, name: str) -> bool:
    return any(lv["lv_name"] == name for lv in list_lvs(vg))


def list_lvs(vg: str) -> list[dict]:
    try:
        out = privops.run(["lvs", "--reportformat", "json", "--units", "m",
                           "--nosuffix", "-o", _LVS_COLS, vg])
    except privops.PrivopsError:
        return []
    return json.loads(out)["report"][0]["lv"] if out else []


def vg_exists(vg: str) -> bool:
    try:
        out = privops.run(["vgs", "--reportformat", "json", "-o", "vg_name", vg])
    except privops.PrivopsError:
        return False
    return bool(out) and bool(json.loads(out)["report"][0]["vg"])
