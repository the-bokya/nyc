from dadar.orm import Client

from nyc.client.volume import lv, names
from nyc.client.volume import snapshot as snap_action
from nyc.client.volume.pool import GOLD_DEFAULT
from nyc.config import volume_vg
from nyc.tables import Snapshots


def reconcile(client: Client, node_id: str) -> dict:
    vg = volume_vg(node_id)
    rows = Snapshots(client).docs.get_all(where={"node_id": node_id})
    expected = {r.__dict__["lv_name"] for r in rows} | {GOLD_DEFAULT}  # default golden is reserved
    on_disk = {l["lv_name"] for l in lv.list_lvs(vg)
               if l["lv_name"].startswith((names.SNAP, names.GOLD))}
    orphans = on_disk - expected
    for name in orphans:
        snap_action.remove(vg, name)
    return {"expected": len(expected), "on_disk": len(on_disk), "deleted": sorted(orphans)}
