#!/usr/bin/env bash
# Idempotently download a stock kernel + bootable ext4 rootfs + ssh key.
# Sources: firecracker-microvm CI assets (publicly hosted on S3).
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p assets

KERNEL="assets/vmlinux"
ROOTFS="assets/rootfs.ext4"
KEY="assets/id_ed25519"

ARCH="$(uname -m)"
BASE="https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.10/${ARCH}"

if [[ ! -f "$KERNEL" ]]; then
    echo "fetching kernel…"
    curl -fsSL -o "$KERNEL" "${BASE}/vmlinux-5.10.223"
fi

if [[ ! -f "$ROOTFS" ]]; then
    echo "fetching rootfs ext4 (~300MB)…"
    curl -fsSL -o "$ROOTFS" "${BASE}/ubuntu-22.04.ext4"
fi

if [[ ! -f "$KEY" ]]; then
    echo "generating ssh keypair…"
    ssh-keygen -t ed25519 -N "" -f "$KEY" -q
fi

# DNS and SSH keys are delivered per-VM via cloud-init seed disk; no rootfs patching needed.

echo "assets ready in assets/"
ls -lh "$KERNEL" "$ROOTFS" "$KEY" "$KEY.pub"
