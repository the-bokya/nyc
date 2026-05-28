#!/usr/bin/env bash
# Idempotent bare-metal node setup for nyc. Run by scripts/deploy.py over
# `ssh -A`, configured entirely through environment variables (so a single ssh
# invocation carries everything; nothing is positional). Every step is
# check-then-act, so re-running `deploy up` is safe.
#
# Required env (set by deploy.py):
#   REPO_URL REF REMOTE_DIR SSH_USER
#   NODE_NAME NODE_HOST PUBLIC_HOST DOMAIN
#   HTTP_PORT RQLITE_HTTP_PORT RQLITE_RAFT_PORT
#   VPC_CIDR DNS
#   ROLE            = bootstrap | join
#   JOIN_TARGET     = <bootstrap_host>:<raft_port>   (join nodes only)
#   VM_KEY_B64 VM_PUB_B64   = shared VM keypair (base64), distributed by deploy
#   VM_TTL_MINUTES  = auto-delete VMs this many minutes after creation (0 = off)
set -euo pipefail

REMOTE_DIR="${REMOTE_DIR/#\~/$HOME}"
NYC_DIR="$REMOTE_DIR/nyc"
DADAR_DIR="$REMOTE_DIR/dadar"
NODE_FOLDER="$NYC_DIR/node"
CADDYFILE="$NODE_FOLDER/Caddyfile"
SUDOERS=/etc/sudoers.d/nyc
SYSCTL=/etc/sysctl.d/99-nyc.conf

log() { printf '\n[provision %s] %s\n' "$NODE_NAME" "$*"; }

main() {
    preflight
    install_packages
    sync_repo
    uv_sync
    install_binaries
    fetch_and_bake_assets
    enable_ip_forward
    write_sudoers
    init_node
    install_node_service
    install_caddy_service
    log "provision complete"
}

preflight() {
    log "preflight"
    [ -e /dev/kvm ] || { echo "FATAL: /dev/kvm missing"; exit 1; }
    [ "$(uname -m)" = x86_64 ] || { echo "FATAL: arch $(uname -m) != x86_64"; exit 1; }
    sudo -n true || { echo "FATAL: passwordless sudo required"; exit 1; }
}

install_packages() {
    log "apt packages + uv + caddy"
    mkdir -p "$NODE_FOLDER"
    dpkg -l | awk '/^ii/{print $2}' | sort >"$NODE_FOLDER/.pre_pkgs" || true
    sudo -n apt-get update -y
    sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y \
        git curl e2fsprogs iproute2 iptables ca-certificates
    command -v uv >/dev/null 2>&1 || curl -fsSL https://astral.sh/uv/install.sh | sh
    install_caddy
    # add the login user to kvm (firecracker runs via sudo, but keep it tidy)
    sudo -n usermod -aG kvm "$SSH_USER" 2>/dev/null || true
}

install_caddy() {
    # Static binary from Caddy's official endpoint — a fresh LTS codename may
    # not be in the apt repo yet (REBUILD 0.2).
    command -v caddy >/dev/null 2>&1 && return
    log "installing caddy (static binary)"
    local tmp; tmp="$(mktemp)"
    curl -fsSL -o "$tmp" "https://caddyserver.com/api/download?os=linux&arch=amd64"
    sudo -n install -m 0755 "$tmp" /usr/bin/caddy
    rm -f "$tmp"
}

sync_repo() {
    log "sync repo @ $REF"
    if [ -d "$REMOTE_DIR/.git" ]; then
        git -C "$REMOTE_DIR" fetch --recurse-submodules origin
        git -C "$REMOTE_DIR" checkout "$REF"
        git -C "$REMOTE_DIR" submodule update --init --recursive
    else
        git clone --recurse-submodules "$REPO_URL" "$REMOTE_DIR"
        git -C "$REMOTE_DIR" checkout "$REF"
        git -C "$REMOTE_DIR" submodule update --init --recursive
    fi
}

uv_sync() {
    log "uv sync dadar + nyc"
    ( cd "$DADAR_DIR" && "$(uv_bin)" sync )
    ( cd "$NYC_DIR" && "$(uv_bin)" sync )
}

install_binaries() {
    log "firecracker + rqlited"
    ( cd "$NYC_DIR" && ./scripts/install_firecracker.sh )
    ( cd "$DADAR_DIR" && ./scripts/install_rqlite.sh )
}

fetch_and_bake_assets() {
    log "fetch artifacts + distribute shared key + bake rootfs"
    ( cd "$NYC_DIR" && ./scripts/fetch_artifacts.sh )
    if [ -n "${VM_KEY_B64:-}" ]; then
        printf '%s' "$VM_KEY_B64" | base64 -d >"$NYC_DIR/assets/id_ed25519"
        printf '%s' "$VM_PUB_B64" | base64 -d >"$NYC_DIR/assets/id_ed25519.pub"
        chmod 600 "$NYC_DIR/assets/id_ed25519"
    fi
    bake_rootfs
}

