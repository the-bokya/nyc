# reconciler

Per-node convergence task. DB is source of truth for *intent*; the local
filesystem and kernel state are the *observed reality*. The reconciler walks
the diff each interval and brings reality to match.

## Files

| File | Role |
|---|---|
| `pass_once.py`   | `run(client, node_id) → {vms, volumes, overlay}` — one shot, returns a report. |
| `vms_pass.py`    | Reconcile `vms` table against `<vms_dir>/*` |
| `volumes_pass.py`| Reconcile `volumes` table against `<volumes_dir>/*.ext4` |
| `overlay_pass.py`| Re-sync each local VPC's VXLAN head-end FDB to the current peer set. |
| `loop.py`        | asyncio background task, started by `nyc.app` on FastAPI lifespan startup |

## Behaviour

For each resource type:

1. Read all DB rows where `node_id == this node`.
2. Enumerate local resources (vm directories, volume files).
3. Compute `(orphans = local - expected)` and `(missing = expected - local)`.
4. Tear down orphans. (Future: recreate missing — punted for v1 because
   safely re-creating a VM requires also re-running the network setup with
   the original IP/VPC, which the DB has but the test surface is large.)

`POST /reconcile` runs `pass_once.run` synchronously and returns the report.
That's how tests assert reconciliation actually did something.

## Overlay pass

VM bring-up seeds a VPC's VXLAN flood list from the registry *at create time*,
but the peer set drifts as nodes join/leave. `overlay_pass` re-reconciles the
head-end FDB for every VPC that has a VM on this node: it reads the live peer
underlay IPs from the dadar `nodes` registry (via `nyc.peers`) and rewrites each
VXLAN's flood entries to exactly that set. No-op on a single host (loopback
node), where there is no tunnel to maintain. Returns `{"synced": [vpc_id, ...]}`.

## Interval

`NYC_RECONCILE_INTERVAL` env var (seconds, default 5). Set to a small value
in tests, large in production.

## Failure mode

The loop swallows exceptions per iteration. A panicking pass should NOT take
the whole node down — that would amplify a partial failure into a total one.
