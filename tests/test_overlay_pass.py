"""Reconciler overlay pass: re-syncs each local VPC's VXLAN FDB to the peers."""
from datetime import datetime, timezone

from dadar.tables import Nodes

from nyc.client.network import vxlan
from nyc.reconciler.overlay_pass import reconcile
from nyc.tables import Vms


def _node(client, nid, host):
    Nodes(client).docs.insert({
        "node_id": nid, "http_port": 8000, "rqlite_http_port": 4001,
        "rqlite_raft_port": 4002, "host": host, "public_host": None,
        "domain": None, "first_seen_at": datetime.now(timezone.utc).isoformat()})


def _vm(client, vm_id, node_id, vpc_id):
    Vms(client).docs.insert({
        "id": vm_id, "node_id": node_id, "name": vm_id, "vpc_id": vpc_id,
        "data_volume_id": None, "ip": "172.16.0.2", "ssh_pubkey_path": None,
        "status": "running", "created_at": datetime.now(timezone.utc).isoformat()})


def test_overlay_sync_populates_fdb(node):
    c = node["orm"]
    _node(c, "self-r", "10.1.0.14"); _node(c, "p1", "10.1.0.15"); _node(c, "p2", "10.1.0.2")
    _vm(c, "vm1", "self-r", "vpcA")
    name = vxlan.name_for("self-r", "vpcA")
    vxlan.ensure(name, 99, "10.1.0.14", "br-self-vpca")
    rep = reconcile(c, "self-r")
    assert "vpcA" in rep["synced"]
    assert vxlan._fdb_peers(name) == {"10.1.0.15", "10.1.0.2"}


def test_overlay_noop_on_loopback(node):
    # conftest registered this node with host 127.0.0.1 → single-host, no overlay.
    assert reconcile(node["orm"], node["node_id"]) == {"synced": []}
