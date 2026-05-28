# routers

FastAPI plumbing. No SQL outside of the dadar ORM. No filesystem calls — they
belong in `nyc.client`. Routers compose: ORM read → client call → ORM write.

| File | Endpoint | Notes |
|---|---|---|
| `vpcs.py`      | `/vpcs`          | Global. No proxy. |
| `volumes.py`   | `/volumes`       | Node-bound. POST/DELETE proxy via `_proxy.forward`. |
| `vms.py`       | `/vms`           | Node-bound. `POST /vms` runs the full lifecycle composer. `POST /vms/spawn` is the turnkey path (see below). |
| `reconcile.py` | `/reconcile`     | POST triggers one immediate reconciler pass on the receiving node. |
| `_proxy.py`    | (helper)         | Looks up `nodes.host` + `nodes.http_port` and forwards via httpx over the private network. |

Patterns:
- 400 for bad input (unknown vpc_id, bad cidr).
- 404 for missing rows.
- 409 for blocked deletes (VPC has VMs, volume attached).
- 201 for create, 204 for delete.

GET responses for `/vms` and `/vms/{id}` include a `live_status` field from
the client (live process check) on top of the DB-recorded status. The DB
status is the **intended** state; `live_status` is the **observed** state.
The reconciler reconciles them.

`live_status` is a *per-owner* observation — the firecracker process only
exists on the node that owns the VM. So `GET /vms/{id}` proxies to the owner
when the VM isn't local, and `GET /vms` merges each owning node's own view:
it asks every other owner for its VMs with the `X-Nyc-Local` header set, which
makes that call return only the owner's rows without re-fanning out (one hop
per owner, not a broadcast). Without this, a non-owner node would always report
`stopped` for remote VMs because it has no local process to probe.

## `POST /vms/spawn`

Turnkey VM creation. Body is `{vm_name, ssh_key, size_mb=1024, vcpu_count=1,
mem_mib=512}` — deliberately **no `vpc_id`, no `node_id`**.

- **Network**: lands in the `default` /16 VPC (`nyc.defaults.ensure_default_vpc`,
  get-or-create).
- **Placement**: the receiving node picks a node at random from the registry
  and proxies the request there, pinning the choice with the `X-Nyc-Pin`
  header so the chosen node spawns locally instead of re-rolling. (`pin`
  never appears in the request body — it is an internal hop, not API surface.)
- **Volume**: a per-VM data volume is auto-created on the target node and
  tracked in `volumes` (named `<vm_name>-data`); its id is stored on the VM row.
- **Key + mount**: `ssh_key`, `/etc/resolv.conf`, and the `/home` data-volume
  fstab entry are written via offline debugfs edits (`vm.inject.run`) on the
  per-VM rootfs copy. The shared base image is never modified.

`DELETE /vms/{id}` currently removes the VM only — it does **not** cascade to
the auto-created volume. (Open question for the lifecycle work: should it?)
