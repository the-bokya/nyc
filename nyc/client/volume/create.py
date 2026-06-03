"""Create a per-VM data volume as a thin LV, formatted ext4.

The volume is a thin LV in the node's pool (real allocation grows on write).
The device node `/dev/<vg>/<name>` is returned and stored as the volume's path;
`attach` symlinks it into the VM dir and firecracker opens it as a block drive.
"""
from nyc.client.volume import lv


def run(vg: str, pool: str, name: str, size_mb: int) -> str:
    dev = lv.create_thin(vg, pool, name, size_mb)
    lv.format_ext4(dev)
    return dev


def from_snapshot(vg: str, snapshot_lv: str, name: str) -> str:
    """Clone a writable data volume from a snapshot (already carries a filesystem)."""
    return lv.clone(vg, snapshot_lv, name)
