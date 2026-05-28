"""Resolve a node's underlay address and its peers from the dadar registry.

The `client/` layer is dadar-free and pure; this app-layer helper does the DB
lookup and hands plain values (the node's own private IP, the list of peer IPs)
to the lifecycle composer and the reconciler.
"""
from dadar.orm import Client
from dadar.tables import Nodes

LOOPBACK = (None, "127.0.0.1", "localhost")


def node_host(client: Client, node_id: str) -> str:
    row = Nodes(client).docs.get(where={"node_id": node_id})
    return row.__dict__["host"] if row else "127.0.0.1"


def peer_hosts(client: Client, node_id: str) -> list[str]:
    return [n.__dict__["host"] for n in Nodes(client).docs.get_all()
            if n.__dict__["node_id"] != node_id and n.__dict__["host"] not in LOOPBACK]
