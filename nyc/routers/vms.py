"""VM CRUD. Node-bound. POST creates env+netns+tap+boot on the target node.

Two create paths:
  - `POST /vms` — explicit: caller picks the `vpc_id` (and optionally `node_id`).
  - `POST /vms/spawn` — turnkey: no vpc_id/node_id. Lands in the default VPC on
    a randomly chosen node (proxied there, pinned via the `X-Nyc-Pin` header so
    the chosen node doesn't re-roll), auto-creates a per-VM data volume, and
    injects the caller's ssh key + /home mount via a cloud-init seed disk.
"""
import os
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dadar.api.deps import get_client, get_node_id
from dadar.orm import Client
from dadar.tables import Nodes
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from nyc import peers
from nyc.client.lifecycle import vm_down, vm_up
from nyc.client.network.allocate import pick_ip
from nyc.client.vm import status as vm_status
from nyc.client.env.paths import for_vm
from nyc.client.volume import create as vol_create
from nyc.config import resolve
from nyc.defaults import ensure_default_vpc
from nyc.routers._proxy import forward
from nyc.tables import Vms, Vpcs, Volumes

router = APIRouter(prefix="/vms")


class VmIn(BaseModel):
    name: str
    vpc_id: str
    data_volume_id: str | None = None
    node_id: str | None = None


class SpawnIn(BaseModel):
    vm_name: str
    ssh_key: str
    size_mb: int = 1024
    vcpu_count: int = 1
    mem_mib: int = 512


@router.get("")
def list_vms(client: Client = Depends(get_client),
             node_id: str = Depends(get_node_id),
             local_only: str | None = Header(default=None, alias="X-Nyc-Local")) -> list[dict]:
    rows = [v.__dict__ for v in Vms(client).docs.get_all()]
    if local_only:
        return [_with_status(r, client) for r in rows if r["node_id"] == node_id]
    return _merge_remote_status(rows, node_id, client)


@router.post("", status_code=201)
def create_vm(body: VmIn, client: Client = Depends(get_client),
              node_id: str = Depends(get_node_id)) -> dict:
    target = body.node_id or node_id
    if target != node_id:
        return forward(client, target, "POST", "/vms", json=body.model_dump())
    return _create_local(body, target, client)


@router.post("/spawn", status_code=201)
def spawn_vm(body: SpawnIn, client: Client = Depends(get_client),
             node_id: str = Depends(get_node_id),
             pin: str | None = Header(default=None, alias="X-Nyc-Pin")) -> dict:
    target = pin or _random_node(client)
    if target != node_id:
        return forward(client, target, "POST", "/vms/spawn",
                       json=body.model_dump(), headers={"X-Nyc-Pin": target})
    return _spawn_local(body, node_id, client)


@router.get("/{vm_id}")
def get_vm(vm_id: str, client: Client = Depends(get_client),
           node_id: str = Depends(get_node_id)) -> dict:
    row = Vms(client).docs.get(where={"id": vm_id})
    if row is None:
        raise HTTPException(404, "vm not found")
    owner = row.__dict__["node_id"]
    if owner != node_id:
        return forward(client, owner, "GET", f"/vms/{vm_id}")
    return _with_status(row.__dict__, client)


@router.delete("/{vm_id}", status_code=204)
def delete_vm(vm_id: str, client: Client = Depends(get_client),
              node_id: str = Depends(get_node_id)) -> None:
    row = Vms(client).docs.get(where={"id": vm_id})
    if row is None:
        raise HTTPException(404, "vm not found")
    owner = row.__dict__["node_id"]
    if owner != node_id:
        forward(client, owner, "DELETE", f"/vms/{vm_id}")
        return
    _delete_local(vm_id, client)


def _create_local(body: VmIn, node_id: str, client: Client) -> dict:
    vpc = _vpc_or_404(body.vpc_id, client)
    vol_path = _volume_path(body.data_volume_id, client) if body.data_volume_id else None
    ip = pick_ip(vpc["cidr"], _used_ips(body.vpc_id, client))
    row = _row(body, node_id, ip)
    Vms(client).docs.insert(row)
    _bring_up(row, vpc["cidr"], vol_path, client)
    return row


def _bring_up(row: dict, cidr: str, vol_path: Path | None, client: Client,
              ssh_pubkey: str | None = None) -> None:
    vm_up.run(_spec(row, cidr, vol_path, client, ssh_pubkey))
    Vms(client).docs.update(where={"id": row["id"]}, set={"status": "running"})
    row["status"] = "running"


