"""Keep each local VPC's VXLAN head-end FDB in sync with the current node set.

VM bring-up populates the FDB from the registry at create time, but the peer
set changes as nodes join/leave. This pass re-reconciles the FDB for every VPC
that has a VM on this node. No-op on a single host (loopback).
"""
from dadar.orm import Client

from nyc import peers
from nyc.client.network import vxlan
from nyc.tables import Vms


def reconcile(client: Client, node_id: str) -> dict:
    host = peers.node_host(client, node_id)
    if host in peers.LOOPBACK:
        return {"synced": []}
    plist = peers.peer_hosts(client, node_id)
    synced = [v for v in _local_vpcs(client, node_id) if _sync(node_id, v, plist)]
    return {"synced": sorted(synced)}


def _sync(node_id: str, vpc_id: str, plist: list[str]) -> bool:
    name = vxlan.name_for(node_id, vpc_id)
    if not vxlan.exists(name):
        return False
    vxlan.set_fdb(name, plist)
    return True


def _local_vpcs(client: Client, node_id: str) -> set[str]:
    return {v.__dict__["vpc_id"] for v in Vms(client).docs.get_all(where={"node_id": node_id})}
