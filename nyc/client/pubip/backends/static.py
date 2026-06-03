"""Static public IP backend.

Same pool semantics as Scaleway but marked provider='static'.
"""
from nyc.config import PubipConfig


def acquire(cfg: PubipConfig, used: set[str]) -> tuple[str, str | None, str, str]:
    free = [a for a in cfg.addresses if a not in used]
    if not free:
        raise RuntimeError("no free public IPs in static pool for this node")
    return free[0], cfg.gateway, cfg.iface, "static"


def release(_cfg: PubipConfig, _address: str) -> None:
    pass
