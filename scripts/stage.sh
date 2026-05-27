#!/usr/bin/env bash
# Spin up N nyc nodes locally and run the end-to-end test suite against them.
#
#   ./scripts/stage.sh [N=3] [--real] [--keep] [--no-tests]
#
# Defaults to NYC_BACKEND=fake (no sudo, no /dev/kvm). Pass --real to flip on
# live firecracker (requires /dev/kvm and passwordless sudo for ip / mkfs.ext4
# / mount / firecracker / kill / truncate / brctl).
#
# Lifecycle: stage.sh ALWAYS tears the cluster down when it exits.
#   default        : start cluster → run e2e tests → tear down → exit.
#   --keep         : start cluster → run e2e tests → BLOCK until Ctrl-C → tear down → exit.
#   --no-tests     : skip the e2e suite (useful combined with --keep).
#
# To poke a kept cluster, run `--keep [--no-tests]` in one terminal and
# `curl :9001/...` or `ssh -i assets/id_ed25519 root@<vm-ip>` from another.
set -euo pipefail

cd "$(dirname "$0")/.."

N="3"
BACKEND="fake"
KEEP=0
RUN_TESTS=1
for arg in "$@"; do
    case "$arg" in
        --real)     BACKEND="real" ;;
        --keep)     KEEP=1 ;;
        --no-tests) RUN_TESTS=0 ;;
        --*)        echo "unknown flag $arg" >&2; exit 2 ;;
        *)          N="$arg" ;;
    esac
done

export NYC_BACKEND="$BACKEND"
export NYC_RECONCILE_INTERVAL="${NYC_RECONCILE_INTERVAL:-3}"

echo "==> NYC_BACKEND=$NYC_BACKEND  N=$N  keep=$KEEP  tests=$RUN_TESTS"

if [[ "$BACKEND" == "real" ]]; then
    [[ -x bin/firecracker ]] || ./scripts/install_firecracker.sh
    [[ -f assets/vmlinux && -f assets/rootfs.ext4 && -f assets/id_ed25519 ]] || ./scripts/fetch_artifacts.sh
    if ! sudo -n /usr/bin/ip -V >/dev/null 2>&1; then
        echo "real mode needs passwordless sudo for ip/mkfs.ext4/mount/umount/firecracker/kill/truncate/brctl."
        echo "install this in /etc/sudoers.d/nyc (via 'sudo visudo -f /etc/sudoers.d/nyc'):"
        echo "  $USER ALL=(root) NOPASSWD: /usr/bin/ip, /usr/bin/mkfs.ext4, /usr/bin/mount, /usr/bin/umount, /usr/bin/truncate, /usr/bin/kill, /usr/bin/brctl, $PWD/bin/firecracker"
        exit 2
    fi
fi

rm -rf stage
mkdir -p stage

BASE_HTTP=9001
BASE_RQ_HTTP=14001
BASE_RQ_RAFT=14002

PIDS=()
JOIN_ADDR="127.0.0.1:${BASE_RQ_RAFT}"

for i in $(seq 1 "$N"); do
    folder="stage/node${i}"
    mkdir -p "$folder/rqlite-data" "$folder/logs"
    http_port=$((BASE_HTTP + i - 1))
    rq_http=$((BASE_RQ_HTTP + (i - 1) * 2))
    rq_raft=$((BASE_RQ_RAFT + (i - 1) * 2))
    cat > "$folder/config.toml" <<EOF
http_port = $http_port
rqlite_http_port = $rq_http
rqlite_raft_port = $rq_raft
EOF
    join_arg=""
    [[ "$i" -gt 1 ]] && join_arg="--join $JOIN_ADDR"
    # setsid + nohup so the node survives shell exit (needed for --keep).
    setsid bash -c "cd '$folder' && nohup uv run --project ../.. dadar run $join_arg > logs/dadar.out 2>&1" < /dev/null &
    PIDS+=($!)
    [[ "$i" -eq 1 ]] && sleep 3
    echo "node${i} http=$http_port rqlite=$rq_http,$rq_raft pid=$!"
done

cleanup() {
    echo "==> stopping nodes"
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    pkill -f rqlited 2>/dev/null || true
    [[ "$BACKEND" == "real" ]] && _purge_nyc_kernel_state
}

_purge_nyc_kernel_state() {
    for br in $(ip -o link show 2>/dev/null | awk -F: '/br-[0-9a-f]{4}-[0-9a-f]{4}/ {gsub(/^ /,"",$2); split($2,a," "); print a[1]}'); do
        sudo -n /usr/bin/ip link del "$br" 2>/dev/null || true
    done
    for ns in $(sudo -n /usr/bin/ip netns list 2>/dev/null | awk '/^vm-[0-9a-f]{8}/ {print $1}'); do
        sudo -n /usr/bin/ip netns del "$ns" 2>/dev/null || true
    done
}

# Always tear down on exit — the cluster lifetime IS the script lifetime.
trap cleanup EXIT
trap 'exit 130' INT TERM

# Wait until every node's /health returns 200.
for i in $(seq 1 "$N"); do
    port=$((BASE_HTTP + i - 1))
    for attempt in $(seq 1 60); do
        if curl -sf "http://127.0.0.1:$port/health" >/dev/null; then
            echo "node$i ready"
            break
        fi
        sleep 0.5
        if [[ "$attempt" -eq 60 ]]; then
            echo "node$i never came up; last log:"
            tail -50 "stage/node${i}/logs/dadar.out" || true
            exit 1
        fi
    done
done

export NYC_STAGE_BASE_PORT="$BASE_HTTP"
export NYC_STAGE_NODES="$N"

if [[ "$RUN_TESTS" -eq 1 ]]; then
    echo "==> running e2e tests"
    if ! uv run pytest -x tests/test_stage_e2e.py -v; then
        for i in $(seq 1 "$N"); do
            echo "==> tail stage/node${i}/logs/dadar.out"
            tail -40 "stage/node${i}/logs/dadar.out" || true
        done
        exit 1
    fi
fi

if [[ "$KEEP" -eq 1 ]]; then
    echo
    echo "==> cluster up (NYC_STAGE_BASE_PORT=$BASE_HTTP, N=$N). Ctrl-C to stop."
    echo "    api:   curl http://127.0.0.1:$BASE_HTTP/nodes | jq"
    echo "    ssh:   ssh -i assets/id_ed25519 -o StrictHostKeyChecking=no root@<vm-ip>"
    # Block until SIGINT/SIGTERM. cleanup() runs from the EXIT trap.
    while true; do sleep 60; done
fi
