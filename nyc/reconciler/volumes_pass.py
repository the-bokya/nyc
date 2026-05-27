from pathlib import Path

from dadar.orm import Client

from nyc.client.volume import delete as vol_delete
from nyc.client.volume.list_files import run as list_vol_files
from nyc.config import resolve
from nyc.tables import Volumes


def reconcile(client: Client, node_id: str) -> dict:
    paths = resolve()
    rows = Volumes(client).docs.get_all(where={"node_id": node_id})
    expected = {Path(r.__dict__["path"]).name for r in rows}
    on_disk = {p.name for p in list_vol_files(paths.volumes_dir)}
    orphan_names = on_disk - expected
    for name in orphan_names:
        vol_delete.run(paths.volumes_dir / name)
    return {"expected": len(expected), "on_disk": len(on_disk), "deleted": sorted(orphan_names)}
