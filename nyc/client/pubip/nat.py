"""1:1 DNAT/SNAT for public IP ↔ VM IP mapping.

DNAT: internet traffic destined for public_ip is redirected to vm_ip.
SNAT: scoped to replies for inbound connections (--ctorigdst public_ip) so the
VM's own outbound traffic (DNS queries, package downloads, ACME challenges)
falls through to the general MASQUERADE and exits on the host's primary IP.
This avoids anti-spoof drops Scaleway applies to outbound traffic sourced from
flexible IPs that aren't the server's primary address.

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


def _snat_rule(public_ip: str, vm_ip: str) -> list[str]:
    return ["-s", vm_ip, "-m", "conntrack", "--ctorigdst", public_ip,
            "-j", "SNAT", "--to-source", public_ip]


def attach(public_ip: str, vm_ip: str) -> None:
    ensure_chains()
    _ensure_rule(NAT, PRE, ["-d", public_ip, "-j", "DNAT", "--to-destination", vm_ip])
    rule = _snat_rule(public_ip, vm_ip)
    if not _rule_exists(NAT, POST, rule):
        privops.run(["iptables", "-t", NAT, "-I", POST, "1", *rule])


def detach(public_ip: str, vm_ip: str) -> None:
    _del_rule(NAT, PRE, ["-d", public_ip, "-j", "DNAT", "--to-destination", vm_ip])
    _del_rule(NAT, POST, _snat_rule(public_ip, vm_ip))
