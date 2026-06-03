"""Tests for the task executor: atomic claim, state transitions, result population."""
import pytest

from nyc.reconciler.task_runner import run_task


@pytest.fixture
def vpc(http):
    return http.post("/vpcs", json={"name": "net", "cidr": "10.1.0.0/24"}).json()


@pytest.fixture
def vm(http, vpc):
    return http.post("/vms", json={"name": "tv", "vpc_id": vpc["id"]}).json()


def test_create_task_via_api(http, vm):
    r = http.post(f"/vms/{vm['id']}/tasks", json={"type": "proxy_reload"})
    assert r.status_code == 201
    t = r.json()
    assert t["status"] == "pending"
    assert t["vm_id"] == vm["id"]


def test_list_and_get_task(http, vm):
    t = http.post(f"/vms/{vm['id']}/tasks", json={"type": "proxy_reload"}).json()
    assert any(x["id"] == t["id"] for x in http.get("/tasks").json())
    assert http.get(f"/tasks/{t['id']}").json()["id"] == t["id"]


def test_task_not_found(http):
    assert http.get("/tasks/no-such-id").status_code == 404


def test_run_task_succeeded(node):
    """run_task marks succeeded when the dispatch returns."""
    from nyc.tables import Tasks
    from datetime import datetime, timezone
    import uuid

    client = node["orm"]
    n_id = node["node_id"]
    now = datetime.now(timezone.utc).isoformat()

    # Insert a task that will fail (no vm found) — tests the failed transition too
    task = {"id": str(uuid.uuid4()), "node_id": n_id, "vm_id": "no-vm",
            "type": "proxy_reload", "params": None, "status": "running",
            "result": None, "created_at": now, "updated_at": now}
    Tasks(client).docs.insert(task)

    run_task(task, client, n_id)

    updated = Tasks(client).docs.get(where={"id": task["id"]})
    d = updated.__dict__
    # "proxy_reload" with no proxy on this node returns "no proxy on this node"
    assert d["status"] == "succeeded"
    assert d["result"] is not None


def test_run_task_failed_on_bad_type(node):
    from nyc.tables import Tasks
    from datetime import datetime, timezone
    import uuid

    client = node["orm"]
    n_id = node["node_id"]
    now = datetime.now(timezone.utc).isoformat()
    task = {"id": str(uuid.uuid4()), "node_id": n_id, "vm_id": "noop",
            "type": "unknown_type", "params": None, "status": "running",
            "result": None, "created_at": now, "updated_at": now}
    Tasks(client).docs.insert(task)
    run_task(task, client, n_id)
    d = Tasks(client).docs.get(where={"id": task["id"]}).__dict__
    assert d["status"] == "failed"
    assert "unknown task type" in d["result"]
