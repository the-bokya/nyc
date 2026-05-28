#!/usr/bin/env python3
"""Preflight: verify bare-metal nodes satisfy nyc's constraints. Read-only —
mutates nothing. Run this BEFORE deploy to shrink the unknowns.

  python3 scripts/preflight.py cluster.toml      # probe every node over ssh -A
  python3 scripts/preflight.py --local cluster.toml   # run checks on THIS box
  python3 scripts/preflight.py --print-script    # dump the remote check script

Connects with SSH agent forwarding (-A) so the git-access check exercises the
exact path the deploy uses. Exits non-zero if any node has a FAIL.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import subprocess
import sys
import tomllib
from pathlib import Path

# Remote, read-only probe. Emits `name|STATUS|detail` lines (PASS/WARN/FAIL/INFO).
# args: REPO_URL HTTP RQHTTP RQRAFT DOMAIN PUBLIC_HOST [PEER_HOST ...]
REMOTE_CHECK = r'''
REPO_URL="$1"; HTTP="$2"; RQHTTP="$3"; RQRAFT="$4"; DOMAIN="$5"; PUBLIC_HOST="$6"; shift 6; PEERS=("$@")
emit(){ printf '%s|%s|%s\n' "$1" "$2" "$3"; }

if [ -r /etc/os-release ]; then . /etc/os-release
  major=${VERSION_ID%%.*}
  if [ "$ID" = ubuntu ] && [ "${major:-0}" -ge 24 ] 2>/dev/null; then emit os PASS "$PRETTY_NAME"
  elif [ "$ID" = ubuntu ]; then emit os WARN "ubuntu $VERSION_ID (want >= 24.04)"
  else emit os FAIL "${PRETTY_NAME:-unknown} (expected Ubuntu >= 24.04)"; fi
else emit os FAIL "no /etc/os-release"; fi

a=$(uname -m); [ "$a" = x86_64 ] && emit arch PASS "$a" || emit arch FAIL "$a (need x86_64)"

if [ -e /dev/kvm ]; then
  { [ -r /dev/kvm ] && [ -w /dev/kvm ]; } && emit kvm PASS "/dev/kvm rw" || emit kvm WARN "/dev/kvm not rw for $(id -un) (ok: fc runs via sudo; deploy adds kvm group)"
else emit kvm FAIL "no /dev/kvm (need KVM)"; fi
grep -Eq '(vmx|svm)' /proc/cpuinfo && emit virt PASS "hw virt flag" || emit virt WARN "no vmx/svm in cpuinfo"

sudo -n true 2>/dev/null && emit sudo PASS "passwordless sudo" || emit sudo FAIL "sudo -n failed"

if timeout 6 curl -fsS -o /dev/null https://1.1.1.1 2>/dev/null; then emit internet PASS "https egress ok"
elif getent hosts github.com >/dev/null 2>&1; then emit internet WARN "dns ok but https blocked"
else emit internet FAIL "no internet egress (VMs+apt+git need it)"; fi
getent hosts github.com >/dev/null 2>&1 && emit dns PASS "resolves names" || emit dns FAIL "no DNS resolution"

if [ -n "$REPO_URL" ]; then
  if GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=8' \
      timeout 25 git ls-remote "$REPO_URL" HEAD >/dev/null 2>&1; then emit git PASS "repo reachable via forwarded agent"
  else emit git FAIL "git ls-remote failed (agent fwd / repo access)"; fi
fi

for p in "${PEERS[@]}"; do
  if timeout 3 bash -c "exec 3<>/dev/tcp/$p/22" 2>/dev/null; then emit "peer:$p" PASS "tcp/22 reachable"
  else emit "peer:$p" FAIL "unreachable on private net"; fi
done

listening=$(ss -ltnH 2>/dev/null | awk '{print $4}')
for pp in "$HTTP" "$RQHTTP" "$RQRAFT"; do
  echo "$listening" | grep -qE "[:.]$pp\$" && emit "port:$pp" WARN "already in use" || emit "port:$pp" PASS "free"
done

if [ -n "$DOMAIN" ]; then
  ips=$(getent hosts "$DOMAIN" | awk '{print $1}')
  if echo "$ips" | grep -qx "$PUBLIC_HOST"; then emit domain PASS "$DOMAIN -> $PUBLIC_HOST"
  elif [ -n "$ips" ]; then emit domain WARN "$DOMAIN -> $ips (expected $PUBLIC_HOST; ACME may fail)"
  else emit domain WARN "$DOMAIN does not resolve (Caddy ACME will fail)"; fi
fi

[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ] && emit clock PASS "ntp synced" || emit clock WARN "ntp not synced (raft prefers low skew)"

miss=""
for c in git curl mkfs.ext4 debugfs iptables caddy uv; do
  command -v "$c" >/dev/null 2>&1 || miss="$miss $c"
done
[ -n "$miss" ] && emit tools INFO "deploy will install:$miss" || emit tools PASS "all tools present"
'''

ORDER = {"FAIL": 0, "WARN": 1, "INFO": 2, "PASS": 3}


def load(path: str) -> dict:
    data = tomllib.loads(Path(path).read_text())
    if "nodes" not in data or not data["nodes"]:
        sys.exit("inventory has no [[nodes]]")
    return data


def _ssh_target(node: dict) -> str:
    return node.get("public_host") or node.get("domain") or node["host"]


def _args(cluster: dict, node: dict, nodes: list[dict]) -> list[str]:
    peers = [n["host"] for n in nodes if n["name"] != node["name"]]
    return [cluster.get("repo_url", ""), str(cluster.get("http_port", 8000)),
            str(cluster.get("rqlite_http_port", 4001)), str(cluster.get("rqlite_raft_port", 4002)),
            node.get("domain", ""), node.get("public_host", ""), *peers]


def probe(cluster: dict, node: dict, nodes: list[dict], local: bool) -> str:
    args = _args(cluster, node, nodes)
    if local:
        cmd = ["bash", "-s", "--", *args]
    else:
        ssh = ["ssh", "-A", "-o", "StrictHostKeyChecking=accept-new",
               "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
               f"{cluster.get('ssh_user', 'ubuntu')}@{_ssh_target(node)}"]
        cmd = [*ssh, "bash", "-s", "--", *args]
    try:
        r = subprocess.run(cmd, input=REMOTE_CHECK, capture_output=True, text=True, timeout=90)
        return r.stdout + (f"\n_ssh|FAIL|{r.stderr.strip()}" if r.returncode and not r.stdout else "")
    except subprocess.TimeoutExpired:
        return "_ssh|FAIL|probe timed out"
    except Exception as exc:  # noqa: BLE001 — surface any ssh failure as a node FAIL
        return f"_ssh|FAIL|{exc}"


def parse(out: str) -> list[tuple[str, str, str]]:
    rows = []
    for line in out.splitlines():
        if line.count("|") >= 2:
            name, status, detail = line.split("|", 2)
            rows.append((name, status, detail))
    return rows


def render(node: dict, rows: list[tuple[str, str, str]]) -> bool:
    print(f"\n=== {node['name']} ({_ssh_target(node)}) ===")
    if not rows:
        print("  [FAIL] no output from probe")
        return False
    for name, status, detail in sorted(rows, key=lambda r: ORDER.get(r[1], 9)):
        print(f"  [{status:<4}] {name:<14} {detail}")
    return all(s != "FAIL" for _, s, _ in rows)


def check_cidr(cluster: dict, nodes: list[dict]) -> None:
    cidr = cluster.get("vpc_cidr", "172.16.0.0/16")
    net = ipaddress.ip_network(cidr, strict=True)
    clashes = [n["host"] for n in nodes if ipaddress.ip_address(n["host"]) in net]
    if clashes:
        print(f"[FAIL] vpc_cidr {cidr} overlaps node underlay IPs {clashes} — pick a non-overlapping range")
    else:
        print(f"[ok] vpc_cidr {cidr} does not overlap any node underlay IP")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inventory", nargs="?")
    ap.add_argument("--local", action="store_true", help="run checks on this machine, no ssh")
    ap.add_argument("--print-script", action="store_true", help="dump the remote check script and exit")
    a = ap.parse_args()
    if a.print_script:
        print(REMOTE_CHECK)
        return 0
    if not a.inventory:
        ap.error("inventory file required (or use --print-script)")
    cluster, nodes = (d := load(a.inventory)).get("cluster", {}), d["nodes"]
    check_cidr(cluster, nodes)
    ok = True
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda n: (n, parse(probe(cluster, n, nodes, a.local))), nodes))
    for node, rows in results:
        ok = render(node, rows) and ok
    print(f"\n{'ALL NODES READY' if ok else 'PREFLIGHT FAILED — fix FAILs above before deploy'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
