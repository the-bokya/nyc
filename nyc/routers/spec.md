# routers

FastAPI plumbing. No SQL outside of the dadar ORM. No filesystem calls — they
belong in `nyc.client`. Routers compose: ORM read → client call → ORM write.

| File | Endpoint | Notes |
|---|---|---|
| `vpcs.py`      | `/vpcs`          | Global. No proxy. |
| `volumes.py`   | `/volumes`       | Node-bound (thin LVs). POST/PATCH/DELETE proxy via `_proxy.forward`. POST takes `size_mb` or `from_snapshot` (clone); PATCH `{size_mb}` resizes (`lvextend`+`resize2fs`). |
| `snapshots.py` | `/snapshots`, `/images` | Node-bound, generic over root + data disks. `POST /snapshots` takes `{volume_id}` (a data volume, `disk=data`) **or** `{vm_id}` (that VM's root LV, `disk=root`). `/images` promotes a snapshot to a golden via `from_snapshot` (inherits `disk`). Writes proxy to the resource's owner; reads serve from local raft. |
| `vms.py`        | `/vms`           | Node-bound. `POST /vms` runs the full lifecycle composer. `POST /vms/spawn` is the turnkey path (see below). |
| `reconcile.py`  | `/reconcile`     | POST triggers one immediate reconciler pass on the receiving node. |
| `_proxy.py`     | (helper)         | Looks up `nodes.host` + `nodes.http_port` and forwards via httpx over the private network. |
| `domains.py`    | `/domains`       | Global. `POST /domains {subdomain|fqdn, vm_id, port=80}` attaches a domain to a VM and enqueues a `proxy_reload` task. `DELETE /domains/{id}` detaches and reloads. |
| `public_ips.py` | `/vms/{id}/public-ip`, `/public-ips` | Node-bound (proxied to VM owner). POST acquires a free IP from the pool, binds it on the host, installs DNAT/SNAT rules, and inserts a `PublicIps` row. DELETE tears down the NAT and unbinds. |
| `tasks.py`      | `/vms/{id}/tasks`, `/tasks`, `/tasks/{id}` | `POST /vms/{id}/tasks {type, params?}` enqueues an async task on the VM's owner node. Poll `GET /tasks/{id}` for status + result. |
| `proxy.py`      | `/proxy`         | Turnkey. `POST /proxy` spawns the proxy VM, attaches a public IP, inserts a `Proxies` row, and enqueues `reverse_proxy_setup` + `proxy_reload` tasks. `GET /proxy` shows the VPC proxy, its public IP, and domain count. |

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
mem_mib=512, root_image=None, data_image=None}` — deliberately **no `vpc_id`,
no `node_id`**.

- **Network**: lands in the `default` /16 VPC (`nyc.defaults.ensure_default_vpc`,
  get-or-create).
- **Placement**: with no image, the receiving node picks a node at random and
  proxies there, pinning the choice with the `X-Nyc-Pin` header so the chosen
  node spawns locally instead of re-rolling. With `root_image`/`data_image`,
  placement is **pinned to the image's owner node** (clone is node-local today);
  if the two images name different nodes it's a 409, and the owner re-verifies
  `image.node_id == node_id` — see [`../../FUTURE.md`](../../FUTURE.md) on
  cross-node images. (`pin` never appears in the request body.)
- **Rootfs**: a thin clone of `root_image` (or `gold-default` when omitted)
  becomes the per-VM writable rootfs. `root_image` **must be `disk=root`** (a
  bootable lineage), else 400 — a data image used as root would never boot.
- **Volume**: a per-VM data volume (thin LV) is auto-created on the target node
  and tracked in `volumes` (named `<vm_name>-data`) — a thin clone of
  `data_image` if given (inheriting its size), otherwise a fresh empty ext4 of
  `size_mb`; its id is on the VM row.
- **Key + mount**: `ssh_key`, `/etc/resolv.conf`, and the `/home` data-volume
  fstab entry are written via offline debugfs edits (`vm.inject.run`) on the
  per-VM rootfs clone. The golden image is read-only and never modified.

`DELETE /vms/{id}` cascades: it detaches the VM's public IP (NAT + host unbind
+ backend release), deletes its `Domains` rows, and clears the `Proxies` row if
this VM was the VPC's proxy — before removing the VM row and tearing down the
firecracker env. The same cascade runs in `vms_pass` orphan teardown.

## Lifecycle: `POST /vms/{id}/{stop,start,reboot}`

Each is proxied to the owning node (same owner lookup as `DELETE`/`GET`) and
flips the `vms.status` column. **stop** → `stopped` (kills firecracker, keeps
netns/veth/tap/bridge/volume/dir for a cheap restart). **start** → `running`
(respawns firecracker from the on-disk `config.json`). **reboot** = stop+start.
The router resolves `ns`/`firecracker_bin` and calls the pure
`lifecycle.vm_stop`/`vm_start` composers (see `client/lifecycle/spec.md`). A
`stopped` VM keeps its dir, so the reconciler does not treat it as an orphan.
