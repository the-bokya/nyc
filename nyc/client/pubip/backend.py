"""Dispatch to the appropriate public-IP backend (scaleway|static)."""
from nyc.client.pubip.backends import scaleway, static
from nyc.config import PubipConfig

_BACKENDS = {
    "scaleway": scaleway,
    "static": static,
}


def acquire(cfg: PubipConfig, used: set[str]) -> tuple[str, str | None, str, str]:
    """Pick a free address; returns (address, gateway, iface, provider)."""
    backend = _BACKENDS.get(cfg.provider)
    if backend is None:
        raise ValueError(f"unknown pubip provider: {cfg.provider}")
    return backend.acquire(cfg, used)


def release(cfg: PubipConfig, address: str) -> None:
    backend = _BACKENDS.get(cfg.provider)
    if backend is not None:
        backend.release(cfg, address)
