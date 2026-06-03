"""Background task executor: runs pending Tasks rows for this node.

Atomically claims one pending task at a time (UPDATE ... WHERE status='pending',
check rows_affected) and runs it in a thread so blocking SSH/install work never
stalls the asyncio event loop. Mirrors reconciler/loop.py structure.
"""
import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from dadar.orm import Client

from nyc.reconciler.task_runner import run_task


def interval() -> float:
    return float(os.environ.get("NYC_EXECUTOR_INTERVAL", "5"))


async def loop(client: Client, node_id: str, stop: asyncio.Event) -> None:
    delay = interval()
    while not stop.is_set():
        try:
            await asyncio.to_thread(_tick, client, node_id)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


def _tick(client: Client, node_id: str) -> None:
    from nyc.tables import Tasks
    pending = Tasks(client).docs.get_all(where={"node_id": node_id, "status": "pending"})
    for task in pending:
        d = task.__dict__
        # Atomic claim: update only if still pending (concurrent safety)
        Tasks(client).docs.update(
            where={"id": d["id"], "status": "pending"},
            set={"status": "running", "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        # Re-read to confirm we won the claim
        claimed = Tasks(client).docs.get(where={"id": d["id"], "status": "running"})
        if claimed is None:
            continue
        run_task(d, client, node_id)
        break  # one task per tick


_TASK: Optional[asyncio.Task] = None
_STOP: Optional[asyncio.Event] = None


def start(client: Client, node_id: str) -> None:
    global _TASK, _STOP
    if _TASK is not None:
        return
    _STOP = asyncio.Event()
    _TASK = asyncio.create_task(loop(client, node_id, _STOP))


async def stop() -> None:
    global _TASK, _STOP
    if _STOP is not None:
        _STOP.set()
    if _TASK is not None:
        await _TASK
    _TASK = None
    _STOP = None
