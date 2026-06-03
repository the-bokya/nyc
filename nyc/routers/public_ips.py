"""Public IP attach/detach for VMs.

POST /vms/{vm_id}/public-ip  — bind a public IP to the VM (proxied to owner node).
DELETE /vms/{vm_id}/public-ip — detach.
GET /public-ips — list all.
"""
import uuid
from datetime import datetime, timezone

from dadar.api.deps import get_client, get_node_id
from dadar.orm import Client
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nyc.client.pubip import backend as pubip_backend
from nyc.client.pubip import host as pubip_host
from nyc.client.pubip import nat as pubip_nat
from nyc.config import pubip as pubip_cfg
from nyc.routers._proxy import forward
from nyc.tables import PublicIps, Vms

router = APIRouter()


class PublicIpIn(BaseModel):
    address: str | None = None
    provider: str | None = None


@router.get("/public-ips")
def list_public_ips(client: Client = Depends(get_client)) -> list[dict]:
    return [p.__dict__ for p in PublicIps(client).docs.get_all()]


@router.post("/vms/{vm_id}/public-ip", status_code=201)
def attach_public_ip(vm_id: str, body: PublicIpIn = PublicIpIn(),
                     client: Client = Depends(get_client),
                     node_id: str = Depends(get_node_id)) -> dict:
    vm = _vm_or_404(vm_id, client)
    owner = vm["node_id"]
    if owner != node_id:
        return forward(client, owner, "POST", f"/vms/{vm_id}/public-ip",
                       json=body.model_dump())
    return _attach_local(vm, client, node_id)


@router.delete("/vms/{vm_id}/public-ip", status_code=204)
def detach_public_ip(vm_id: str, client: Client = Depends(get_client),
                     node_id: str = Depends(get_node_id)) -> None:
    vm = _vm_or_404(vm_id, client)
    owner = vm["node_id"]
    if owner != node_id:
        forward(client, owner, "DELETE", f"/vms/{vm_id}/public-ip")
        return
    _detach_local(vm_id, vm["ip"], client)


def _attach_local(vm: dict, client: Client, node_id: str) -> dict:
    cfg = pubip_cfg()
    used = {r.__dict__["address"] for r in PublicIps(client).docs.get_all(where={"node_id": node_id})}
    address, gateway, iface, provider = pubip_backend.acquire(cfg, used)
    pubip_host.bind(address, iface)
    pubip_nat.attach(address, vm["ip"])
    row = {
        "id": str(uuid.uuid4()),
        "node_id": node_id,
        "vm_id": vm["id"],
        "address": address,
        "gateway": gateway,
        "iface": iface,
        "provider": provider,
        "status": "attached",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    PublicIps(client).docs.insert(row)
    return row


def _detach_local(vm_id: str, vm_ip: str, client: Client) -> None:
    pip = PublicIps(client).docs.get(where={"vm_id": vm_id, "status": "attached"})
    if pip is None:
        return
    d = pip.__dict__
    pubip_nat.detach(d["address"], vm_ip)
    pubip_host.unbind(d["address"], d["iface"])
    cfg = pubip_cfg()
    pubip_backend.release(cfg, d["address"])
    PublicIps(client).docs.delete(where={"id": d["id"]})


def _vm_or_404(vm_id: str, client: Client) -> dict:
    vm = Vms(client).docs.get(where={"id": vm_id})
    if vm is None:
        raise HTTPException(404, "vm not found")
    return vm.__dict__
