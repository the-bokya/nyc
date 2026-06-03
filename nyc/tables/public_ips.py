from dadar.orm import ORM


class PublicIps(ORM):
    name = "public_ips"
    fields = {
        "id":         "TEXT PRIMARY KEY",
        "node_id":    "TEXT NOT NULL",
        "vm_id":      "TEXT",
        "address":    "TEXT NOT NULL",
        "gateway":    "TEXT",
        "iface":      "TEXT NOT NULL",
        "provider":   "TEXT NOT NULL DEFAULT 'scaleway'",
        "status":     "TEXT NOT NULL DEFAULT 'attached'",
        "created_at": "TEXT NOT NULL",
    }
