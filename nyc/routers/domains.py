"""Domain ↔ VM routing intent. Cluster-wide (no node-binding).

POST /domains   attach a subdomain to a VM and trigger a proxy Caddyfile reload.
DELETE /domains/{id}   detach and reload.
"""
import uuid
from datetime import datetime, timezone

from dadar.api.deps import get_client
from dadar.orm import Client
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nyc.config import cluster_domain
from nyc.tables import Domains, Proxies, Tasks, Vms

router = APIRouter(prefix="/domains")


class DomainIn(BaseModel):
    vm_id: str
    port: int = 80
    subdomain: str | None = None
    fqdn: str | None = None


@router.get("")
def list_domains(client: Client = Depends(get_client)) -> list[dict]:
    return [d.__dict__ for d in Domains(client).docs.get_all()]


@router.post("", status_code=201)
def create_domain(body: DomainIn, client: Client = Depends(get_client)) -> dict:
    fqdn = _resolve_fqdn(body)
    _vm_or_404(body.vm_id, client)
    row = {"id": str(uuid.uuid4()), "fqdn": fqdn, "vm_id": body.vm_id,
           "port": body.port, "created_at": datetime.now(timezone.utc).isoformat()}
    Domains(client).docs.insert(row)
    _enqueue_reload(body.vm_id, client)
    return row


@router.delete("/{domain_id}", status_code=204)
def delete_domain(domain_id: str, client: Client = Depends(get_client)) -> None:
    row = Domains(client).docs.get(where={"id": domain_id})
    if row is None:
        raise HTTPException(404, "domain not found")
    vm_id = row.__dict__["vm_id"]
    Domains(client).docs.delete(where={"id": domain_id})
    _enqueue_reload(vm_id, client)


def _resolve_fqdn(body: DomainIn) -> str:
    if body.fqdn:
        return body.fqdn
    if body.subdomain:
        root = cluster_domain()
        if not root:
            raise HTTPException(400, "cluster domain not configured; supply fqdn directly")
        return f"{body.subdomain}.{root}"
    raise HTTPException(400, "supply subdomain or fqdn")


def _vm_or_404(vm_id: str, client: Client) -> dict:
    vm = Vms(client).docs.get(where={"id": vm_id})
    if vm is None:
        raise HTTPException(404, "vm not found")
    return vm.__dict__


def _enqueue_reload(vm_id: str, client: Client) -> None:
    """Enqueue a proxy_reload on the VPC's proxy node, if a proxy exists."""
    vm = Vms(client).docs.get(where={"id": vm_id})
    if vm is None:
        return
    vpc_id = vm.__dict__["vpc_id"]
    proxy = Proxies(client).docs.get(where={"vpc_id": vpc_id})
    if proxy is None:
        return
    proxy_node = proxy.__dict__["node_id"]
    proxy_vm_id = proxy.__dict__["vm_id"]
    _insert_task(proxy_vm_id, proxy_node, "proxy_reload", None, client)


def _insert_task(vm_id: str, node_id: str, task_type: str, params, client: Client) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row = {"id": str(uuid.uuid4()), "node_id": node_id, "vm_id": vm_id,
           "type": task_type, "params": params, "status": "pending",
           "result": None, "created_at": now, "updated_at": now}
    Tasks(client).docs.insert(row)
    return row
