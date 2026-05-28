#!/usr/bin/env bash
# Bake /etc/resolv.conf (a single nameserver) into assets/rootfs.ext4 so guest
# VMs can resolve names for internet access.
#
#   ./scripts/inject_resolv.sh            # uses $NYC_VM_DNS or 1.1.1.1
#   ./scripts/inject_resolv.sh 8.8.8.8
#
# Uses `debugfs` (e2fsprogs) — no mount, no sudo; only write permission on the
# rootfs file is needed. Idempotent: removes any prior /etc/resolv.conf (often a
# dangling systemd-resolved symlink in stock images) and writes a static file.
set -euo pipefail

cd "$(dirname "$0")/.."

DNS="${1:-${NYC_VM_DNS:-1.1.1.1}}"
ROOTFS="assets/rootfs.ext4"

[[ -f "$ROOTFS" ]]            || { echo "rootfs missing — run scripts/fetch_artifacts.sh" >&2; exit 1; }
command -v debugfs >/dev/null || { echo "debugfs missing — install e2fsprogs" >&2; exit 1; }

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
printf 'nameserver %s\n' "$DNS" > "$TMP"

debugfs -w -R "rm /etc/resolv.conf" "$ROOTFS" 2>/dev/null || true
debugfs -w -R "write $TMP /etc/resolv.conf" "$ROOTFS"
debugfs -w -R "set_inode_field /etc/resolv.conf mode 0100644" "$ROOTFS"
debugfs -w -R "set_inode_field /etc/resolv.conf uid 0" "$ROOTFS"
debugfs -w -R "set_inode_field /etc/resolv.conf gid 0" "$ROOTFS"

echo "baked 'nameserver $DNS' into $ROOTFS"
