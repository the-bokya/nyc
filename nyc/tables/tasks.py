from dadar.orm import ORM


class Tasks(ORM):
    name = "tasks"
    fields = {
        "id":         "TEXT PRIMARY KEY",
        "node_id":    "TEXT NOT NULL",
        "vm_id":      "TEXT NOT NULL",
        "type":       "TEXT NOT NULL",
        "params":     "TEXT",
        "status":     "TEXT NOT NULL DEFAULT 'pending'",
        "result":     "TEXT",
        "created_at": "TEXT NOT NULL",
        "updated_at": "TEXT NOT NULL",
    }
