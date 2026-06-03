"""Volume CRUD. Node-bound — POST proxies to the target node, GET reads from local raft."""
import uuid
from datetime import datetime, timezone

from dadar.api.deps import get_client, get_node_id
from dadar.orm import Client
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nyc.client.volume import create as vol_create
from nyc.client.volume import delete as vol_delete
from nyc.client.volume import lv, names
from nyc.config import lvm, volume_vg
from nyc.routers._proxy import forward
from nyc.tables import Volumes, Vms, Snapshots

router = APIRouter(prefix="/volumes")


class VolumeIn(BaseModel):
    name: str
    size_mb: int = 0
    node_id: str | None = None
    from_snapshot: str | None = None  # clone from this snapshot instead of a blank ext4 LV


class ResizeIn(BaseModel):
    size_mb: int


@router.get("")
def list_volumes(client: Client = Depends(get_client)) -> list[dict]:
    return [v.__dict__ for v in Volumes(client).docs.get_all()]


@router.post("", status_code=201)
def create_volume(body: VolumeIn, client: Client = Depends(get_client),
                  node_id: str = Depends(get_node_id)) -> dict:
    target = body.node_id or node_id
    if target != node_id:
        return forward(client, target, "POST", "/volumes", json=body.model_dump())
    return _create_local(body, target, client)


@router.get("/{volume_id}")
def get_volume(volume_id: str, client: Client = Depends(get_client)) -> dict:
    row = Volumes(client).docs.get(where={"id": volume_id})
    if row is None:
        raise HTTPException(404, "volume not found")
    return row.__dict__


@router.patch("/{volume_id}")
def resize_volume(volume_id: str, body: ResizeIn, client: Client = Depends(get_client),
                  node_id: str = Depends(get_node_id)) -> dict:
    row = Volumes(client).docs.get(where={"id": volume_id})
    if row is None:
        raise HTTPException(404, "volume not found")
    owner = row.__dict__["node_id"]
    if owner != node_id:
        return forward(client, owner, "PATCH", f"/volumes/{volume_id}", json=body.model_dump())
    return _resize_local(row.__dict__, body.size_mb, client)


def _resize_local(row: dict, size_mb: int, client: Client) -> dict:
    lv.extend(volume_vg(row["node_id"]), names.data(row["id"]), size_mb)
    Volumes(client).docs.update(where={"id": row["id"]}, set={"size_mb": size_mb})
    return {**row, "size_mb": size_mb}


@router.delete("/{volume_id}", status_code=204)
def delete_volume(volume_id: str, client: Client = Depends(get_client),
                  node_id: str = Depends(get_node_id)) -> None:
    row = Volumes(client).docs.get(where={"id": volume_id})
    if row is None:
        raise HTTPException(404, "volume not found")
    owner = row.__dict__["node_id"]
    if owner != node_id:
        forward(client, owner, "DELETE", f"/volumes/{volume_id}")
        return
    _delete_local(row.__dict__, client)


def _create_local(body: VolumeIn, node_id: str, client: Client) -> dict:
    vol_id = str(uuid.uuid4())
    vg = volume_vg(node_id)
    dev, size_mb = _provision(body, vg, names.data(vol_id), node_id, client)
    row = {"id": vol_id, "node_id": node_id, "name": body.name, "size_mb": size_mb,
           "path": dev, "status": "ready",
           "created_at": datetime.now(timezone.utc).isoformat()}
    Volumes(client).docs.insert(row)
    return row


def _provision(body: VolumeIn, vg: str, name: str, node_id: str, client: Client) -> tuple[str, int]:
    if not body.from_snapshot:
        return vol_create.run(vg, lvm().thinpool, name, body.size_mb), body.size_mb
    snap = Snapshots(client).docs.get(where={"id": body.from_snapshot})
    if snap is None or snap.__dict__["node_id"] != node_id:
        raise HTTPException(400, "unknown or remote from_snapshot")
    return vol_create.from_snapshot(vg, snap.__dict__["lv_name"], name), snap.__dict__["size_mb"]


def _delete_local(row: dict, client: Client) -> None:
    if Vms(client).docs.get(where={"data_volume_id": row["id"]}) is not None:
        raise HTTPException(409, "volume attached to a vm")
    vol_delete.run(volume_vg(row["node_id"]), names.data(row["id"]))
    Volumes(client).docs.delete(where={"id": row["id"]})
