"""Re-ensure public IP host binding + NAT after node reboot.

DB is source of truth: for every PublicIps row with node_id==this_node and
status='attached', idempotently re-apply host.bind + nat.attach. Same
DB-as-truth, re-ensure philosophy as the other reconciler passes.
"""
from dadar.orm import Client

from nyc.client.pubip import host as pubip_host
from nyc.client.pubip import nat as pubip_nat
from nyc.tables import PublicIps, Vms


def reconcile(client: Client, node_id: str) -> dict:
    rows = PublicIps(client).docs.get_all(where={"node_id": node_id, "status": "attached"})
    ensured, skipped = 0, 0
    for row in rows:
        d = row.__dict__
        vm = Vms(client).docs.get(where={"id": d["vm_id"]})
        if vm is None:
            skipped += 1
            continue
        pubip_host.bind(d["address"], d["iface"])
        pubip_nat.attach(d["address"], vm.__dict__["ip"])
        ensured += 1
    return {"ensured": ensured, "skipped": skipped}
