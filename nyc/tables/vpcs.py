from dadar.orm import ORM


class Vpcs(ORM):
    name = "vpcs"
    fields = {
        "id":         "TEXT PRIMARY KEY",
        "name":       "TEXT NOT NULL UNIQUE",
        "cidr":       "TEXT NOT NULL",
        "created_at": "TEXT NOT NULL",
    }
