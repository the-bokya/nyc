"""Remove a data volume's thin LV (idempotent)."""
from nyc.client.volume import lv


def run(vg: str, name: str) -> None:
    lv.remove(vg, name)
