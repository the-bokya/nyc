from dadar.orm import ORM


class Volumes(ORM):
    name = "volumes"
    fields = {
        "id":         "TEXT PRIMARY KEY",
        "node_id":    "TEXT NOT NULL",
        "name":       "TEXT NOT NULL",
        "size_mb":    "INTEGER NOT NULL",
        "path":       "TEXT NOT NULL",
        "status":     "TEXT NOT NULL DEFAULT 'pending'",
        "created_at": "TEXT NOT NULL",
    }
