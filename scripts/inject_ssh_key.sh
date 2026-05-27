#!/usr/bin/env bash
# Inject a public ssh key into assets/rootfs.ext4 so VMs accept it as root.
#
#   ./scripts/inject_ssh_key.sh                  # uses assets/id_ed25519.pub
#   ./scripts/inject_ssh_key.sh ~/.ssh/foo.pub   # use a different key
#
# Idempotent: the key is appended to existing /root/.ssh/authorized_keys with
# duplicates removed. Existing keys (e.g. firecracker CI's stock key) survive.
# Also forces PermitRootLogin to a deterministic value.
#
# Uses `debugfs` (part of e2fsprogs) so no mount and no sudo are required —
# you only need write permission on the rootfs file.
set -euo pipefail

cd "$(dirname "$0")/.."

PUBKEY_PATH="${1:-assets/id_ed25519.pub}"
ROOTFS="assets/rootfs.ext4"

[[ -f "$PUBKEY_PATH" ]] || { echo "pubkey not found: $PUBKEY_PATH" >&2; exit 1; }
[[ -f "$ROOTFS" ]]      || { echo "rootfs missing — run scripts/fetch_artifacts.sh" >&2; exit 1; }
command -v debugfs >/dev/null || { echo "debugfs missing — install e2fsprogs" >&2; exit 1; }

PUBKEY="$(cat "$PUBKEY_PATH")"
echo "==> injecting $(basename "$PUBKEY_PATH") into $ROOTFS"

EXISTING="$(mktemp)"
STAGED="$(mktemp)"
trap 'rm -f "$EXISTING" "$STAGED"' EXIT

# Dump existing authorized_keys (may not exist yet); strip any prior copy of
# this exact key; then append it. That keeps the file deterministic across
# re-runs and preserves any keys baked in by upstream rootfs builders.
debugfs -R "cat /root/.ssh/authorized_keys" "$ROOTFS" 2>/dev/null > "$EXISTING" || true
{ grep -vxF "$PUBKEY" "$EXISTING" || true; echo "$PUBKEY"; } > "$STAGED"

debugfs -w -R "mkdir /root/.ssh"               "$ROOTFS" 2>/dev/null || true
debugfs -w -R "rm    /root/.ssh/authorized_keys" "$ROOTFS" 2>/dev/null || true
debugfs -w -R "write $STAGED /root/.ssh/authorized_keys" "$ROOTFS"
debugfs -w -R "set_inode_field /root/.ssh/authorized_keys mode 0100600" "$ROOTFS"
debugfs -w -R "set_inode_field /root/.ssh/authorized_keys uid 0" "$ROOTFS"
debugfs -w -R "set_inode_field /root/.ssh/authorized_keys gid 0" "$ROOTFS"
debugfs -w -R "set_inode_field /root/.ssh mode 040700" "$ROOTFS"

# Force PermitRootLogin to a deterministic value (default differs across rootfs revs).
SSHD_TMP="$(mktemp)"
debugfs -R "dump /etc/ssh/sshd_config $SSHD_TMP" "$ROOTFS" 2>/dev/null || true
if [[ -s "$SSHD_TMP" ]]; then
    sed -i 's/^#*PermitRootLogin .*/PermitRootLogin prohibit-password/' "$SSHD_TMP"
    debugfs -w -R "rm /etc/ssh/sshd_config" "$ROOTFS" 2>/dev/null || true
    debugfs -w -R "write $SSHD_TMP /etc/ssh/sshd_config" "$ROOTFS"
    debugfs -w -R "set_inode_field /etc/ssh/sshd_config mode 0100644" "$ROOTFS"
fi
rm -f "$SSHD_TMP"

PRIVKEY="${PUBKEY_PATH%.pub}"
KEY_COUNT="$(wc -l < "$STAGED")"
echo "done — authorized_keys now has $KEY_COUNT key(s)."
echo "ssh in: ssh -i $PRIVKEY -o StrictHostKeyChecking=no root@<vm-ip>"
