from dadar.orm import Client

from nyc.client.lifecycle import vm_down
from nyc.client.vm.list_dirs import run as list_vm_dirs
from nyc.config import resolve
from nyc.tables import Vms


def reconcile(client: Client, node_id: str) -> dict:
    paths = resolve()
    rows = Vms(client).docs.get_all(where={"node_id": node_id})
    expected = {r.__dict__["id"] for r in rows}
    on_disk = set(list_vm_dirs(paths.vms_dir))
    orphans = on_disk - expected
    for vm_id in orphans:
        vm_down.run(paths.vms_dir, vm_id)
    return {"expected": len(expected), "on_disk": len(on_disk), "killed": sorted(orphans)}
