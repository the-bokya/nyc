# nyc — spec

Authoritative reference for the `nyc` submodule. If the code disappears, this
doc plus the per-directory `spec.md`s must reproduce it. Forward-looking gaps
live in [`FUTURE.md`](FUTURE.md); cross-node networking in
[`NETWORKING.md`](NETWORKING.md).

## Identity

- `nyc` is a **downstream dadar app**: it depends on `dadar` for the ORM, the
  rqlite supervisor, the FastAPI factory, the `Nodes` table, and the `dadar`
  CLI. It adds three tables, four routers, a Firecracker client, a reconciler,
  and the staging + deploy scripts. Dependency is one-way (`nyc → dadar`).
- Every `nyc` node is a `dadar` node folder with a `[tool.dadar]` pointer at
  `nyc.app`.

## Domain model

Three resources, all replicated through raft via dadar's ORM:

| Resource | Scope | Key fields |
|---|---|---|
| `vpcs`      | cluster-wide | `id`, `name` (UNIQUE), `cidr`, `created_at` |
| `volumes`   | node-bound   | `id`, `node_id`, `name`, `size_mb`, `path` (LV device node), `status`, `created_at` |
| `vms`       | node-bound   | `id`, `node_id`, `name`, `vpc_id`, `data_volume_id\|null`, `ip`, `ssh_pubkey_path`, `vcpu_count`, `mem_mib`, `status`, `created_at` |
| `snapshots` | node-bound   | `id`, `node_id`, `name`, `role` (`snapshot`\|`golden`), `disk` (`root`\|`data`), `parent\|null`, `lv_name`, `size_mb`, `created_at` |

- `id`s are stringified UUIDv4; `node_id` is a UUID from dadar's `Nodes` table.
- All VM storage is **LVM thin volumes** in a per-node volume group's thin pool:
  data volumes, snapshots, golden images, and per-VM rootfs overlays are all
  thin LVs. A golden is a read-only snapshot; a VM's rootfs is a writable thin
  *clone* of a golden (no full copy). Thin snapshots are independent — deleting
  one never breaks clones of it. Substrate + API: `client/volume/spec.md`.
- `vms.ip` is allocated from `vpcs.cidr`, unique within the VPC (enforced in
  Python today — see [`FUTURE.md`](FUTURE.md) on the allocation race).
- `vms.status` ∈ `{pending, running, stopped, failed}`;
  `volumes.status` ∈ `{pending, ready, attached, failed}`. Enforced by the
  router layer, not SQL (see `tables/spec.md`).

## REST API

| Method | Path | Body | Notes |
|---|---|---|---|
| GET/POST/GET/DELETE | /vpcs[/{id}]    | POST `{name, cidr}`                       | global; DELETE 409 if any VM attached |
| GET/POST/GET/PATCH/DELETE | /volumes[/{id}] | POST `{name, size_mb \| from_snapshot, node_id?}`; PATCH `{size_mb}` | node-bound thin LV; PATCH resizes; DELETE 409 if attached |
| GET/POST/GET/DELETE | /snapshots[/{id}] | POST `{name, volume_id\|vm_id}`        | node-bound; read-only thin freeze of a data volume (`disk=data`) or a VM's root (`disk=root`) |
| GET/POST/GET/DELETE | /images[/{id}]    | POST `{name, from_snapshot}`            | node-bound; golden image (inherits the snapshot's `disk`) |
| GET/POST/GET/DELETE | /vms[/{id}]     | POST `{name, vpc_id, data_volume_id?, node_id?}` | node-bound; full bring-up/teardown |
| POST | /vms/spawn            | `{vm_name, ssh_key, size_mb?, vcpu_count?, mem_mib?, root_image?, data_image?}` | turnkey: default VPC, key injected; `root_image` (must be `disk=root`) + `data_image` clone goldens and pin to their node; else random node + `gold-default` + fresh data ext4 |
| POST | /vms/{id}/stop\|start\|reboot | —                                | proxied to owner; flips `vms.status` |
| POST | /reconcile           | —                                                | force one reconciler pass on the receiving node |
| GET  | /health, /nodes      | (inherited from dadar core)                      | |

A write targeting another node's `node_id` (or a read of a remote VM's
`live_status`) is **proxied**: the receiver looks the target up in `nodes`,
forwards the HTTP call over the private network, and returns the response
unchanged (`routers/_proxy.py`). Plain reads serve from local rqlite (raft is
the consistency model). Routers, proxy semantics, and the spawn/lifecycle paths
are detailed in `routers/spec.md`.

## Firecracker client (decoupled from REST)

`nyc.client` is pure Python — no HTTP server, no dadar imports. Routers and the
reconciler import its actions. One file per action, each public verb ≤12 lines.
**Per-function contracts live in the per-directory specs** (not restated here,
where they drift): `client/spec.md` (module map + `vm_up`/`vm_down`
composition), then `client/{env,vm,network,volume,lifecycle}/spec.md`.

`client/privops.run(argv)` is the only place that branches on backend: `real`
shells `sudo -n …` (the file-only ops in `_NO_SUDO` run unprivileged so the
node user keeps ownership), `fake` mutates an in-memory `STATE`. `NYC_BACKEND`
(default `fake`) selects which.

## Reconciler

A per-node asyncio task (every `NYC_RECONCILE_INTERVAL`s, default 5) plus a
synchronous `POST /reconcile`. Each pass reads this node's DB rows, enumerates
local resources (VM dirs and the node VG's thin LVs — data/snapshot/golden/
rootfs), and **tears down orphans** (resource present, no row). It also reaps
TTL-expired VMs and re-syncs each local VPC's VXLAN flood list. Recreating rows
whose backing resource vanished, and restarting dead-but-`running` VMs, are
**not yet done** — see `reconciler/spec.md` and [`FUTURE.md`](FUTURE.md).

## Directory-level isolation

Every node folder owns its `vms/` dir and netns/tap/bridge names; its storage
is a per-node LVM volume group (the configured device in prod, or a per-node
loopback file in single-host staging — VG name `<vg>-<node[:8]>` so staged
nodes never share a VG). Resource names derive from the VM/volume/VPC UUID
(globally unique), and bridge/VXLAN names carry the node short-id (`br-<4>-<4>`),
so two nodes on one host never collide. (Truncation-collision risk at scale:
[`FUTURE.md`](FUTURE.md).)

## Staging

`scripts/stage.sh N` boots N dadar nodes in `./stage/node{1..N}/`, populates
`assets/`, and runs `tests/test_stage_e2e.py`. Defaults to the fake backend;
`--real` flips to live Firecracker (`/dev/kvm` + passwordless sudo). The same
e2e test must pass single-host here and on a bare-metal cluster — directory
isolation is what makes that true. Bare-metal deploy: `scripts/spec.md`.
