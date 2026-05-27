from dadar.orm import ORM


class Vms(ORM):
    name = "vms"
    fields = {
        "id":              "TEXT PRIMARY KEY",
        "node_id":         "TEXT NOT NULL",
        "name":            "TEXT NOT NULL",
        "vpc_id":          "TEXT NOT NULL",
        "data_volume_id":  "TEXT",
        "ip":              "TEXT NOT NULL",
        "ssh_pubkey_path": "TEXT",
        "status":          "TEXT NOT NULL DEFAULT 'pending'",
        "created_at":      "TEXT NOT NULL",
    }
