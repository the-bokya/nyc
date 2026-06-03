"""Render a Caddyfile for the reverse-proxy VM.

Caddy automatic HTTPS is on by default — no extra directives needed.
Each route is one `fqdn { reverse_proxy ip:port }` block.
"""


def render(routes: list[tuple[str, str, int]]) -> str:
    """routes: list of (fqdn, vm_ip, port)"""
    if not routes:
        return ""
    blocks = [f"{fqdn} {{\n\treverse_proxy {ip}:{port}\n}}" for fqdn, ip, port in routes]
    return "\n\n".join(blocks) + "\n"
