# tables

Eight ORM models, one file each. Each subclasses `dadar.orm.ORM` and declares
`name` + `fields` only — no methods. CRUD goes through `Vpcs(client).docs.*`.

| File | Purpose | Scope |
|---|---|---|
| `vpcs.py`       | private network CIDR ranges                                           | global |
| `volumes.py`    | per-VM data volumes (thin LVs)                                        | node-bound |
| `vms.py`        | running microVMs                                                       | node-bound |
| `snapshots.py`  | read-only thin images: `role` ∈ {snapshot, golden}, `disk` ∈ {root, data} | node-bound |
| `public_ips.py` | public IP ↔ VM binding; `provider` ∈ {scaleway, static}; `status` ∈ {attached, released, failed} | node-bound |
| `domains.py`    | subdomain → VM routing intent; `fqdn` is UNIQUE                       | global |
| `tasks.py`      | async guest operations; `type` ∈ {reverse_proxy_setup, proxy_reload}; `status` ∈ {pending, running, succeeded, failed} | node-bound |
| `proxies.py`    | one reverse-proxy VM per VPC; `vpc_id` is UNIQUE                      | global |

`ALL` in `__init__.py` is the list registered with `DadarApp(tables=...)`.

Schema choices:

- IDs are stringified UUIDv4. Generated in Python so callers can return them in
  POST responses before the row is committed (rqlite's `last_insert_id`
  doesn't apply to TEXT PKs).
- `cidr` is a free-form string (e.g. `10.10.0.0/24`). Validated by Python at
  the router layer (`ipaddress.ip_network(strict=True)`), not by SQL.
- `vms.ip` uniqueness within a VPC is enforced only in Python (`pick_ip`); there
  is no `UNIQUE(vpc_id, ip)` index, so concurrent creates can race. See
  [`../../FUTURE.md`](../../FUTURE.md).
- `vms.status` and `volumes.status` are TEXT, not CHECK-constrained. Migration
  cost outweighs the value — the router layer enforces the enum.
- `vms.vcpu_count` / `vms.mem_mib` carry the VM's machine shape (defaults 1 /
  512). Persisted so a stop→start can rebuild the same firecracker config.
- The `default` VPC (a /16, see `nyc.defaults`) is the network `POST /vms/spawn`
  uses. It is an ordinary row enforced unique by `vpcs.name`; get-or-create is
  race-safe (a losing concurrent insert just re-reads the winner).
- No foreign keys at the SQL level. rqlite supports them but cross-table
  referential integrity isn't worth the deletion-order pain. The routers
  enforce it (`DELETE /vpcs/{id}` rejects when any VM still references it).
- `volumes.path` holds the LV **device node** `/dev/<vg>/<lv>` (the LV name is
  `data-<id>`, derived, not stored). The volume is a thin LV; `size_mb` is its
  thin *virtual* size — physical usage grows on write.
- `snapshots` has two independent axes: `role` ∈ {snapshot, golden} (a
  point-in-time freeze vs a cloneable image) and `disk` ∈ {root, data} (a VM
  rootfs lineage vs a data volume). `disk` is set from the snapshot source — a
  `volume_id` gives `data`, a `vm_id` gives `root` (snapshots the VM's
  `rootfs-<id>` LV) — and a golden inherits it. Only a `disk=root` image is a
  valid boot source, which the spawn router enforces (`root_image` → 400 if not
  root). `parent` is the id it derived from (volume/VM for a snapshot, snapshot
  for a golden; null for `gold-default`). `lv_name` is the backing LV. Names are
  router-enforced, not SQL. Thin independence means **no cascade**: deleting a
  volume/VM with snapshots, or a golden with clones, is allowed and harmless.
