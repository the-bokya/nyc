"""Bind/unbind a public IP address on the host's network interface.

Idempotent: `ip addr show` guards the add so re-running is safe.
"""
from nyc.client import privops


def bind(address: str, iface: str) -> None:
    if not _bound(address, iface):
        privops.run(["ip", "addr", "add", f"{address}/32", "dev", iface])


def unbind(address: str, iface: str) -> None:
    if _bound(address, iface):
        privops.run(["ip", "addr", "del", f"{address}/32", "dev", iface])


def _bound(address: str, iface: str) -> bool:
    out = privops.run(["ip", "addr", "show", "dev", iface])
    return f"{address}/32" in out
