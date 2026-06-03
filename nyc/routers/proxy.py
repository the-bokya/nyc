"""Turnkey reverse-proxy endpoint.

POST /proxy — spawn a proxy VM, attach a public IP, enqueue setup + reload.
GET  /proxy — show the VPC's proxy + public IP + domain count.
"""
import json
import uuid
from datetime import datetime, timezone

from dadar.api.deps import get_client, get_node_id
from dadar.orm import Client
from dadar.tables import Nodes
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nyc.client.pubip import backend as pubip_backend
from nyc.client.pubip import host as pubip_host
from nyc.client.pubip import nat as pubip_nat
from nyc.config import pubip as pubip_cfg
from nyc.defaults import ensure_default_vpc
from nyc.routers._proxy import forward
from nyc.tables import Domains, Proxies, PublicIps, Tasks

router = APIRouter(prefix="/proxy")


class ProxyIn(BaseModel):
    name: str | None = None
    vcpu_count: int = 1
    mem_mib: int = 512


@router.get("")
def get_proxy(client: Client = Depends(get_client)) -> dict:
    vpc = ensure_default_vpc(client)
    proxy = Proxies(client).docs.get(where={"vpc_id": vpc["id"]})
    if proxy is None:
        raise HTTPException(404, "no proxy for default VPC")
    d = proxy.__dict__
    pip = None
    if d.get("public_ip_id"):
        pip_row = PublicIps(client).docs.get(where={"id": d["public_ip_id"]})
        pip = pip_row.__dict__ if pip_row else None
    domain_count = len(Domains(client).docs.get_all())
    return {**d, "public_ip": pip, "domain_count": domain_count}


@router.post("", status_code=201)
def create_proxy(body: ProxyIn, client: Client = Depends(get_client),
                 node_id: str = Depends(get_node_id)) -> dict:
    vpc = ensure_default_vpc(client)
    if Proxies(client).docs.get(where={"vpc_id": vpc["id"]}) is not None:
        raise HTTPException(409, "proxy already exists for this VPC")

    target = _node_with_free_ip(client, node_id)
    if target != node_id:
        return forward(client, target, "POST", "/proxy",
                       json=body.model_dump(), headers={"X-Nyc-Pin": target})

    return _create_proxy_local(body, vpc, node_id, client)


def _create_proxy_local(body: ProxyIn, vpc: dict, node_id: str, client: Client) -> dict:
    from nyc.routers.vms import SpawnIn, _spawn_local
    from nyc.config import resolve

    ssh_key_path = resolve().ssh_key
    spawn_body = SpawnIn(
        vm_name=body.name or "proxy",
        ssh_key="",  # will use baked-in key; empty pubkey is fine
        vcpu_count=body.vcpu_count,
        mem_mib=body.mem_mib,
    )
    vm = _spawn_local(spawn_body, node_id, client)
    vm_id = vm["id"]
    vm_ip = vm["ip"]

    # Attach public IP
    cfg = pubip_cfg()
    used = {r.__dict__["address"] for r in PublicIps(client).docs.get_all(where={"node_id": node_id})}
    address, gateway, iface, provider = pubip_backend.acquire(cfg, used)
    pubip_host.bind(address, iface)
    pubip_nat.attach(address, vm_ip)
    pip_row = {
        "id": str(uuid.uuid4()),
        "node_id": node_id,
        "vm_id": vm_id,
        "address": address,
        "gateway": gateway,
        "iface": iface,
        "provider": provider,
        "status": "attached",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    PublicIps(client).docs.insert(pip_row)

    # Insert Proxies row
    proxy_row = {
        "id": str(uuid.uuid4()),
        "vpc_id": vpc["id"],
        "vm_id": vm_id,
        "node_id": node_id,
        "public_ip_id": pip_row["id"],
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    Proxies(client).docs.insert(proxy_row)

    # Enqueue setup + reload tasks
    now = datetime.now(timezone.utc).isoformat()
    setup_task = _task_row(vm_id, node_id, "reverse_proxy_setup",
                           json.dumps({"vm_ip": vm_ip, "public_ip": address}), now)
    reload_task = _task_row(vm_id, node_id, "proxy_reload", None, now)
    Tasks(client).docs.insert(setup_task)
    Tasks(client).docs.insert(reload_task)

    return {"vm": vm, "public_ip": pip_row, "proxy": proxy_row,
            "setup_task_id": setup_task["id"]}


def _task_row(vm_id: str, node_id: str, task_type: str, params, now: str) -> dict:
    return {"id": str(uuid.uuid4()), "node_id": node_id, "vm_id": vm_id,
            "type": task_type, "params": params, "status": "pending",
            "result": None, "created_at": now, "updated_at": now}


def _node_with_free_ip(client: Client, default: str) -> str:
    """Return the first node that has at least one free public IP."""
    cfg = pubip_cfg()
    all_ips = {r.__dict__["address"] for r in PublicIps(client).docs.get_all()}
    free = [a for a in cfg.addresses if a not in all_ips]
    if free:
        return default
    nodes = Nodes(client).docs.get_all()
    if not nodes:
        raise HTTPException(503, "no nodes registered")
    raise HTTPException(503, "no nodes have free public IPs")
