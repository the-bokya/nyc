"""Static pool: pick a free entry from the node's declared public-IP pool.

Pool entries are operator-declared in cluster.toml (address + provider-registered MAC).
acquire() returns the first unused entry; release() is a no-op (IPs stay on the server).
"""
from nyc.config import PubipConfig


def acquire(cfg: PubipConfig, used: set[str]) -> tuple[str, str, str, str]:
    """Return (address, gateway, mac, prefix). Raises if pool exhausted."""
    free = [e for e in cfg.ips if e.address not in used]
    if not free:
        raise RuntimeError("no free public IPs in pool for this node")
    e = free[0]
    return e.address, cfg.gateway or "", e.mac, e.prefix


def release(_cfg: PubipConfig, _address: str) -> None:
    pass
