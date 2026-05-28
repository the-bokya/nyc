"""The cross-node proxy targets the registry `host`, not hardcoded loopback."""
from datetime import datetime, timezone

import pytest
from dadar.tables import Nodes
from fastapi import HTTPException

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
