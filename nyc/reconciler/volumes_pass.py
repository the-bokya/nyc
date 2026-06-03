from dadar.orm import Client

from nyc.client.volume import delete as vol_delete
from nyc.client.volume import lv, names
from nyc.config import volume_vg
from nyc.tables import Volumes


def reconcile(client: Client, node_id: str) -> dict:
    vg = volume_vg(node_id)
    rows = Volumes(client).docs.get_all(where={"node_id": node_id})
    expected = {names.data(r.__dict__["id"]) for r in rows}
    on_disk = {l["lv_name"] for l in lv.list_lvs(vg) if l["lv_name"].startswith(names.DATA)}
    orphans = on_disk - expected
    for name in orphans:
        vol_delete.run(vg, name)
    return {"expected": len(expected), "on_disk": len(on_disk), "deleted": sorted(orphans)}
