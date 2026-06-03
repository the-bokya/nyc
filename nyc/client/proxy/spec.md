# client/proxy

Caddy reverse-proxy guest management.

## Files

| File | Purpose |
|---|---|
| `setup.sh`    | Shell script templated into a guest VM to install Caddy via static binary, write a minimal bootstrap Caddyfile, install + enable `caddy.service`, start Caddy. Idempotent (check-then-act). |
| `caddyfile.py`| `render(routes) -> str` — produces one `fqdn { reverse_proxy ip:port }` block per route. Caddy automatic HTTPS is on by default (no extra directives). |
| `push.py`     | `setup(ip, key)` runs `setup.sh` in the guest; `reload(ip, key, caddyfile_text)` writes the Caddyfile and calls `caddy reload`. Both via `client.guest.run`. |

## Design

- Caddy is installed as a **static binary** (not via apt) for reliability on
  minimal images. Same approach as `scripts/provision.py`'s host Caddy install.
- `push.reload` writes the Caddyfile atomically then reloads Caddy in-process
  (`caddy reload --config ...`), so routes are never interrupted.
- The proxy VM is a plain VPC guest VM — it gets a public IP via `pubip_nat`
  DNAT/SNAT on its owner node, and reaches target VMs on any node via the VXLAN
  overlay by their private IPs.
- Guest SSH uses the shared `assets/id_ed25519` key (baked into every rootfs).
