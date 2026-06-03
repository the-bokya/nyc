#!/usr/bin/env python3
"""nyc bare-metal deploy orchestrator.

Provisioning is pyinfra (`inventory.py` + `provision.py` / `teardown.py`); this
script drives it and adds the app-level steps pyinfra doesn't model — health
waits, the default VPC, the spawn smoke test, and the read-only operational
commands.

  deploy.py up     cluster.toml          # pyinfra provision (bootstrap first) + smoke
  deploy.py down   cluster.toml [--purge] # delete VMs via API, then pyinfra teardown
  deploy.py status cluster.toml          # health + node count
  deploy.py ssh    cluster.toml <vm_id>  # ssh into a VM via the host jump server
  deploy.py overlay-check cluster.toml <vpc_id>  # per-node VXLAN/FDB/NAT audit

Run inside the nyc venv so `pyinfra` is on PATH:  uv run scripts/deploy.py up ...
API calls (health, VPC, smoke, overlay probe) run *on* a node over ssh ->
localhost, so the control machine needs no private-network access. See spec.md.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SSH = ["ssh", "-A", "-o", "StrictHostKeyChecking=accept-new",
       "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
LOOPBACK = (None, "", "127.0.0.1", "localhost")

# Read-only per-node overlay probe (args: BR VX). Emits key=val lines.
OVERLAY_PROBE = r'''
BR="$1"; VX="$2"
ex=no; ip link show "$VX" >/dev/null 2>&1 && ex=yes
vni=$(ip -d link show "$VX" 2>/dev/null | grep -o 'vxlan id [0-9]*' | awk '{print $3}')
local=$(ip -d link show "$VX" 2>/dev/null | grep -o 'local [0-9.]*' | awk '{print $2}')
master=$(ip link show "$VX" 2>/dev/null | grep -o 'master [^ ]*' | awk '{print $2}')
fdb=$(bridge fdb show dev "$VX" 2>/dev/null | awk '/00:00:00:00:00:00/{for(i=1;i<=NF;i++) if($i=="dst") print $(i+1)}' | sort -u | paste -sd, -)
braddr=$(ip -o -4 addr show "$BR" 2>/dev/null | awk '{print $4}')
brmac=$(cat /sys/class/net/"$BR"/address 2>/dev/null)
ipfwd=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)
printf 'ex=%s\nvni=%s\nlocal=%s\nmaster=%s\nfdb=%s\nbraddr=%s\nbrmac=%s\nipfwd=%s\n' \
  "$ex" "$vni" "$local" "$master" "$fdb" "$braddr" "$brmac" "$ipfwd"
'''


# --- overlay name/id helpers (mirror nyc.client.network.overlay) ---
def vni_for(vpc_id: str) -> int:
    return int.from_bytes(hashlib.sha256(vpc_id.encode()).digest()[:4], "big") % (2**24 - 1) + 1


def anycast_mac(vpc_id: str) -> str:
    b = hashlib.sha256(vpc_id.encode()).digest()
    return "02:" + ":".join(f"{x:02x}" for x in b[:5])


def overlay_names(node_id: str, vpc_id: str) -> tuple[str, str]:
    return f"br-{node_id[:4]}-{vpc_id[:4]}", f"vx-{node_id[:4]}-{vpc_id[:4]}"


# --- inventory ---
def load(path: str) -> tuple[dict, list[dict]]:
    data = tomllib.loads(Path(path).read_text())
    if not data.get("nodes"):
        sys.exit("inventory has no [[nodes]]")
    return data.get("cluster", {}), data["nodes"]


def validate(cluster: dict, nodes: list[dict]) -> None:
    boots = [n for n in nodes if n.get("bootstrap")]
    if len(boots) != 1:
        sys.exit(f"exactly one node must have bootstrap=true (found {len(boots)})")
    cidr = cluster.get("vpc_cidr", "172.16.0.0/16")
    net = ipaddress.ip_network(cidr, strict=True)
    clash = [n["host"] for n in nodes if ipaddress.ip_address(n["host"]) in net]
    if clash:
        sys.exit(f"vpc_cidr {cidr} overlaps node underlay IPs {clash}")


def bootstrap_of(nodes: list[dict]) -> dict:
    return next(n for n in nodes if n.get("bootstrap"))


def ssh_target(cluster: dict, node: dict) -> str:
    host = node.get("public_host") or node.get("domain") or node["host"]
    return f"{cluster.get('ssh_user', 'ubuntu')}@{host}"


def keydir(inventory: str) -> Path:
    return Path(inventory).resolve().parent / ".nyc-deploy"


def ensure_keypair(inventory: str) -> None:
    """Generate the shared VM keypair if absent (provision.py uploads it)."""
    d = keydir(inventory)
    d.mkdir(exist_ok=True)
    key = d / "id_ed25519"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-q"], check=True)


# --- pyinfra ---
def _pyinfra(deploy: str, inventory: str, limit: list[str] | None = None, purge: bool = False) -> None:
    env = {**os.environ, "NYC_CLUSTER": str(Path(inventory).resolve())}
    if purge:
        env["NYC_PURGE"] = "1"
    cmd = ["pyinfra", "inventory.py", deploy] + (["--limit", ",".join(limit)] if limit else [])
    print(f"--> pyinfra {deploy} ({','.join(limit) if limit else 'all'})")
    if subprocess.run(cmd, cwd=HERE, env=env).returncode:
        raise RuntimeError(f"pyinfra {deploy} failed")


# --- on-node API access (ssh -> the node's own localhost) ---
def remote_curl(cluster: dict, node: dict, method: str, path: str, body: dict | None = None) -> dict:
    url = f"http://{node['host']}:{cluster.get('http_port', 8000)}{path}"
    inner = ["curl", "-fsS", "-X", method, url]
    if body is not None:
        inner += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    cmd = SSH + [ssh_target(cluster, node), shlex.join(inner)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    return json.loads(out) if out else {}


def wait_health(cluster: dict, node: dict, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: str = ""
    while time.monotonic() < deadline:
        try:
            if remote_curl(cluster, node, "GET", "/health"):
                print(f"    {node['name']} healthy")
                return
            last_err = "empty response"
        except Exception as exc:
            last_err = str(exc)
        time.sleep(2.0)
    raise RuntimeError(f"{node['name']} never became healthy (last error: {last_err})")


def ensure_default_vpc(cluster: dict, boot: dict) -> None:
    cidr = cluster.get("vpc_cidr", "172.16.0.0/16")
    if any(v.get("name") == "default" for v in remote_curl(cluster, boot, "GET", "/vpcs")):
        print("    default VPC already exists")
        return
    remote_curl(cluster, boot, "POST", "/vpcs", {"name": "default", "cidr": cidr})
    print(f"    created default VPC {cidr}")


def smoke(cluster: dict, boot: dict, inventory: str) -> None:
    pub = (keydir(inventory) / "id_ed25519.pub").read_text().strip()
    vm = remote_curl(cluster, boot, "POST", "/vms/spawn", {"vm_name": "deploy-smoke", "ssh_key": pub})
    ok = vm.get("status") == "running"
    print(f"    smoke spawn: {vm.get('id')} status={vm.get('status')}")
    remote_curl(cluster, boot, "DELETE", f"/vms/{vm['id']}")
    if vm.get("data_volume_id"):
        remote_curl(cluster, boot, "DELETE", f"/volumes/{vm['data_volume_id']}")
    if not ok:
        raise RuntimeError("smoke test VM did not reach running")


# --- commands ---
def cmd_up(cluster: dict, nodes: list[dict], inventory: str) -> int:
    validate(cluster, nodes)
    ensure_keypair(inventory)
    boot = bootstrap_of(nodes)
    _pyinfra("provision.py", inventory, limit=[boot["name"]])
    wait_health(cluster, boot)
    joiners = [n for n in nodes if not n.get("bootstrap")]
    if joiners:
        _pyinfra("provision.py", inventory, limit=[n["name"] for n in joiners])
        for n in joiners:
            wait_health(cluster, n)
    ensure_default_vpc(cluster, boot)
    smoke(cluster, boot, inventory)
    print(f"\nUP: {len(nodes)} node(s) provisioned.")
    return 0


def cmd_down(cluster: dict, nodes: list[dict], inventory: str, purge: bool) -> int:
    _delete_all_vms(cluster, nodes)
    _pyinfra("teardown.py", inventory, purge=purge)
    print(f"\nDOWN: {len(nodes)} node(s) torn down{' (purged)' if purge else ''}.")
    return 0


def cmd_status(cluster: dict, nodes: list[dict]) -> int:
    boot = bootstrap_of(nodes)
    for n in nodes:
        try:
            h = remote_curl(cluster, n, "GET", "/health")
            print(f"  [up]   {n['name']} ({n['host']}) {h}")
        except Exception as exc:
            print(f"  [down] {n['name']} ({n['host']}) {exc}")
    try:
        registered = remote_curl(cluster, boot, "GET", "/nodes")
        print(f"\ncluster sees {len(registered)} / {len(nodes)} nodes")
    except Exception as exc:
        print(f"\ncould not read /nodes from bootstrap: {exc}")
    return 0


def cmd_ssh(cluster: dict, nodes: list[dict], inventory: str, vm_id: str) -> int:
    # The hosting node is the only box that can route to the VM's VPC IP, so we
    # ProxyJump through it using the operator's normal node login (ssh_user) and
    # authenticate to the VM with the shared VM key. No special host account.
    boot = bootstrap_of(nodes)
    vm = remote_curl(cluster, boot, "GET", f"/vms/{vm_id}")
    by_id = {n["node_id"]: n for n in remote_curl(cluster, boot, "GET", "/nodes")}
    host_node = by_id.get(vm["node_id"])
    if not host_node:
        sys.exit(f"could not resolve hosting node for vm {vm_id}")
    argv = ["ssh", "-J", ssh_target(cluster, host_node), "-i", str(keydir(inventory) / "id_ed25519"),
            "-o", "StrictHostKeyChecking=accept-new", f"root@{vm['ip']}"]
    print(f"--> {' '.join(argv)}")
    return subprocess.call(argv)


def cmd_overlay_check(cluster: dict, nodes: list[dict], vpc_id: str) -> int:
    boot = bootstrap_of(nodes)
    registry = remote_curl(cluster, boot, "GET", "/nodes")
    vms = remote_curl(cluster, boot, "GET", "/vms")
    with_vm = {v["node_id"] for v in vms if v.get("vpc_id") == vpc_id}
    hosts = [r["host"] for r in registry if r["host"] not in LOOPBACK]
    vni, mac = vni_for(vpc_id), anycast_mac(vpc_id)
    ok = True
    for r in registry:
        br, vx = overlay_names(r["node_id"], vpc_id)
        peers = sorted(set(hosts) - {r["host"]})
        ok = _audit_node(cluster, r, br, vx, vni, mac, peers, r["node_id"] in with_vm) and ok
    print(f"\nvpc {vpc_id}: vni={vni} anycast_mac={mac} (peers expected per node = the other underlay IPs)")
    print("OVERLAY OK" if ok else "OVERLAY ISSUES — investigate [BAD] rows (see the diagnostic ladder)")
    return 0 if ok else 1


def _audit_node(cluster: dict, reg: dict, br: str, vx: str, vni: int, mac: str,
                peers: list[str], expect: bool) -> bool:
    try:
        d = _overlay_probe(cluster, reg, br, vx)
    except Exception as exc:
        print(f"\n=== {reg['host']} === \n  [ERR] probe failed: {exc}")
        return False
    return _render_overlay(reg, _eval_overlay(d, reg["host"], vni, mac, br, vx, peers, expect))


def _overlay_probe(cluster: dict, reg: dict, br: str, vx: str) -> dict:
    cmd = SSH + [ssh_target(cluster, reg), "bash", "-s", "--", br, vx]
    out = subprocess.run(cmd, input=OVERLAY_PROBE, capture_output=True, text=True, timeout=30).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _eval_overlay(d: dict, host: str, vni: int, mac: str, br: str, vx: str,
                  peers: list[str], expect: bool) -> list[tuple]:
    if not expect:
        stale = d.get("ex") == "yes"
        return [("overlay", not stale,
                 f"STALE: {vx} present but no VM in VPC" if stale
                 else "no VM in this VPC on node (overlay not expected)")]
    have = sorted(filter(None, (d.get("fdb") or "").split(",")))
    return [
        ("overlay dev", d.get("ex") == "yes", vx if d.get("ex") == "yes" else f"{vx} MISSING"),
        ("vni", d.get("vni") == str(vni), f"{d.get('vni')} (want {vni})"),
        ("local ip", d.get("local") == host, f"{d.get('local')} (want {host})"),
        ("bridge master", d.get("master") == br, f"{d.get('master')} (want {br})"),
        ("fdb peers", have == peers, f"have {have} want {peers}"),
        ("bridge mac", d.get("brmac") == mac, f"{d.get('brmac')} (want {mac})"),
        ("bridge gw ip", bool(d.get("braddr")), d.get("braddr") or "MISSING"),
        ("ip_forward", d.get("ipfwd") == "1", d.get("ipfwd") or "?"),
    ]


def _render_overlay(reg: dict, rows: list[tuple]) -> bool:
    tgt = reg.get("domain") or reg.get("public_host") or reg["host"]
    print(f"\n=== {tgt} ({reg['host']}) node={reg['node_id'][:8]} ===")
    node_ok = True
    for label, good, detail in rows:
        print(f"  [{'OK ' if good else 'BAD'}] {label:<14} {detail}")
        node_ok = node_ok and good
    return node_ok


def _delete_all_vms(cluster: dict, nodes: list[dict]) -> None:
    boot = bootstrap_of(nodes)
    try:
        vms = remote_curl(cluster, boot, "GET", "/vms")
    except Exception:
        print("    cluster unreachable — skipping API VM cleanup")
        return
    for vm in vms:
        try:
            remote_curl(cluster, boot, "DELETE", f"/vms/{vm['id']}")
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="nyc bare-metal deploy")
    ap.add_argument("action", choices=["up", "down", "status", "ssh", "overlay-check"])
    ap.add_argument("inventory")
    ap.add_argument("target", nargs="?", help="ssh: vm_id · overlay-check: vpc_id")
    ap.add_argument("--purge", action="store_true", help="down: also remove packages + checkout")
    a = ap.parse_args()
    cluster, nodes = load(a.inventory)
    if a.action == "up":
        return cmd_up(cluster, nodes, a.inventory)
    if a.action == "down":
        return cmd_down(cluster, nodes, a.inventory, a.purge)
    if a.action == "ssh":
        if not a.target:
            ap.error("ssh requires a vm_id")
        return cmd_ssh(cluster, nodes, a.inventory, a.target)
    if a.action == "overlay-check":
        if not a.target:
            ap.error("overlay-check requires a vpc_id")
        return cmd_overlay_check(cluster, nodes, a.target)
    return cmd_status(cluster, nodes)


if __name__ == "__main__":
    raise SystemExit(main())
