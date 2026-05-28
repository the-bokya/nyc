"""VM CRUD. Node-bound. POST creates env+netns+tap+boot on the target node.

Node selection: explicit `node_id` in body wins; otherwise the receiving node
takes it. Production might want a scheduler — out of scope for now.
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dadar.api.deps import get_client, get_node_id
from dadar.orm import Client
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nyc import peers
from nyc.client.lifecycle import vm_down, vm_up
from nyc.client.network.allocate import pick_ip
from nyc.client.vm import status as vm_status
from nyc.client.env.paths import for_vm
from nyc.config import resolve
from nyc.routers._proxy import forward
from nyc.tables import Vms, Vpcs, Volumes

router = APIRouter(prefix="/vms")


class VmIn(BaseModel):
    name: str
    vpc_id: str
    data_volume_id: str | None = None
    node_id: str | None = None


@router.get("")
def list_vms(client: Client = Depends(get_client)) -> list[dict]:
    return [_with_status(v.__dict__, client) for v in Vms(client).docs.get_all()]


@router.post("", status_code=201)
def create_vm(body: VmIn, client: Client = Depends(get_client),
              node_id: str = Depends(get_node_id)) -> dict:
    target = body.node_id or node_id
    if target != node_id:
        return forward(client, target, "POST", "/vms", json=body.model_dump())
    return _create_local(body, target, client)


@router.get("/{vm_id}")
def get_vm(vm_id: str, client: Client = Depends(get_client)) -> dict:
    row = Vms(client).docs.get(where={"id": vm_id})
    if row is None:
        raise HTTPException(404, "vm not found")
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


def _bring_up(row: dict, cidr: str, vol_path: Path | None, client: Client) -> None:
    vm_up.run(_spec(row, cidr, vol_path, client))
    Vms(client).docs.update(where={"id": row["id"]}, set={"status": "running"})
    row["status"] = "running"


def _spec(row: dict, cidr: str, vol_path: Path | None, client: Client) -> "vm_up.VmSpec":
    paths = resolve()
    node_id = row["node_id"]
    return vm_up.VmSpec(
        vm_id=row["id"], node_id=node_id, vpc_id=row["vpc_id"],
        ip=row["ip"], cidr=cidr, data_volume_path=vol_path,
        assets={"rootfs": paths.rootfs, "kernel": paths.kernel, "ssh_key": paths.ssh_key},
        vms_dir=paths.vms_dir, firecracker_bin=paths.firecracker_bin,
        node_host=peers.node_host(client, node_id),
        peer_hosts=peers.peer_hosts(client, node_id),
        dns=os.environ.get("NYC_VM_DNS", "1.1.1.1"),
    )


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
