"""Scaleway flexible IP backend.

The IP is already attached to the Elastic Metal server at the provider level.
acquire() picks a free address from the node's pool declared in cluster.toml.
release() is a no-op against Scaleway (the IP stays on the server).

# TODO: optional flexible-ip API order/attach via
#   POST https://api.scaleway.com/flexible-ip/v1alpha1/zones/<zone>/fips/attach
#   headers: X-Auth-Token: <SCW_SECRET_KEY>
#   body: {"fips_ids": [...], "server_id": "<server_id>"}
"""
from nyc.config import PubipConfig


def acquire(cfg: PubipConfig, used: set[str]) -> tuple[str, str | None, str, str]:
    """Return (address, gateway, iface, provider). Raises if pool exhausted."""
    free = [a for a in cfg.addresses if a not in used]
    if not free:
        raise RuntimeError("no free public IPs in scaleway pool for this node")
    return free[0], cfg.gateway, cfg.iface, "scaleway"


def release(_cfg: PubipConfig, _address: str) -> None:
    pass
