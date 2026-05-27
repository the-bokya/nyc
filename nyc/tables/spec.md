# tables

Three ORM models, one file each. Each subclasses `dadar.orm.ORM` and declares
`name` + `fields` only — no methods. CRUD goes through `Vpcs(client).docs.*`.

| File | Purpose | Scope |
|---|---|---|
| `vpcs.py`    | private network CIDR ranges  | global (no `node_id`) |
| `volumes.py` | per-VM data volumes          | node-bound |
| `vms.py`     | running microVMs             | node-bound |

`ALL` in `__init__.py` is the list registered with `DadarApp(tables=...)`.

Schema choices:

- IDs are stringified UUIDv4. Generated in Python so callers can return them in
  POST responses before the row is committed (rqlite's `last_insert_id`
  doesn't apply to TEXT PKs).
- `cidr` is a free-form string (e.g. `10.10.0.0/24`). Validated by Python at
  the router layer (`ipaddress.ip_network(strict=True)`), not by SQL.
- `vms.status` and `volumes.status` are TEXT, not CHECK-constrained. Migration
  cost outweighs the value — the router layer enforces the enum.
- No foreign keys at the SQL level. rqlite supports them but cross-table
  referential integrity isn't worth the deletion-order pain. The routers
  enforce it (`DELETE /vpcs/{id}` rejects when any VM still references it).
