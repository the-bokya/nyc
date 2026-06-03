"""1:1 DNAT/SNAT for public IP ↔ VM IP mapping.

DNAT: internet traffic destined for public_ip is redirected to vm_ip.
SNAT: traffic from vm_ip exits on public_ip (inserted before the general
MASQUERADE rule so the specific SNAT wins).

Rules live in dedicated NYC-PREROUTING chain (joined from PREROUTING) and
reuse the existing NYC-POSTROUTING chain. Same _ensure_rule/_rule_exists
idempotency pattern as nyc/client/network/nat.py.
"""
from nyc.client import privops
from nyc.client.network.nat import (
    NAT, POST,
    _ensure_chain, _ensure_rule, _rule_exists, _del_rule,
)

PRE = "NYC-PREROUTING"


def ensure_chains() -> None:
    _ensure_chain(NAT, PRE)
    _ensure_rule(NAT, "PREROUTING", ["-j", PRE])


def attach(public_ip: str, vm_ip: str) -> None:
    ensure_chains()
    _ensure_rule(NAT, PRE, ["-d", public_ip, "-j", "DNAT", "--to-destination", vm_ip])
    # Insert SNAT before the general MASQUERADE so the VM egresses on its public IP.
    if not _rule_exists(NAT, POST, ["-s", vm_ip, "-j", "SNAT", "--to-source", public_ip]):
        privops.run(["iptables", "-t", NAT, "-I", POST, "1",
                     "-s", vm_ip, "-j", "SNAT", "--to-source", public_ip])


def detach(public_ip: str, vm_ip: str) -> None:
    _del_rule(NAT, PRE, ["-d", public_ip, "-j", "DNAT", "--to-destination", vm_ip])
    _del_rule(NAT, POST, ["-s", vm_ip, "-j", "SNAT", "--to-source", public_ip])
