"""Public IP attach/detach for VMs.

POST /vms/{vm_id}/public-ip  — bind a public IP to the VM (proxied to owner node).
DELETE /vms/{vm_id}/public-ip — detach.
GET /public-ips — list all.

Attaching adds a PublicIps row then recreates the VM so it boots with eth1
wired to the public bridge. Detaching deletes the row and recreates again
without eth1. The recreate cost is documented: this is a reboot.
"""
import uuid
from datetime import datetime, timezone

from dadar.api.deps import get_client, get_node_id
from dadar.orm import Client
from fastapi import APIRouter, Depends, HTTPException

from nyc.client.pubip import pool as pubip_pool
from nyc.config import pubip as pubip_cfg
from nyc.routers._proxy import forward
from nyc.routers.vms import recreate_vm
from nyc.tables import PublicIps, Vms

router = APIRouter()


@router.get("/public-ips")
def list_public_ips(client: Client = Depends(get_client)) -> list[dict]:
    return [p.__dict__ for p in PublicIps(client).docs.get_all()]


@router.post("/vms/{vm_id}/public-ip", status_code=201)
def attach_public_ip(vm_id: str, client: Client = Depends(get_client),
                     node_id: str = Depends(get_node_id)) -> dict:
    vm = _vm_or_404(vm_id, client)
    owner = vm["node_id"]
    if owner != node_id:
        return forward(client, owner, "POST", f"/vms/{vm_id}/public-ip")
    return _attach_local(vm, client, node_id)


@router.delete("/vms/{vm_id}/public-ip", status_code=204)
def detach_public_ip(vm_id: str, client: Client = Depends(get_client),
                     node_id: str = Depends(get_node_id)) -> None:
    vm = _vm_or_404(vm_id, client)
    owner = vm["node_id"]
    if owner != node_id:
        forward(client, owner, "DELETE", f"/vms/{vm_id}/public-ip")
        return
    _detach_local(vm_id, client)


def _attach_local(vm: dict, client: Client, node_id: str) -> dict:
    cfg = pubip_cfg()
    used = {r.__dict__["address"] for r in PublicIps(client).docs.get_all(where={"node_id": node_id})}
    address, gateway, mac, prefix = pubip_pool.acquire(cfg, used)
    row = {
        "id": str(uuid.uuid4()),
        "node_id": node_id,
        "vm_id": vm["id"],
        "address": address,
        "gateway": gateway,
        "mac": mac,
        "prefix": prefix,
        "status": "attached",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    PublicIps(client).docs.insert(row)
    # Recreate VM so it boots with eth1 wired to the public bridge.
    recreate_vm(vm["id"], client)
    return row


def _detach_local(vm_id: str, client: Client) -> None:
    pip = PublicIps(client).docs.get(where={"vm_id": vm_id, "status": "attached"})
    if pip is None:
        return
    PublicIps(client).docs.delete(where={"id": pip.__dict__["id"]})
    recreate_vm(vm_id, client)


def _vm_or_404(vm_id: str, client: Client) -> dict:
    vm = Vms(client).docs.get(where={"id": vm_id})
    if vm is None:
        raise HTTPException(404, "vm not found")
    return vm.__dict__