def _spec(row: dict, cidr: str, vol_path: Path | None, client: Client,
          ssh_pubkey: str | None = None) -> "vm_up.VmSpec":
    paths = resolve()
    node_id = row["node_id"]
    return vm_up.VmSpec(
        vm_id=row["id"], vm_name=row["name"], node_id=node_id, vpc_id=row["vpc_id"],
        ip=row["ip"], cidr=cidr, data_volume_path=vol_path,
        assets={"rootfs": paths.rootfs, "kernel": paths.kernel, "ssh_key": paths.ssh_key},
        vms_dir=paths.vms_dir, firecracker_bin=paths.firecracker_bin,
        node_host=peers.node_host(client, node_id),
        peer_hosts=peers.peer_hosts(client, node_id),
        dns=os.environ.get("NYC_VM_DNS", "1.1.1.1"),
        ssh_pubkey=ssh_pubkey,
        vcpu_count=int(row.get("vcpu_count", 1)), mem_mib=int(row.get("mem_mib", 512)),
    )


def _spawn_local(body: SpawnIn, node_id: str, client: Client) -> dict:
    vpc = ensure_default_vpc(client)
    vol = _auto_volume(body.vm_name, body.size_mb, node_id, client)
    ip = pick_ip(vpc["cidr"], _used_ips(vpc["id"], client))
    row = _spawn_row(body, node_id, vpc["id"], ip, vol["id"])
    Vms(client).docs.insert(row)
    _bring_up(row, vpc["cidr"], Path(vol["path"]), client, ssh_pubkey=body.ssh_key)
    return row


def _random_node(client: Client) -> str:
    rows = Nodes(client).docs.get_all()
    if not rows:
        raise HTTPException(503, "no nodes registered")
    return random.choice(rows).__dict__["node_id"]


def _auto_volume(vm_name: str, size_mb: int, node_id: str, client: Client) -> dict:
    vol_id = str(uuid.uuid4())
    vol_path = resolve().volumes_dir / f"{vol_id}.ext4"
    vol_create.run(vol_path, size_mb)
    row = {"id": vol_id, "node_id": node_id, "name": f"{vm_name}-data", "size_mb": size_mb,
           "path": str(vol_path), "status": "ready",
           "created_at": datetime.now(timezone.utc).isoformat()}
    Volumes(client).docs.insert(row)
    return row


def _spawn_row(body: SpawnIn, node_id: str, vpc_id: str, ip: str, vol_id: str) -> dict:
    return {"id": str(uuid.uuid4()), "node_id": node_id, "name": body.vm_name,
            "vpc_id": vpc_id, "data_volume_id": vol_id, "ip": ip, "ssh_pubkey_path": None,
            "vcpu_count": body.vcpu_count, "mem_mib": body.mem_mib,
            "status": "pending", "created_at": datetime.now(timezone.utc).isoformat()}


def _delete_local(vm_id: str, client: Client) -> None:
    paths = resolve()
    vm_down.run(paths.vms_dir, vm_id)
    Vms(client).docs.delete(where={"id": vm_id})


def _row(body: VmIn, node_id: str, ip: str) -> dict:
    paths = resolve()
    return {"id": str(uuid.uuid4()), "node_id": node_id, "name": body.name,
            "vpc_id": body.vpc_id, "data_volume_id": body.data_volume_id,
            "ip": ip, "ssh_pubkey_path": str(paths.ssh_key) + ".pub",
            "status": "pending", "created_at": datetime.now(timezone.utc).isoformat()}


def _vpc_or_404(vpc_id: str, client: Client) -> dict:
    vpc = Vpcs(client).docs.get(where={"id": vpc_id})
    if vpc is None:
        raise HTTPException(400, "unknown vpc_id")
    return vpc.__dict__


def _volume_path(volume_id: str, client: Client) -> Path:
    vol = Volumes(client).docs.get(where={"id": volume_id})
    if vol is None:
        raise HTTPException(400, "unknown data_volume_id")
    return Path(vol.__dict__["path"])


def _used_ips(vpc_id: str, client: Client) -> set[str]:
    return {v.__dict__["ip"] for v in Vms(client).docs.get_all(where={"vpc_id": vpc_id})}


def _with_status(row: dict, client: Client) -> dict:
    paths = resolve()
    live = vm_status.run(for_vm(paths.vms_dir, row["id"]))
    return {**row, "live_status": live}


def _merge_remote_status(rows: list[dict], node_id: str, client: Client) -> list[dict]:
    # live_status is a per-owner observation (a local process check). Compute it
    # for our own VMs; for the rest, ask each owning node for its local view.
    # The X-Nyc-Local header makes that call return only the owner's VMs without
    # re-fanning out, so this is one hop per owning node, not a broadcast storm.
    by_id = {r["id"]: _with_status(r, client) for r in rows if r["node_id"] == node_id}
    for owner in {r["node_id"] for r in rows if r["node_id"] != node_id}:
        try:
            remote = forward(client, owner, "GET", "/vms", headers={"X-Nyc-Local": "1"})
        except HTTPException:
            remote = []
        for rv in remote:
            by_id[rv["id"]] = rv
    return [by_id.get(r["id"], {**r, "live_status": "unknown"}) for r in rows]
