"""Dispatch and execute a single task. Called from executor.py in a thread."""
from datetime import datetime, timezone

from dadar.orm import Client

from nyc.tables import Domains, Proxies, Tasks, Vms


def run_task(task: dict, client: Client, node_id: str) -> None:
    try:
        result = _dispatch(task, client, node_id)
        _finish(task["id"], "succeeded", result, client)
    except Exception as exc:
        _finish(task["id"], "failed", str(exc), client)


def _dispatch(task: dict, client: Client, node_id: str) -> str:
    if task["type"] == "reverse_proxy_setup":
        return _setup(task, client)
    if task["type"] == "proxy_reload":
        return _reload(task, client, node_id)
    raise ValueError(f"unknown task type: {task['type']}")


def _setup(task: dict, client: Client) -> str:
    from nyc.client.proxy import push
    from nyc.config import resolve
    vm = _vm_or_fail(task["vm_id"], client)
    key = str(resolve().ssh_key)
    out = push.setup(vm["ip"], key)
    Proxies(client).docs.update(where={"vm_id": task["vm_id"]}, set={"status": "ready"})
    return out or "ok"


def _reload(task: dict, client: Client, node_id: str) -> str:
    from nyc.client.proxy import caddyfile, push
    from nyc.config import resolve

    proxy = Proxies(client).docs.get(where={"node_id": node_id})
    if proxy is None:
        return "no proxy on this node"
    proxy_vm = _vm_or_fail(proxy.__dict__["vm_id"], client)

    routes = _build_routes(client)
    text = caddyfile.render(routes)
    key = str(resolve().ssh_key)
    out = push.reload(proxy_vm["ip"], key, text)
    return out or "ok"


def _build_routes(client: Client) -> list[tuple[str, str, int]]:
    routes = []
    for domain in Domains(client).docs.get_all():
        d = domain.__dict__
        vm = Vms(client).docs.get(where={"id": d["vm_id"]})
        if vm is None:
            continue
        routes.append((d["fqdn"], vm.__dict__["ip"], d["port"]))
    return routes


def _vm_or_fail(vm_id: str, client: Client) -> dict:
    vm = Vms(client).docs.get(where={"id": vm_id})
    if vm is None:
        raise RuntimeError(f"vm {vm_id} not found")
    return vm.__dict__


def _finish(task_id: str, status: str, result: str, client: Client) -> None:
    Tasks(client).docs.update(
        where={"id": task_id},
        set={"status": status, "result": result,
             "updated_at": datetime.now(timezone.utc).isoformat()},
    )
