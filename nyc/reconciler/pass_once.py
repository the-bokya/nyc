"""One reconciliation pass: align local disk/network state to the DB.

DB is source of truth. For each local resource owned by this node:
  - row exists, resource missing → recreate
  - row missing, resource exists → tear down
"""
from dadar.orm import Client

from nyc.reconciler.overlay_pass import reconcile as reconcile_overlay
from nyc.reconciler.ttl_pass import reconcile as reconcile_ttl
from nyc.reconciler.vms_pass import reconcile as reconcile_vms
from nyc.reconciler.volumes_pass import reconcile as reconcile_volumes


def run(client: Client, node_id: str) -> dict:
    ttl = reconcile_ttl(client, node_id)
    vms = reconcile_vms(client, node_id)
    vols = reconcile_volumes(client, node_id)
    overlay = reconcile_overlay(client, node_id)
    return {"ttl": ttl, "vms": vms, "volumes": vols, "overlay": overlay}
