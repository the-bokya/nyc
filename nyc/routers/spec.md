# routers

FastAPI plumbing. No SQL outside of the dadar ORM. No filesystem calls — they
belong in `nyc.client`. Routers compose: ORM read → client call → ORM write.

| File | Endpoint | Notes |
|---|---|---|
| `vpcs.py`      | `/vpcs`          | Global. No proxy. |
| `volumes.py`   | `/volumes`       | Node-bound. POST/DELETE proxy via `_proxy.forward`. |
| `vms.py`       | `/vms`           | Node-bound. POST runs the full lifecycle composer. |
| `reconcile.py` | `/reconcile`     | POST triggers one immediate reconciler pass on the receiving node. |
| `_proxy.py`    | (helper)         | Looks up `nodes.http_port` and forwards via httpx. |

Patterns:
- 400 for bad input (unknown vpc_id, bad cidr).
- 404 for missing rows.
- 409 for blocked deletes (VPC has VMs, volume attached).
- 201 for create, 204 for delete.

GET responses for `/vms` and `/vms/{id}` include a `live_status` field from
the client (live process check) on top of the DB-recorded status. The DB
status is the **intended** state; `live_status` is the **observed** state.
The reconciler reconciles them.
