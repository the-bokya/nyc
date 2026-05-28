"""Cluster-wide defaults that more than one layer needs to agree on.

The default VPC is the network `spawn_vm` drops VMs into when the caller gives
no VPC. It is a /16 (per design: VPCs are /16, not /24) so one VPC comfortably
holds a cluster's worth of VMs. `ensure_default_vpc` is get-or-create and
race-safe — the `vpcs.name` UNIQUE constraint means a concurrent loser just
re-reads the winner's row. The deploy script also pre-creates it; either path
converges on the same single row.
"""
import uuid
from datetime import datetime, timezone

from dadar.orm import Client
from dadar.orm.client import RqliteError

from nyc.tables import Vpcs

DEFAULT_VPC_NAME = "default"
DEFAULT_VPC_CIDR = "172.16.0.0/16"


def ensure_default_vpc(client: Client) -> dict:
    existing = Vpcs(client).docs.get(where={"name": DEFAULT_VPC_NAME})
    if existing is not None:
        return existing.__dict__
    row = {"id": str(uuid.uuid4()), "name": DEFAULT_VPC_NAME,
           "cidr": DEFAULT_VPC_CIDR, "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        Vpcs(client).docs.insert(row)
        return row
    except RqliteError:
        return Vpcs(client).docs.get(where={"name": DEFAULT_VPC_NAME}).__dict__
