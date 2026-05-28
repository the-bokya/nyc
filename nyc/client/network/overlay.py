"""Deterministic per-VPC overlay identifiers.

Pure functions — same VPC id yields the same answer on every node, so all
nodes agree on a VPC's VXLAN id and its anycast gateway MAC with zero
coordination (no allocator, no extra DB column).
"""
import hashlib


def vni_for(vpc_id: str) -> int:
    """A stable VXLAN Network Identifier in [1, 2**24 - 1] derived from vpc_id."""
    digest = hashlib.sha256(vpc_id.encode()).digest()
    return int.from_bytes(digest[:4], "big") % (2**24 - 1) + 1


def anycast_mac(vpc_id: str) -> str:
    """A locally-administered unicast MAC, identical on every node for this VPC.

    Used as the shared gateway MAC: each node's VPC bridge carries this address,
    so a guest's gateway-bound frames are consumed locally (a Linux bridge
    terminates frames to its own MAC and never floods them across the overlay).
    """
    b = hashlib.sha256(vpc_id.encode()).digest()
    return "02:" + ":".join(f"{x:02x}" for x in b[:5])
