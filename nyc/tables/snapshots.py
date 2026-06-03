from dadar.orm import ORM


class Snapshots(ORM):
    """Read-only thin images, node-bound. Two independent axes:
    `role` — `snapshot` (point-in-time freeze) vs `golden` (bootable/cloneable image);
    `disk` — `root` (a VM rootfs lineage, the only thing valid as a boot image) vs
    `data` (a data volume). `parent` is the id this was derived from — a volume or
    a VM (for snapshots), a snapshot (for goldens); null for the default golden.
    `lv_name` is the backing LV. Status/role/disk enforced by the router, not SQL."""
    name = "snapshots"
    fields = {
        "id":         "TEXT PRIMARY KEY",
        "node_id":    "TEXT NOT NULL",
        "name":       "TEXT NOT NULL",
        "role":       "TEXT NOT NULL DEFAULT 'snapshot'",
        "disk":       "TEXT NOT NULL DEFAULT 'data'",
        "parent":     "TEXT",
        "lv_name":    "TEXT NOT NULL",
        "size_mb":    "INTEGER NOT NULL DEFAULT 0",
        "created_at": "TEXT NOT NULL",
    }
