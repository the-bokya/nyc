"""VPC CRUD. Global resources — no node_id, no proxying."""
import ipaddress
import uuid
from datetime import datetime, timezone

from dadar.api.deps import get_client
from dadar.orm import Client
from dadar.orm.client import RqliteError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nyc.tables import Vpcs, Vms

router = APIRouter(prefix="/vpcs")


class VpcIn(BaseModel):
    name: str
    cidr: str


@router.get("")
def list_vpcs(client: Client = Depends(get_client)) -> list[dict]:
    return [v.__dict__ for v in Vpcs(client).docs.get_all()]


@router.post("", status_code=201)
def create_vpc(body: VpcIn, client: Client = Depends(get_client)) -> dict:
    _validate_cidr(body.cidr)
    row = {"id": str(uuid.uuid4()), "name": body.name, "cidr": body.cidr,
           "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        Vpcs(client).docs.insert(row)
    except RqliteError as exc:
        raise HTTPException(409, str(exc))
    return row


@router.get("/{vpc_id}")
def get_vpc(vpc_id: str, client: Client = Depends(get_client)) -> dict:
    row = Vpcs(client).docs.get(where={"id": vpc_id})
    if row is None:
        raise HTTPException(404, "vpc not found")
    return row.__dict__


@router.delete("/{vpc_id}", status_code=204)
def delete_vpc(vpc_id: str, client: Client = Depends(get_client)) -> None:
    if Vms(client).docs.get(where={"vpc_id": vpc_id}) is not None:
        raise HTTPException(409, "vpc still has vms")
    affected = Vpcs(client).docs.delete(where={"id": vpc_id})
    if affected == 0:
        raise HTTPException(404, "vpc not found")


def _validate_cidr(cidr: str) -> None:
    try:
        ipaddress.ip_network(cidr, strict=True)
    except ValueError as exc:
        raise HTTPException(400, f"invalid cidr: {exc}")
