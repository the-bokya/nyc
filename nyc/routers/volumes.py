"""Volume CRUD. Node-bound — POST proxies to the target node, GET reads from local raft."""
import uuid
from datetime import datetime, timezone

from dadar.api.deps import get_client, get_node_id
from dadar.orm import Client
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nyc.client.volume import create as vol_create
from nyc.client.volume import delete as vol_delete
from nyc.config import resolve
from nyc.routers._proxy import forward
from nyc.tables import Volumes, Vms

router = APIRouter(prefix="/volumes")


class VolumeIn(BaseModel):
    name: str
    size_mb: int
    node_id: str | None = None


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
    paths = resolve()
    vol_path = paths.volumes_dir / f"{vol_id}.ext4"
    vol_create.run(vol_path, body.size_mb)
    row = {"id": vol_id, "node_id": node_id, "name": body.name, "size_mb": body.size_mb,
           "path": str(vol_path), "status": "ready",
           "created_at": datetime.now(timezone.utc).isoformat()}
    Volumes(client).docs.insert(row)
    return row


def _delete_local(row: dict, client: Client) -> None:
    if Vms(client).docs.get(where={"data_volume_id": row["id"]}) is not None:
        raise HTTPException(409, "volume attached to a vm")
    from pathlib import Path
    vol_delete.run(Path(row["path"]))
    Volumes(client).docs.delete(where={"id": row["id"]})
