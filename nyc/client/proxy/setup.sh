#!/bin/bash
# Install Caddy via static binary in the proxy guest VM. Idempotent.
set -euo pipefail

CADDY_VERSION="2.8.4"
CADDY_BIN="/usr/local/bin/caddy"
CADDY_CFG="/etc/caddy/Caddyfile"

if ! command -v caddy &>/dev/null; then
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  goarch="amd64" ;;
        aarch64) goarch="arm64" ;;
        *)       echo "unsupported arch: $arch"; exit 1 ;;
    esac
    url="https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${goarch}.tar.gz"
    tmp="$(mktemp -d)"
    curl -fsSL "$url" | tar -xz -C "$tmp"
    install -m 0755 "$tmp/caddy" "$CADDY_BIN"
    rm -rf "$tmp"
fi

mkdir -p /etc/caddy

if [ ! -f "$CADDY_CFG" ]; then
    cat > "$CADDY_CFG" <<'EOF'
# managed by nyc
EOF
fi

if [ ! -f /etc/systemd/system/caddy.service ]; then
    cat > /etc/systemd/system/caddy.service <<'EOF'
[Unit]
Description=Caddy reverse proxy
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile
Restart=on-failure
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable caddy
fi

systemctl is-active caddy &>/dev/null || systemctl start caddy
