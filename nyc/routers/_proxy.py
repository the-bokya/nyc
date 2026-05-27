"""Forward a request from the receiving node to the node that owns the resource.

Looked up via the dadar `nodes` table — every node registers its http_port
there at boot. We POST/DELETE/etc. against `http://127.0.0.1:<port>` (single
host today; future bare-metal will swap in real addresses, also from `nodes`).
"""
from typing import Any

import httpx
from dadar.orm import Client
from dadar.tables import Nodes
from fastapi import HTTPException


def forward(client: Client, node_id: str, method: str, path: str, json: Any = None) -> dict:
    base = _base_url(client, node_id)
    resp = httpx.request(method, f"{base}{path}", json=json, timeout=30.0)
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json() if resp.content else {}


def _base_url(client: Client, node_id: str) -> str:
    row = Nodes(client).docs.get(where={"node_id": node_id})
    if row is None:
        raise HTTPException(404, f"unknown node {node_id}")
    return f"http://127.0.0.1:{row.__dict__['http_port']}"
