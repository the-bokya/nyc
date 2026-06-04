"""The cross-node proxy targets the registry `host`, not hardcoded loopback.

Also covers the acquire-before-spawn ordering: the proxy VM must boot with
eth1 directly (no recreate) because the PublicIps row is inserted before
_bring_up runs.
"""
import os
from datetime import datetime, timezone

import pytest
from dadar.tables import Nodes
from fastapi import HTTPException

os.environ.setdefault("NYC_PUBLIC_IPS", "203.0.113.10|de:ad:be:ef:00:01,203.0.113.11|de:ad:be:ef:00:02")
os.environ.setdefault("NYC_PUBLIC_BRIDGE", "pub0")
os.environ.setdefault("NYC_PUBIP_GATEWAY", "62.210.0.1")

from nyc.routers._proxy import _base_url


def _register(client, node_id, host, http_port):
    Nodes(client).docs.insert({
        "node_id": node_id, "http_port": http_port,
        "rqlite_http_port": 4001, "rqlite_raft_port": 4002,
        "host": host, "public_host": None, "domain": None,
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
    })


def test_base_url_uses_private_host(node):
    client = node["orm"]
    _register(client, "peer-1", "10.0.0.42", 8000)
    assert _base_url(client, "peer-1") == "http://10.0.0.42:8000"


def test_base_url_loopback_single_host(node):
    client = node["orm"]
    _register(client, "peer-2", "127.0.0.1", 9002)
    assert _base_url(client, "peer-2") == "http://127.0.0.1:9002"


def test_base_url_unknown_node_404(node):
    with pytest.raises(HTTPException) as exc:
        _base_url(node["orm"], "ghost")
    assert exc.value.status_code == 404


def test_proxy_vm_boots_with_eth1_no_recreate(http, node):
    """Proxy VM must have eth1 wired at first boot (no recreate after spawn)."""
    from nyc.client.privops_fake import STATE
    import json
    from nyc.config import resolve
    from nyc.client.env.paths import for_vm

    r = http.post("/proxy", json={"name": "px"})
    assert r.status_code == 201, r.text
    d = r.json()
    vm_id = d["vm"]["id"]
    pip = d["public_ip"]

    assert pip["address"] == "203.0.113.10"
    assert pip["mac"] == "de:ad:be:ef:00:01"

    # pvh-* enslaved to pub0 — wired at first spawn
    pvh = f"pvh-{vm_id[:8]}"
    assert STATE["links"][pvh].get("master") == "pub0"

    # config.json has eth1 from the first (and only) spawn — no recreate
    paths = for_vm(resolve().vms_dir, vm_id)
    cfg = json.loads(paths.config.read_text())
    nics = cfg["network-interfaces"]
    assert len(nics) == 2
    assert nics[1]["iface_id"] == "eth1"
    assert nics[1]["guest_mac"] == "de:ad:be:ef:00:01"
