from dadar.orm import ORM


class Domains(ORM):
    name = "domains"
    fields = {
        "id":         "TEXT PRIMARY KEY",
        "fqdn":       "TEXT NOT NULL UNIQUE",
        "vm_id":      "TEXT NOT NULL",
        "port":       "INTEGER NOT NULL DEFAULT 80",
        "created_at": "TEXT NOT NULL",
    }
