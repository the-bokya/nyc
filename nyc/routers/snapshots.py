"""Snapshot + golden-image CRUD. Node-bound — the thin LV lives on the
resource's owner node, so writes proxy to the owner and reads serve from local
raft (replicated). A golden image is a snapshot promoted to `role=golden`: a
cheap read-only thin snapshot, never a block copy. Snapshots are independent of
their origin, so deleting one never affects the volumes/VMs cloned from it.
"""
import uuid
from datetime import datetime, timezone

from dadar.api.deps import get_client, get_node_id
from dadar.orm import Client
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nyc.client.volume import names
from nyc.client.volume import snapshot as snap_action
from nyc.config import volume_vg
from nyc.routers._proxy import forward
from nyc.tables import Snapshots, Volumes

snapshots = APIRouter(prefix="/snapshots")
images = APIRouter(prefix="/images")


class SnapshotIn(BaseModel):
    name: str
    volume_id: str


class ImageIn(BaseModel):
    name: str
    from_snapshot: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@snapshots.get("")
def list_snapshots(client: Client = Depends(get_client)) -> list[dict]:
    return [s.__dict__ for s in Snapshots(client).docs.get_all(where={"role": "snapshot"})]


@snapshots.post("", status_code=201)
def create_snapshot(body: SnapshotIn, client: Client = Depends(get_client),
                    node_id: str = Depends(get_node_id)) -> dict:
    vol = Volumes(client).docs.get(where={"id": body.volume_id})
    if vol is None:
        raise HTTPException(400, "unknown volume_id")
    owner = vol.__dict__["node_id"]
    if owner != node_id:
        return forward(client, owner, "POST", "/snapshots", json=body.model_dump())
    return _create_snapshot_local(body, vol.__dict__, owner, client)


@snapshots.get("/{snapshot_id}")
def get_snapshot(snapshot_id: str, client: Client = Depends(get_client)) -> dict:
    return _get_or_404(snapshot_id, "snapshot", client)


@snapshots.delete("/{snapshot_id}", status_code=204)
def delete_snapshot(snapshot_id: str, client: Client = Depends(get_client),
                    node_id: str = Depends(get_node_id)) -> None:
    _delete(snapshot_id, "snapshot", "/snapshots", client, node_id)


@images.get("")
def list_images(client: Client = Depends(get_client)) -> list[dict]:
    return [s.__dict__ for s in Snapshots(client).docs.get_all(where={"role": "golden"})]


@images.post("", status_code=201)
def create_image(body: ImageIn, client: Client = Depends(get_client),
                 node_id: str = Depends(get_node_id)) -> dict:
    snap = Snapshots(client).docs.get(where={"id": body.from_snapshot, "role": "snapshot"})
    if snap is None:
        raise HTTPException(400, "unknown from_snapshot")
    owner = snap.__dict__["node_id"]
    if owner != node_id:
        return forward(client, owner, "POST", "/images", json=body.model_dump())
    return _create_image_local(body, snap.__dict__, owner, client)


@images.get("/{image_id}")
def get_image(image_id: str, client: Client = Depends(get_client)) -> dict:
    return _get_or_404(image_id, "golden", client)


@images.delete("/{image_id}", status_code=204)
def delete_image(image_id: str, client: Client = Depends(get_client),
                 node_id: str = Depends(get_node_id)) -> None:
    _delete(image_id, "golden", "/images", client, node_id)


def _create_snapshot_local(body: SnapshotIn, vol: dict, node_id: str, client: Client) -> dict:
    snap_id = str(uuid.uuid4())
    snap_action.create(volume_vg(node_id), body.volume_id, snap_id)
    row = {"id": snap_id, "node_id": node_id, "name": body.name, "role": "snapshot",
           "parent": body.volume_id, "lv_name": names.snap(snap_id),
           "size_mb": vol["size_mb"], "created_at": _now()}
    Snapshots(client).docs.insert(row)
    return row


def _create_image_local(body: ImageIn, snap: dict, node_id: str, client: Client) -> dict:
    gold_id = str(uuid.uuid4())
    snap_action.golden(volume_vg(node_id), snap["id"], gold_id)
    row = {"id": gold_id, "node_id": node_id, "name": body.name, "role": "golden",
           "parent": snap["id"], "lv_name": names.gold(gold_id),
           "size_mb": snap["size_mb"], "created_at": _now()}
    Snapshots(client).docs.insert(row)
    return row


def _get_or_404(rid: str, role: str, client: Client) -> dict:
    row = Snapshots(client).docs.get(where={"id": rid, "role": role})
    if row is None:
        raise HTTPException(404, f"{role} not found")
    return row.__dict__


def _delete(rid: str, role: str, prefix: str, client: Client, node_id: str) -> None:
    row = Snapshots(client).docs.get(where={"id": rid, "role": role})
    if row is None:
        raise HTTPException(404, f"{role} not found")
    owner = row.__dict__["node_id"]
    if owner != node_id:
        forward(client, owner, "DELETE", f"{prefix}/{rid}")
        return
    snap_action.remove(volume_vg(owner), row.__dict__["lv_name"])
    Snapshots(client).docs.delete(where={"id": rid})
