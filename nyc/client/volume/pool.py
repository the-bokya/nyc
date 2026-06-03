"""Idempotent per-node LVM substrate, run at node startup (app.on_startup) and
safe to re-run (also the self-heal-after-reboot path).

    PV (block device, or loopback over a sparse file)
      -> VG  (per machine in prod; per node in loopback staging)
        -> thin pool
          -> default golden image  (base rootfs imported from assets, snapshot RO)

nyc owns the configured block device: it pvcreate/vgcreates only when the VG is
absent and the device has no signature (or NYC_LVM_FORCE=1). Loopback mode needs
no device and never touches real disks beyond a file in the node folder.
"""
import json
import os
from pathlib import Path

from nyc.client import privops
from nyc.client.volume import lv
from nyc.config import LvmConfig

GOLD_DEFAULT = "gold-default"
BASE_ROOTFS = "base-rootfs"


def ensure(node_id: str, cfg: LvmConfig, rootfs_src: Path) -> str:
    """Bring the substrate up to spec and return the effective VG name."""
    pv = _ensure_pv(cfg)
    vg = cfg.vg_for(node_id)
    if not lv.vg_exists(vg):
        privops.run(["vgcreate", vg, pv])
    _ensure_pool(vg, cfg.thinpool)
    privops.run(["vgchange", "-ay", vg])
    _ensure_default_golden(vg, cfg, rootfs_src)
    return vg


def _ensure_pv(cfg: LvmConfig) -> str:
    if cfg.device:
        _ensure_pvcreate(cfg.device)
        return cfg.device
    return _ensure_loopback(cfg)


def _ensure_loopback(cfg: LvmConfig) -> str:
    f = cfg.loop_file
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        privops.run(["truncate", "-s", f"{cfg.loop_size_gb}G", str(f)])
    dev = _loop_dev(f)
    _ensure_pvcreate(dev)
    return dev


def _loop_dev(f: Path) -> str:
    out = privops.run(["losetup", "-j", str(f)]).strip()
    if out:
        return out.split(":", 1)[0].strip()
    return privops.run(["losetup", "--find", "--show", str(f)]).strip()


def _ensure_pvcreate(dev: str) -> None:
    if _pv_exists(dev):
        return
    force = os.environ.get("NYC_LVM_FORCE") == "1"
    privops.run(["pvcreate", "-ff", "-y", dev] if force else ["pvcreate", dev])


def _ensure_pool(vg: str, pool: str) -> None:
    if not lv.exists(vg, pool):
        privops.run(["lvcreate", "--type", "thin-pool", "-l", "100%FREE", "-n", pool, vg])


def _ensure_default_golden(vg: str, cfg: LvmConfig, rootfs_src: Path) -> None:
    if lv.exists(vg, GOLD_DEFAULT):
        return
    lv.create_thin(vg, cfg.thinpool, BASE_ROOTFS, cfg.base_rootfs_mb)
    privops.run(["dd", f"if={rootfs_src}", f"of={lv.device_path(vg, BASE_ROOTFS)}",
                 "bs=4M", "conv=fsync"])
    lv.snapshot(vg, BASE_ROOTFS, GOLD_DEFAULT, readonly=True)


def _pv_exists(dev: str) -> bool:
    try:
        out = privops.run(["pvs", "--reportformat", "json", "-o", "pv_name", dev])
    except privops.PrivopsError:
        return False
    return bool(out) and bool(json.loads(out)["report"][0]["pv"])
