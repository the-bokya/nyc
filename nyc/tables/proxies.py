from dadar.orm import ORM


class Proxies(ORM):
    name = "proxies"
    fields = {
        "id":           "TEXT PRIMARY KEY",
        "vpc_id":       "TEXT NOT NULL UNIQUE",
        "vm_id":        "TEXT NOT NULL",
        "node_id":      "TEXT NOT NULL",
        "public_ip_id": "TEXT",
        "status":       "TEXT NOT NULL DEFAULT 'pending'",
        "created_at":   "TEXT NOT NULL",
    }
