"""Reap local VMs older than NYC_VM_TTL_MINUTES (set from cluster.toml by deploy).

Optional: a TTL of 0 (or unset) disables reaping entirely. When enabled, every
reconciler pass deletes this node's VMs whose `created_at` is older than the
TTL — same teardown path as `DELETE /vms` (vm_down + drop the row; the auto data
volume is not cascaded, matching DELETE semantics).
"""
import os
from datetime import datetime, timezone

from dadar.orm import Client

from nyc.client.lifecycle import vm_down
from nyc.config import resolve
from nyc.tables import Vms


def ttl_minutes() -> float:
    return float(os.environ.get("NYC_VM_TTL_MINUTES", "0") or "0")


def reconcile(client: Client, node_id: str) -> dict:
    ttl = ttl_minutes()
    if ttl <= 0:
        return {"reaped": []}
    paths = resolve()
    cutoff = datetime.now(timezone.utc).timestamp() - ttl * 60
    rows = Vms(client).docs.get_all(where={"node_id": node_id})
    expired = [r.__dict__["id"] for r in rows if _expired(r.__dict__, cutoff)]
    for vm_id in expired:
        vm_down.run(paths.vms_dir, vm_id)
        Vms(client).docs.delete(where={"id": vm_id})
    return {"reaped": sorted(expired)}


def _expired(row: dict, cutoff: float) -> bool:
    return datetime.fromisoformat(row["created_at"]).timestamp() < cutoff
