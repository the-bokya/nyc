#!/usr/bin/env bash
# Idempotently download the firecracker binary to bin/firecracker.
# Pinned version below; bump deliberately.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p bin

VERSION="v1.7.0"
ARCH="$(uname -m)"
TARGET="bin/firecracker"

if [[ -x "$TARGET" ]]; then
    echo "firecracker already at $TARGET"; exit 0
fi

URL="https://github.com/firecracker-microvm/firecracker/releases/download/${VERSION}/firecracker-${VERSION}-${ARCH}.tgz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "downloading firecracker ${VERSION} (${ARCH})…"
curl -fsSL -o "$TMP/fc.tgz" "$URL"
tar -xzf "$TMP/fc.tgz" -C "$TMP"
cp "$TMP/release-${VERSION}-${ARCH}/firecracker-${VERSION}-${ARCH}" "$TARGET"
chmod +x "$TARGET"
echo "installed $TARGET ($($TARGET --version 2>&1 | head -1))"
