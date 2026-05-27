"""Background asyncio task that runs the reconciler every N seconds.

Launched from FastAPI's lifespan in `nyc.app`. Stops when the app stops.
"""
import asyncio
import os
from typing import Optional

from dadar.orm import Client

from nyc.reconciler.pass_once import run as reconcile_once


def interval() -> float:
    return float(os.environ.get("NYC_RECONCILE_INTERVAL", "5"))


async def loop(client: Client, node_id: str, stop: asyncio.Event) -> None:
    delay = interval()
    while not stop.is_set():
        try:
            reconcile_once(client, node_id)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


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
