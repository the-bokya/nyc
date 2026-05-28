"""One Linux bridge per VPC on this node.

Bridge name is `br-<6char-node>-<6char-vpc>` so two nodes on the same host
never collide.
"""
from nyc.client import privops


def name_for(node_id: str, vpc_id: str) -> str:
    # Linux IFNAMSIZ caps interface names at 15 chars. "br-XXXX-XXXX" = 12.
    return f"br-{node_id[:4]}-{vpc_id[:4]}"


def ensure(bridge: str, host_ip_cidr: str, mac: str | None = None) -> None:
    if not exists(bridge):
        privops.run(["ip", "link", "add", bridge, "type", "bridge"])
        if mac:  # anycast gateway: same MAC on every node for this VPC
            privops.run(["ip", "link", "set", bridge, "address", mac])
        privops.run(["ip", "addr", "add", host_ip_cidr, "dev", bridge])
        privops.run(["ip", "link", "set", bridge, "up"])


def delete(bridge: str) -> None:
    if exists(bridge):
        privops.run(["ip", "link", "del", bridge])


def attach(bridge: str, host_veth: str) -> None:
    privops.run(["ip", "link", "set", host_veth, "master", bridge])


def exists(bridge: str) -> bool:
    if privops.backend() == "fake":
        from nyc.client.privops_fake import STATE
        return bridge in STATE["bridges"] or bridge in STATE["links"]
    # Probe the specific device — parsing `ip -o link show` is fragile: the name
    # column carries a trailing ":" (and "@ifN" for some kinds), so substring
    # matching silently fails and ensure() would re-create an existing bridge.
    try:
        privops.run(["ip", "link", "show", bridge])
        return True
    except privops.PrivopsError:
        return False
