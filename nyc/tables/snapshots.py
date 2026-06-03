from dadar.orm import ORM


class Snapshots(ORM):
    """Read-only thin images, node-bound. `role` distinguishes a plain snapshot
    (a point-in-time freeze of a volume) from a golden image (a bootable rootfs
    source). `parent` is the id this was derived from — a volume for a snapshot,
    a snapshot for a golden (null for the substrate's default golden). `lv_name`
    is the backing LV. Status enforced by the router layer, not SQL."""
    name = "snapshots"
    fields = {
        "id":         "TEXT PRIMARY KEY",
        "node_id":    "TEXT NOT NULL",
        "name":       "TEXT NOT NULL",
        "role":       "TEXT NOT NULL DEFAULT 'snapshot'",
        "parent":     "TEXT",
        "lv_name":    "TEXT NOT NULL",
        "size_mb":    "INTEGER NOT NULL DEFAULT 0",
        "created_at": "TEXT NOT NULL",
    }
