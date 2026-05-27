"""Shared fixtures: real rqlite, FastAPI TestClient, NYC_BACKEND=fake.

We never mock the database — every test gets a real rqlited in a tmpdir on
free ports. We DO use the fake privops backend so tests don't need /dev/kvm,
sudo, or a real firecracker binary.
"""
import os
import socket
import time

import httpx
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("NYC_BACKEND", "fake")
os.environ.setdefault("NYC_RECONCILE_INTERVAL", "60")

from dadar.api import build  # noqa: E402
from dadar.config import NodeConfig  # noqa: E402
from dadar.node import bootstrap, rqlite_proc  # noqa: E402
from dadar.orm import Client  # noqa: E402

from nyc.app import app as nyc_app  # noqa: E402
from nyc.client import privops  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/readyz", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError(url)


@pytest.fixture(autouse=True)
def _reset_privops_state():
    privops.reset_state()
    yield
    privops.reset_state()


@pytest.fixture
def node(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = NodeConfig(
        folder=tmp_path,
        http_port=_free_port(),
        rqlite_http_port=_free_port(),
        rqlite_raft_port=_free_port(),
    )
    proc = rqlite_proc.start(cfg, node_id="nyc-test-1")
    try:
        _wait_ready(cfg.rqlite_url)
        client = Client(cfg.rqlite_url)
        bootstrap.ensure_tables(client, nyc_app.tables)
        bootstrap.register_self(client, "nyc-test-1", cfg)
        fastapi_app = build(client, "nyc-test-1", user_routers=nyc_app.routers)
        yield {"client": TestClient(fastapi_app), "orm": client, "node_id": "nyc-test-1", "tmp_path": tmp_path}
        client.close()
    finally:
        rqlite_proc.stop(proc)


@pytest.fixture
def http(node):
    return node["client"]
