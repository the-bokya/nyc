"""Push Caddy config into the proxy guest VM."""
from pathlib import Path

from nyc.client.guest import run as guest_run

_SETUP_SH = (Path(__file__).parent / "setup.sh").read_text()


def setup(ip: str, key: str) -> str:
    return guest_run.run(ip, key, _SETUP_SH)


def reload(ip: str, key: str, caddyfile_text: str) -> str:
    script = (
        "mkdir -p /etc/caddy\n"
        f"cat > /etc/caddy/Caddyfile << 'NYCEOF'\n{caddyfile_text}NYCEOF\n"
        "caddy reload --config /etc/caddy/Caddyfile\n"
    )
    return guest_run.run(ip, key, script)
