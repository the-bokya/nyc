# reconciler

Per-node convergence task. DB is source of truth for *intent*; the local
filesystem and kernel state are the *observed reality*. The reconciler walks
the diff each interval and brings reality to match.

## Files

| File | Role |
|---|---|
| `pass_once.py`    | `run(client, node_id) → {ttl, vms, volumes, snapshots, overlay, pubip}` — one shot, returns a report. |
| `ttl_pass.py`     | Delete local VMs older than `NYC_VM_TTL_MINUTES` (0/unset = off). |
| `vms_pass.py`     | Reconcile `vms` table against `<vms_dir>/*`; also prune `rootfs-*` LVs with no row. Calls teardown cascade. |
| `volumes_pass.py` | Reconcile `volumes` table against the `data-*` thin LVs in the node's VG. |
| `snapshots_pass.py`| Reconcile `snapshots` table against the `snap-*`/`gold-*` LVs (keeps reserved `gold-default`). |
| `overlay_pass.py` | Re-sync each local VPC's VXLAN head-end FDB to the current peer set. |
| `pubip_pass.py`   | Re-ensure host bind + NAT for every `PublicIps` row owned by this node. Survives reboots. |
| `loop.py`         | asyncio background task, started by `nyc.app` on FastAPI lifespan startup. |
| `executor.py`     | asyncio background task that atomically claims and runs `Tasks` rows for this node. One task per tick, in a thread. |
| `task_runner.py`  | Dispatch by `type`: `reverse_proxy_setup` → `proxy.push.setup`; `proxy_reload` → render Caddyfile + `proxy.push.reload`. |

## Behaviour

For each resource type:

1. Read all DB rows where `node_id == this node`.
2. Enumerate local resources (vm directories; the node VG's `data-`/`snap-`/
   `gold-`/`rootfs-` thin LVs, via `lv.list_lvs`).
3. Compute `(orphans = local - expected)` and `(missing = expected - local)`.
4. Tear down orphans (`lvremove` for LVs, `vm_down` for VM dirs — which also
   removes the rootfs clone LV). Recreating missing resources and restarting
   dead-but-`running` VMs are **not yet done** — the loop is one-directional
   (prunes excess, never enforces intent). See [`../../FUTURE.md`](../../FUTURE.md).

`POST /reconcile` runs `pass_once.run` synchronously and returns the report.
That's how tests assert reconciliation actually did something.

## TTL pass

`ttl_pass` runs **first** in each pass. Optional auto-expiry: when
`NYC_VM_TTL_MINUTES > 0` (deploy bakes it into the node's systemd unit from
`cluster.toml`'s `vm_ttl_minutes`), it deletes this node's VMs whose
`created_at` is older than the TTL — `vm_down` + drop the row, the same teardown
`DELETE /vms` uses (the auto data volume is not cascaded). At 0/unset it returns
`{"reaped": []}` immediately, so staging and TTL-less clusters are unaffected.
Running before the vms pass means a reaped VM's dir is already gone and is not
re-seen as an orphan.

## Overlay pass

VM bring-up seeds a VPC's VXLAN flood list from the registry *at create time*,
but the peer set drifts as nodes join/leave. `overlay_pass` re-reconciles the
head-end FDB for every VPC that has a VM on this node: it reads the live peer
underlay IPs from the dadar `nodes` registry (via `nyc.peers`) and rewrites each
VXLAN's flood entries to exactly that set. No-op on a single host (loopback
node), where there is no tunnel to maintain. Returns `{"synced": [vpc_id, ...]}`.

## Public-IP pass

`pubip_pass` is called from `pass_once.run` after every other pass. For each
`PublicIps` row with `node_id == this_node` and `status = attached`, it calls
`pubip.host.bind` + `pubip.nat.attach` (both idempotent). This re-applies the
`/32` address and DNAT/SNAT rules after a reboot, since `iptables` and `ip addr`
rules are not persistent across reboots.

## Executor

`executor.py` runs independently from the reconciler loop (separate asyncio task,
started in `app._on_startup`). Every `NYC_EXECUTOR_INTERVAL` seconds (default 5),
it claims one `pending` task for this node by updating `status='running'`, then
re-reads to confirm the win (handles concurrent multi-node claim races). The task
runs in a thread via `asyncio.to_thread` so blocking SSH work never stalls the
event loop. On completion, the row is updated to `succeeded`/`failed` with the
output/error as `result`.

## Interval

`NYC_RECONCILE_INTERVAL` env var (seconds, default 5). Set to a small value
in tests, large in production. `NYC_EXECUTOR_INTERVAL` (default 5) controls
the task executor independently.

## Failure mode

Both loops swallow exceptions per iteration. A panicking pass should NOT take
the whole node down — that would amplify a partial failure into a total one.
