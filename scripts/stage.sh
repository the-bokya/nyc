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
        echo "real mode needs passwordless sudo for ip/bridge/iptables/sysctl/mount/umount/kill/firecracker,"
        echo "the LVM toolchain (lvm/pv*/vg*/lv*/losetup/dmsetup) for the loopback-backed volume group,"
        echo "and mkfs.ext4/debugfs/dd/resize2fs against root-owned LV device nodes."
        echo "(Only truncate — the loopback backing file — stays unprivileged.)"
        echo "install this in /etc/sudoers.d/nyc (via 'sudo visudo -f /etc/sudoers.d/nyc'):"
        echo "  $USER ALL=(root) NOPASSWD: /usr/bin/ip, /usr/sbin/bridge, /usr/sbin/iptables, /usr/sbin/sysctl, /usr/bin/mount, /usr/bin/umount, /usr/bin/kill, /usr/sbin/lvm, /usr/sbin/pvcreate, /usr/sbin/pvremove, /usr/sbin/pvs, /usr/sbin/vgcreate, /usr/sbin/vgremove, /usr/sbin/vgchange, /usr/sbin/vgs, /usr/sbin/lvcreate, /usr/sbin/lvremove, /usr/sbin/lvchange, /usr/sbin/lvextend, /usr/sbin/lvs, /usr/sbin/losetup, /usr/sbin/dmsetup, /usr/bin/dd, /usr/sbin/mkfs.ext4, /usr/sbin/debugfs, /usr/sbin/resize2fs, $PWD/bin/firecracker"
        exit 2
    fi
fi

# Reclaim loop devices + their VGs whose backing file is under THIS repo's
# stage/ dir. Keyed off losetup (not the file), so it also reclaims a prior
# SIGKILL'd run whose backing files are already gone (losetup shows "(deleted)").
# Touches only loops pointing into our stage/ — nothing else on the box. Defined
# here so both the startup sweep below and cleanup() (at EXIT) can call it.
_purge_nyc_lvm() {
    local stagedir="$PWD/stage"
    sudo -n /usr/sbin/losetup -l -O NAME,BACK-FILE 2>/dev/null | while read -r loop back _; do
        case "$back" in "$stagedir"/*) ;; *) continue ;; esac
        for vg in $(sudo -n /usr/sbin/pvs --noheadings -o vg_name "$loop" 2>/dev/null); do
            sudo -n /usr/sbin/vgchange -an "$vg" 2>/dev/null || true
            sudo -n /usr/sbin/vgremove -f -y "$vg" 2>/dev/null || true
        done
        sudo -n /usr/sbin/pvremove -ff -y "$loop" 2>/dev/null || true
        sudo -n /usr/sbin/losetup -d "$loop" 2>/dev/null || true
    done
}

# A prior run killed with SIGKILL never ran its EXIT trap → leaked loops/VGs.
# Reclaim ours before wiping stage/ (otherwise the backing files vanish and the
# loops can never be matched again).
[[ "$BACKEND" == "real" ]] && _purge_nyc_lvm

rm -rf stage 2>/dev/null || true
if [[ -e stage ]]; then
    echo "stage/ has files this user can't remove — likely root-owned leftovers" >&2
    echo "from running this script under sudo. nyc itself no longer creates" >&2
    echo "root-owned files. Clear it once with:  sudo rm -rf stage" >&2
    exit 1
fi
mkdir -p stage

BASE_HTTP=9001
BASE_RQ_HTTP=14001
BASE_RQ_RAFT=14002

PIDS=()
JOIN_ADDR="127.0.0.1:${BASE_RQ_RAFT}"

# Fail fast if a previous run leaked a node that still holds one of our ports.
# Otherwise the readiness probe greets the stale zombie, the e2e suite runs
# against a half-dead cluster, and DB writes hang for minutes.
for i in $(seq 1 "$N"); do
    for p in $((BASE_HTTP + i - 1)) $((BASE_RQ_HTTP + (i - 1) * 2)) $((BASE_RQ_RAFT + (i - 1) * 2)); do
        if ss -ltn 2>/dev/null | grep -qE "[:.]${p}([^0-9]|$)"; then
            echo "port $p already in use — a previous staging node likely leaked:" >&2
            ss -ltnp 2>/dev/null | grep -E "[:.]${p}([^0-9]|$)" >&2 || true
            echo "stop it and retry, e.g.:  pkill -f 'dadar run'; pkill -f rqlited" >&2
            exit 1
        fi
    done
done

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
    local rc=$?   # preserve the real exit status (0 on success, 1 on test fail)
    echo "==> stopping nodes"
    # Target the node processes by their distinctive cmdlines. A plain
    # `kill $pid` on the setsid wrapper does NOT take down the detached
    # uv → python → rqlited tree (bash doesn't forward the signal), so the
    # node leaks and squats its ports. Killing the python `dadar run` parent
    # also reaps its (often defunct) rqlited child. We deliberately do NOT
    # process-group-kill: setsid does not reliably isolate the node into its
    # own group here, so a negative-pid kill can take out this script too.
    pkill -TERM -f "dadar run" 2>/dev/null || true
    pkill -TERM -f rqlited 2>/dev/null || true
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    if [[ "$BACKEND" == "real" ]]; then
        # firecracker runs as root (sudo) and holds the rootfs/data LV devices
        # OPEN — until it exits, vgremove can't drop the VG. pgrep sees root
        # procs unprivileged; `kill` is already in the sudoers set (pkill isn't).
        local fcs; fcs="$(pgrep -f "$PWD/bin/firecracker" 2>/dev/null)" || true
        [[ -n "$fcs" ]] && sudo -n /usr/bin/kill -TERM $fcs 2>/dev/null || true
        sleep 1  # let firecracker exit and release the LV devices before vgremove
        _purge_nyc_kernel_state
        _purge_nyc_lvm
    fi
    exit "$rc"
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
