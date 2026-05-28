"""Per-VPC VXLAN tunnel: one device per node, enslaved to the VPC bridge.

Cross-node L2 without multicast: BUM (broadcast/unknown-unicast/multicast)
traffic is replicated to every peer via static all-zeros FDB entries
("head-end replication"); known unicast is delivered point-to-point once the
bridge learns the remote MAC. Peer destinations are the other nodes' underlay
IPs, resolved from the dadar `nodes` registry by the caller.
"""
from nyc.client import privops

DSTPORT = "4789"
ALL_ZEROS = "00:00:00:00:00:00"


def name_for(node_id: str, vpc_id: str) -> str:
    # IFNAMSIZ caps names at 15 chars: "vx-XXXX-XXXX" = 12.
    return f"vx-{node_id[:4]}-{vpc_id[:4]}"


def ensure(name: str, vni: int, local_ip: str, bridge: str) -> None:
    if exists(name):
        return
    privops.run(["ip", "link", "add", name, "type", "vxlan", "id", str(vni),
                 "dstport", DSTPORT, "local", local_ip])
    privops.run(["ip", "link", "set", name, "master", bridge])
    privops.run(["ip", "link", "set", name, "up"])


def set_fdb(name: str, peers: list[str]) -> None:
    """Reconcile the head-end replication list to exactly `peers`."""
    current = _fdb_peers(name)
    for dst in set(peers) - current:
        privops.run(["bridge", "fdb", "append", ALL_ZEROS, "dev", name, "dst", dst])
    for dst in current - set(peers):
        privops.run(["bridge", "fdb", "del", ALL_ZEROS, "dev", name, "dst", dst])


def delete(name: str) -> None:
    if exists(name):
        privops.run(["ip", "link", "del", name])


def exists(name: str) -> bool:
    if privops.backend() == "fake":
        from nyc.client.privops_fake import STATE
        return name in STATE["links"]
    try:
        privops.run(["ip", "link", "show", name])
        return True
    except privops.PrivopsError:
        return False


def _fdb_peers(name: str) -> set[str]:
    out = privops.run(["bridge", "fdb", "show", "dev", name])
    return {line.split("dst")[1].split()[0]
            for line in out.splitlines() if ALL_ZEROS in line and "dst" in line}
