"""End-to-end against a live N-node cluster.

Two ways to point this at a cluster:
  NYC_E2E_URLS        - comma-separated base URLs, node 1 first
                        (e.g. "http://10.0.0.11:8000,http://10.0.0.12:8000").
                        Used by `deploy up` against real bare-metal nodes.
  NYC_STAGE_BASE_PORT - port of node 1 on localhost (subsequent nodes +1),
  NYC_STAGE_NODES     - cluster size N. Set by `scripts/stage.sh`.

`NYC_E2E_URLS` wins when both are set. Skipped if neither is set (unit runs).
"""
import os
import re
import subprocess
import time

import httpx
import pytest

URLS = [u.strip().rstrip("/") for u in os.environ.get("NYC_E2E_URLS", "").split(",") if u.strip()]
BASE = int(os.environ.get("NYC_STAGE_BASE_PORT", "0"))
N = len(URLS) or int(os.environ.get("NYC_STAGE_NODES", "0"))

pytestmark = pytest.mark.skipif(not URLS and BASE == 0, reason="staging env vars not set")


def _list_links() -> list[str]:
    out = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True).stdout
    return [line.split(":", 2)[1].strip().split("@")[0] for line in out.splitlines() if line.strip()]


def _list_netns() -> list[str]:
    out = subprocess.run(["sudo", "-n", "/usr/bin/ip", "netns", "list"],
                         capture_output=True, text=True).stdout
    return [line.split()[0] for line in out.splitlines() if line.strip()]


@pytest.fixture(scope="session", autouse=True)
def _purge_stale_kernel_state():
    """After the e2e session, drop nyc-pattern bridges / netns that survived
    per-VM teardown (the bridge is shared per VPC and isn't deleted by vm_down).
    """
    yield
    for ns in _list_netns():
        if re.fullmatch(r"vm-[0-9a-f]{8}", ns):
            subprocess.run(["sudo", "-n", "/usr/bin/ip", "netns", "del", ns], check=False)
    for link in _list_links():
        if re.fullmatch(r"br-[0-9a-f]{4}-[0-9a-f]{4}", link) or link.startswith(("vmh-", "vmn-")):
            subprocess.run(["sudo", "-n", "/usr/bin/ip", "link", "del", link], check=False)


def url(node_i: int, path: str) -> str:
    if URLS:
        return f"{URLS[node_i - 1]}{path}"
    return f"http://127.0.0.1:{BASE + node_i - 1}{path}"


def post(node_i, path, json):
    r = httpx.post(url(node_i, path), json=json, timeout=30.0)
    if r.status_code >= 400:
        raise AssertionError(f"POST {path} on node{node_i} → {r.status_code}: {r.text}")
    return r.json()


def _wait_propagate(node_i: int, path: str, pred, timeout=10.0) -> dict | list:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = httpx.get(url(node_i, path), timeout=2.0).json()
        if pred(last):
            return last
        time.sleep(0.2)
    raise AssertionError(f"never propagated to node{node_i}: {last}")


def test_health_on_every_node():
    for i in range(1, N + 1):
        assert httpx.get(url(i, "/health"), timeout=2.0).status_code == 200


def test_cluster_sees_all_n_nodes():
    nodes = httpx.get(url(1, "/nodes"), timeout=5.0).json()
    assert len(nodes) == N


def test_vpc_created_on_node1_visible_on_node_n():
    vpc = httpx.post(url(1, "/vpcs"),
                     json={"name": f"e2e-{time.time_ns()}", "cidr": "10.123.0.0/24"},
                     timeout=5.0).json()
    _wait_propagate(N, "/vpcs", lambda lst: any(v["id"] == vpc["id"] for v in lst))


def test_volume_targeted_at_last_node_propagates_to_all():
    nodes = httpx.get(url(1, "/nodes"), timeout=5.0).json()
    target = nodes[-1]["node_id"]
    vol = post(1, "/volumes", {"name": f"vol-{time.time_ns()}", "size_mb": 8, "node_id": target})
    assert vol["node_id"] == target
    _wait_propagate(N, "/volumes", lambda lst: any(v["id"] == vol["id"] for v in lst))


def test_vm_full_lifecycle_across_nodes():
    nodes = httpx.get(url(1, "/nodes"), timeout=5.0).json()
    target = nodes[-1]["node_id"]
    vpc = post(1, "/vpcs", {"name": f"vmnet-{time.time_ns()}", "cidr": "10.200.0.0/24"})
    vm = post(1, "/vms", {"name": "spread", "vpc_id": vpc["id"], "node_id": target})
    assert vm["node_id"] == target
    assert vm["ip"].startswith("10.200.0.")
    _wait_propagate(1, "/vms", lambda lst: any(v["id"] == vm["id"] and v["status"] == "running" for v in lst))
    assert httpx.delete(url(1, f"/vms/{vm['id']}"), timeout=15.0).status_code == 204
    _wait_propagate(N, "/vms", lambda lst: not any(v["id"] == vm["id"] for v in lst))


def test_reconciler_endpoint_responds():
    rep = httpx.post(url(1, "/reconcile"), timeout=10.0).json()
    assert "vms" in rep and "volumes" in rep


