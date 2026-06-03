from dadar.orm import Client

from nyc.client.lifecycle import vm_down
from nyc.client.vm.list_dirs import run as list_vm_dirs
from nyc.client.volume import lv, names
from nyc.config import resolve, volume_vg
from nyc.tables import Vms


def reconcile(client: Client, node_id: str) -> dict:
    paths = resolve()
    vg = volume_vg(node_id)
    expected = {r.__dict__["id"] for r in Vms(client).docs.get_all(where={"node_id": node_id})}
    on_disk = set(list_vm_dirs(paths.vms_dir))
    orphans = on_disk - expected
    for vm_id in orphans:
        vm_down.run(paths.vms_dir, vm_id, vg)  # also removes the rootfs clone LV
    _prune_rootfs_lvs(vg, expected)
    return {"expected": len(expected), "on_disk": len(on_disk), "killed": sorted(orphans)}


def _prune_rootfs_lvs(vg: str, expected: set) -> None:
    """Drop rootfs clone LVs with no DB row — covers an LV left without its dir."""
    for entry in lv.list_lvs(vg):
        name = entry["lv_name"]
        if name.startswith(names.ROOTFS) and name[len(names.ROOTFS):] not in expected:
            lv.remove(vg, name)
