"""Downstream contract: tables, routers, and the reconciler loop lifecycle."""
from dadar.app import DadarApp

from nyc.client.volume import pool
from nyc.config import lvm, resolve
from nyc.reconciler.executor import start as _start_executor
from nyc.reconciler.executor import stop as _stop_executor
from nyc.reconciler.loop import start as _start_loop
from nyc.reconciler.loop import stop as _stop_loop
from nyc.routers import ALL as ROUTERS
from nyc.tables import ALL as TABLES


def _on_startup(client, node_id):
    pool.ensure(node_id, lvm(), resolve().rootfs)
    _start_loop(client, node_id)
    _start_executor(client, node_id)


async def _on_shutdown(_client, _node_id):
    await _stop_loop()
    await _stop_executor()


app = DadarApp(
    tables=TABLES,
    routers=ROUTERS,
    on_startup=[_on_startup],
    on_shutdown=[_on_shutdown],
)