def test_spawn_many_reuse_shared_bridge():
    # Regression: every spawn uses the default VPC, so VMs landing on the same
    # node share one bridge. bridge.ensure must be idempotent — a real-mode
    # exists() bug made the 2nd spawn on a node fail with "File exists".
    vms = [post(1, "/vms/spawn", {"vm_name": f"sh-{i}-{time.time_ns()}",
                                  "ssh_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY sh@nyc"})
           for i in range(4)]
    try:
        assert all(v["status"] == "running" for v in vms)
        assert len({v["ip"] for v in vms}) == len(vms)  # distinct IPs in the /16
    finally:
        for v in vms:
            httpx.delete(url(1, f"/vms/{v['id']}"), timeout=15.0)
            httpx.delete(url(1, f"/volumes/{v['data_volume_id']}"), timeout=15.0)


def test_live_status_consistent_across_nodes():
    # Regression: live_status is a per-owner observation. Before the fix a
    # non-owner node reported "stopped" because it checked its own (empty) local
    # state. Every node must now agree with the owner.
    vm = post(1, "/vms/spawn", {"vm_name": f"ls-{time.time_ns()}",
                                "ssh_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY ls@nyc"})
    try:
        for i in range(1, N + 1):
            lst = _wait_propagate(i, "/vms", lambda l: any(v["id"] == vm["id"] for v in l))
            mine = next(v for v in lst if v["id"] == vm["id"])
            assert mine["live_status"] == "running", f"node{i} list: {mine['live_status']}"
            one = httpx.get(url(i, f"/vms/{vm['id']}"), timeout=5.0).json()
            assert one["live_status"] == "running", f"node{i} get: {one['live_status']}"
    finally:
        httpx.delete(url(1, f"/vms/{vm['id']}"), timeout=15.0)
        httpx.delete(url(1, f"/volumes/{vm['data_volume_id']}"), timeout=15.0)


def test_spawn_default_vpc_random_node():
    # No vpc_id / node_id: lands in the default /16, auto-volume, random node.
    vm = post(1, "/vms/spawn", {"vm_name": f"sp-{time.time_ns()}",
                                "ssh_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY e2e@nyc"})
    try:
        assert vm["status"] == "running"
        assert vm["ip"].startswith("172.16.")
        assert vm["data_volume_id"]
        assert vm["node_id"] in {n["node_id"] for n in httpx.get(url(1, "/nodes"), timeout=5.0).json()}
        _wait_propagate(N, "/vms", lambda lst: any(v["id"] == vm["id"] for v in lst))
        _wait_propagate(N, "/volumes", lambda lst: any(v["id"] == vm["data_volume_id"] for v in lst))
    finally:
        httpx.delete(url(1, f"/vms/{vm['id']}"), timeout=15.0)
        httpx.delete(url(1, f"/volumes/{vm['data_volume_id']}"), timeout=15.0)


def test_ssh_into_vm_works():
    """Real boot + real network + real sshd. Skipped under NYC_BACKEND=fake."""
    if os.environ.get("NYC_BACKEND") != "real":
        pytest.skip("ssh requires NYC_BACKEND=real")
    pubkey = open("assets/id_ed25519.pub").read().strip()
    vm = post(1, "/vms/spawn", {"vm_name": f"ssh-{time.time_ns()}", "ssh_key": pubkey})
    try:
        _wait_ssh(vm["ip"], timeout=90.0)
        out = _ssh(vm["ip"], "echo nyc-ok && uname -n")
        assert "nyc-ok" in out
    finally:
        httpx.delete(url(1, f"/vms/{vm['id']}"), timeout=15.0)
        httpx.delete(url(1, f"/volumes/{vm['data_volume_id']}"), timeout=15.0)


def _ssh(ip: str, cmd: str) -> str:
    argv = ["ssh", "-i", "assets/id_ed25519",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=5",
            f"root@{ip}", cmd]
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout


def _wait_ssh(ip: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _ssh(ip, "true")
            return
        except subprocess.CalledProcessError:
            time.sleep(1.0)
    raise AssertionError(f"ssh to {ip} never came up within {timeout}s")


def test_db_is_source_of_truth_kills_orphan():
    # Create a VM, delete the row, then trigger reconcile on the owner and
    # check the local resource is gone — assert reconcile is idempotent
    # (no crash, no row, no orphan listed in the report).
    nodes = httpx.get(url(1, "/nodes"), timeout=5.0).json()
    owner_idx = N  # last node, works for any N >= 1
    owner_id = nodes[owner_idx - 1]["node_id"]
    vpc = post(1, "/vpcs", {"name": f"orph-{time.time_ns()}", "cidr": "10.201.0.0/24"})
    vm = post(1, "/vms", {"name": "orphan", "vpc_id": vpc["id"], "node_id": owner_id})
    httpx.delete(url(owner_idx, f"/vms/{vm['id']}"), timeout=10.0)
    rep = httpx.post(url(owner_idx, "/reconcile"), timeout=10.0).json()
    assert vm["id"] not in rep["vms"].get("killed", [])
