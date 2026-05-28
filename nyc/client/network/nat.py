"""Give VMs internet: enable IP forwarding and masquerade VPC traffic out the
node's uplink. Rules live in dedicated `NYC-*` chains for clean teardown.

Intra-VPC traffic (dst inside the VPC CIDR) is NOT masqueraded — only
internet-bound traffic is, so VM-to-VM keeps real source addresses. Idempotent:
every rule is guarded by an `iptables -C` check first.
"""
from nyc.client import privops

NAT, FILTER = "nat", "filter"
POST, FWD = "NYC-POSTROUTING", "NYC-FORWARD"


def ensure(cidr: str) -> None:
    privops.run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    _ensure_chain(NAT, POST)
    _ensure_chain(FILTER, FWD)
    _ensure_rule(NAT, "POSTROUTING", ["-j", POST])
    _ensure_rule(FILTER, "FORWARD", ["-j", FWD])
    _ensure_rule(NAT, POST, ["-s", cidr, "!", "-d", cidr, "-j", "MASQUERADE"])
    _ensure_rule(FILTER, FWD, ["-s", cidr, "-j", "ACCEPT"])
    _ensure_rule(FILTER, FWD, ["-d", cidr, "-j", "ACCEPT"])


def delete(cidr: str) -> None:
    _del_rule(NAT, POST, ["-s", cidr, "!", "-d", cidr, "-j", "MASQUERADE"])
    _del_rule(FILTER, FWD, ["-s", cidr, "-j", "ACCEPT"])
    _del_rule(FILTER, FWD, ["-d", cidr, "-j", "ACCEPT"])


def _ensure_chain(table: str, chain: str) -> None:
    if not _chain_exists(table, chain):
        privops.run(["iptables", "-t", table, "-N", chain])


def _chain_exists(table: str, chain: str) -> bool:
    try:
        privops.run(["iptables", "-t", table, "-nL", chain])
        return True
    except privops.PrivopsError:
        return False


def _ensure_rule(table: str, chain: str, args: list[str]) -> None:
    if not _rule_exists(table, chain, args):
        privops.run(["iptables", "-t", table, "-A", chain, *args])


def _rule_exists(table: str, chain: str, args: list[str]) -> bool:
    try:
        privops.run(["iptables", "-t", table, "-C", chain, *args])
        return True
    except privops.PrivopsError:
        return False


def _del_rule(table: str, chain: str, args: list[str]) -> None:
    if _rule_exists(table, chain, args):
        privops.run(["iptables", "-t", table, "-D", chain, *args])
