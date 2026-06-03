#!/usr/bin/env bash
# Idempotent reverse of provision.sh. Run by scripts/deploy.py over `ssh -A`.
# Returns the host to its pre-`up` config; `--purge` additionally removes the
# packages we installed and the checkout. Every step tolerates already-gone
# state, so a partial earlier run never blocks teardown.
#
# Required env: REMOTE_DIR SSH_USER   (PURGE=1 to also remove packages + checkout
#   + the LVM vg/pv on LVM_DEVICE; LVM_VG/LVM_DEVICE name what to wipe)
set -uo pipefail

REMOTE_DIR="${REMOTE_DIR/#\~/$HOME}"
NYC_DIR="$REMOTE_DIR/nyc"
NODE_FOLDER="$NYC_DIR/node"
SUDOERS=/etc/sudoers.d/nyc
SYSCTL=/etc/sysctl.d/99-nyc.conf
STATE_DIR="$HOME/.nyc"   # pre-`up` snapshots (mirrors provision.sh)

log() { printf '\n[teardown] %s\n' "$*"; }

main() {
    stop_services
    purge_lvm_if_requested        # --purge only: VMs' data lives here
    purge_kernel_links
    purge_iptables
    restore_ip_forward            # reads pre_ip_forward
    purge_packages_if_requested   # reads pre_pkgs
    remove_node_folder
    remove_sudoers
    remove_state_dir              # last: both snapshots consumed
    log "teardown complete"
}

stop_services() {
    log "stop services"
    for unit in nyc-node.service nyc-caddy.service; do
        sudo -n systemctl disable --now "$unit" 2>/dev/null
        sudo -n rm -f "/etc/systemd/system/$unit"
    done
    sudo -n systemctl daemon-reload
}

purge_lvm_if_requested() {
    # nyc owns the device, so the VG holds VM data — only wipe it on --purge,
    # so a plain `down` (then `up`) preserves volumes. Idempotent / tolerant.
    [ "${PURGE:-0}" = 1 ] && [ -n "${LVM_VG:-}" ] || return 0
    log "--purge: remove LVM vg ${LVM_VG}"
    sudo -n vgchange -an "$LVM_VG" 2>/dev/null
    sudo -n vgremove -f -y "$LVM_VG" 2>/dev/null
    [ -n "${LVM_DEVICE:-}" ] && sudo -n pvremove -ff -y "$LVM_DEVICE" 2>/dev/null
    return 0
}

purge_kernel_links() {
    log "purge netns + links (anchored regexes)"
    ip netns list 2>/dev/null | awk '{print $1}' | grep -E '^vm-[0-9a-f]{8}$' | \
        while read -r ns; do sudo -n ip netns del "$ns" 2>/dev/null; done
    ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d'@' -f1 | \
        grep -E '^(br-[0-9a-f]{4}-[0-9a-f]{4}|vx-[0-9a-f]{4}-[0-9a-f]{4}|vm[hn]-[0-9a-f]{8})$' | \
        while read -r dev; do sudo -n ip link del "$dev" 2>/dev/null; done
}

purge_iptables() {
    log "purge iptables chains"
    sudo -n iptables -t nat -D POSTROUTING -j NYC-POSTROUTING 2>/dev/null
    sudo -n iptables -D FORWARD -j NYC-FORWARD 2>/dev/null
    sudo -n iptables -t nat -F NYC-POSTROUTING 2>/dev/null
    sudo -n iptables -t nat -X NYC-POSTROUTING 2>/dev/null
    sudo -n iptables -F NYC-FORWARD 2>/dev/null
    sudo -n iptables -X NYC-FORWARD 2>/dev/null
}

restore_ip_forward() {
    log "restore ip_forward"
    if [ -f "$STATE_DIR/pre_ip_forward" ]; then
        sudo -n sysctl -w "net.ipv4.ip_forward=$(cat "$STATE_DIR/pre_ip_forward")" >/dev/null
    fi
    sudo -n rm -f "$SYSCTL"
    sudo -n sysctl --system >/dev/null 2>&1
}

purge_packages_if_requested() {
    [ "${PURGE:-0}" = 1 ] || return 0
    log "--purge: remove packages we installed"
    local pre="$STATE_DIR/pre_pkgs"
    [ -f "$pre" ] || return 0
    local added; added="$(comm -13 "$pre" <(dpkg -l | awk '/^ii/{print $2}' | sort))"
    # only ever remove from the known nyc install set, intersected with what we added
    local known="git curl e2fsprogs iproute2 iptables ca-certificates"
    local rm=""
    for p in $known; do echo "$added" | grep -qx "$p" && rm="$rm $p"; done
    [ -n "$rm" ] && sudo -n DEBIAN_FRONTEND=noninteractive apt-get remove -y $rm || true
}

remove_node_folder() {
    log "rm node folder"
    rm -rf "$NODE_FOLDER"
    if [ "${PURGE:-0}" = 1 ]; then
        log "--purge: rm checkout $REMOTE_DIR"
        rm -rf "$REMOTE_DIR"
    fi
}

remove_sudoers() {
    log "rm sudoers"
    sudo -n rm -f "$SUDOERS"
}

remove_state_dir() {
    rm -rf "$STATE_DIR"
}

main "$@"