bake_rootfs() {
    # Bake the shared pubkey + resolv.conf into the base rootfs so VMs created
    # via plain `POST /vms` (no per-VM key layer) are reachable for the ssh-jump
    # deliverable. spawn_vm still layers its own per-VM key on the copy.
    local pub; pub="$(cat "$NYC_DIR/assets/id_ed25519.pub")"
    local cmds; cmds="$(mktemp)"
    local ak; ak="$(mktemp)"; printf '%s\n' "$pub" >"$ak"
    local rc; rc="$(mktemp)"; printf 'nameserver %s\n' "$DNS" >"$rc"
    {
        echo "mkdir /root/.ssh"
        echo "rm /root/.ssh/authorized_keys"
        echo "write $ak /root/.ssh/authorized_keys"
        echo "set_inode_field /root/.ssh/authorized_keys mode 0100600"
        echo "set_inode_field /root/.ssh mode 040700"
        echo "rm /etc/resolv.conf"
        echo "write $rc /etc/resolv.conf"
        echo "set_inode_field /etc/resolv.conf mode 0100644"
    } >"$cmds"
    debugfs -w -f "$cmds" "$NYC_DIR/assets/rootfs.ext4" || true
    rm -f "$cmds" "$ak" "$rc"
}

enable_ip_forward() {
    log "ip_forward"
    [ -f "$NODE_FOLDER/.pre_ip_forward" ] || \
        cat /proc/sys/net/ipv4/ip_forward >"$NODE_FOLDER/.pre_ip_forward"
    echo "net.ipv4.ip_forward=1" | sudo -n tee "$SYSCTL" >/dev/null
    sudo -n sysctl --system >/dev/null
}

write_sudoers() {
    log "sudoers"
    local fc="$NYC_DIR/bin/firecracker"
    local tmp; tmp="$(mktemp)"
    cat >"$tmp" <<EOF
$SSH_USER ALL=(root) NOPASSWD: /usr/sbin/ip,/usr/bin/ip,/usr/sbin/iptables,/usr/bin/iptables,/usr/sbin/sysctl,/usr/sbin/bridge,/usr/bin/bridge,/sbin/mkfs.ext4,/usr/sbin/mkfs.ext4,/usr/bin/mount,/usr/bin/umount,/usr/bin/truncate,/usr/bin/kill,$fc
EOF
    sudo -n visudo -cf "$tmp"
    sudo -n install -m 0440 "$tmp" "$SUDOERS"
    rm -f "$tmp"
}

init_node() {
    log "dadar init"
    ( cd "$NODE_FOLDER" && "$(uv_bin)" run --project "$NYC_DIR" dadar init \
        --host "$NODE_HOST" --public-host "$PUBLIC_HOST" --domain "$DOMAIN" \
        --http-port "$HTTP_PORT" --rqlite-http-port "$RQLITE_HTTP_PORT" \
        --rqlite-raft-port "$RQLITE_RAFT_PORT" )
}

install_node_service() {
    log "nyc-node.service ($ROLE)"
    local mode="--bootstrap"
    [ "$ROLE" = join ] && mode="--join $JOIN_TARGET"
    local tmp; tmp="$(mktemp)"
    cat >"$tmp" <<EOF
[Unit]
Description=nyc dadar node
After=network-online.target
Wants=network-online.target

[Service]
User=$SSH_USER
WorkingDirectory=$NODE_FOLDER
ExecStart=/usr/bin/env $(uv_bin) run --project $NYC_DIR dadar run $mode
Restart=on-failure
Environment=NYC_BACKEND=real
Environment=NYC_VM_TTL_MINUTES=${VM_TTL_MINUTES:-0}

[Install]
WantedBy=multi-user.target
EOF
    sudo -n install -m 0644 "$tmp" /etc/systemd/system/nyc-node.service
    rm -f "$tmp"
    sudo -n systemctl daemon-reload
    sudo -n systemctl enable --now nyc-node.service
}

install_caddy_service() {
    log "nyc-caddy.service"
    printf '%s {\n    reverse_proxy %s:%s\n}\n' "$DOMAIN" "$NODE_HOST" "$HTTP_PORT" >"$CADDYFILE"
    local tmp; tmp="$(mktemp)"
    cat >"$tmp" <<EOF
[Unit]
Description=nyc caddy TLS front-end
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/caddy run --config $CADDYFILE --adapter caddyfile
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
    sudo -n install -m 0644 "$tmp" /etc/systemd/system/nyc-caddy.service
    rm -f "$tmp"
    sudo -n systemctl daemon-reload
    sudo -n systemctl enable --now nyc-caddy.service
}

uv_bin() { command -v uv || echo "$HOME/.local/bin/uv"; }

main "$@"
