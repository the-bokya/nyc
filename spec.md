# nyc — spec

Authoritative reference for the `nyc` submodule. If the code disappears,
this doc must reproduce it.

## Identity

- `nyc` is a **downstream dadar app**. It depends on `dadar` for: the ORM, the
  rqlite supervisor, the FastAPI factory, the `Nodes` table and the `dadar`
  CLI. `nyc` adds three tables, four routers, a Firecracker client, a
  reconciler, and a staging script.
- The `dadar` framework supplies the cluster fabric. Every `nyc` node is a
  `dadar` node folder with a `[tool.dadar]` pointer at `nyc.app`.

## Domain model

Three resources, all replicated through raft via dadar's ORM:

| Resource | Scope | Key fields |
|---|---|---|
| `vpcs`    | cluster-wide | `id` (uuid), `name`, `cidr` (e.g. `10.10.0.0/24`), `created_at` |
| `volumes` | node-bound   | `id` (uuid), `node_id`, `name`, `size_mb`, `path`, `status`, `created_at` |
| `vms`     | node-bound   | `id` (uuid), `node_id`, `name`, `vpc_id` (FK), `data_volume_id` (FK\|null), `ip`, `ssh_pubkey_path`, `status`, `created_at` |

- `node_id` is a UUIDv4 from dadar's `Nodes` table.
- `vms.ip` is allocated from `vpcs.cidr` and is unique within the VPC.
- `vms.status` ∈ `{pending, running, stopped, failed}`.
- `volumes.status` ∈ `{pending, ready, attached, failed}`.

## REST API

| Method | Path | Body | Notes |
|---|---|---|---|
| GET    | /vpcs                | —                                            | list across cluster |
| POST   | /vpcs                | `{name, cidr}`                               | global resource |
| GET    | /vpcs/{id}           | —                                            | one |
| DELETE | /vpcs/{id}           | —                                            | 409 if any VM still attached |
| GET    | /volumes             | —                                            | list across cluster |
| POST   | /volumes             | `{name, size_mb, node_id?}`                  | if `node_id` omitted, pick this node |
| GET    | /volumes/{id}        | —                                            | |
| DELETE | /volumes/{id}        | —                                            | 409 if attached to a running VM |
| GET    | /vms                 | —                                            | list across cluster |
| POST   | /vms                 | `{name, vpc_id, data_volume_id?, node_id?}`  | creates env + netns + tap + boots |
| GET    | /vms/{id}            | —                                            | includes live `status` from client |
| DELETE | /vms/{id}            | —                                            | full teardown |
| POST   | /reconcile           | —                                            | force a reconciler pass on the receiving node |
| GET    | /health, /nodes      | (inherited from dadar core)                  | |

A write that targets a different node's `node_id` is proxied: the receiving
node looks up the target in `nodes`, forwards the HTTP request, and returns
the response unchanged. Reads serve from local rqlite (raft is the
consistency model).

## Firecracker client (decoupled from REST)

`nyc.client` is a pure-Python library with no HTTP server in sight. It exposes
one function per action. Routers import and call these.

```
client/env/setup.py          run(vm_dir, vm_id) -> dict[str, Path]
client/env/teardown.py       run(vm_dir) -> None
client/vm/create.py          run(vm_dir, cfg)  -> int (pid)
client/vm/boot.py            run(vm_dir, cfg)  -> None    # configures + InstanceStart
client/vm/kill.py            run(vm_dir)       -> None
client/vm/status.py          run(vm_dir)       -> str     # running|stopped
client/vm/ssh.py             run(ip, key)      -> str     # convenience cmd-line
client/network/namespace.py  create(name)/delete(name)
client/network/tap.py        create(ns, tap, ip, peer_ip)/delete(ns, tap)
client/network/bridge.py     ensure(name, cidr)/delete(name)
client/network/allocate.py   pick_ip(cidr, used) -> str
client/volume/create.py      run(path, size_mb) -> None
client/volume/delete.py      run(path) -> None
client/volume/attach.py      run(vm_dir, volume_path) -> Path  # symlink into vm_dir
```

Backend selection: `nyc.client.privops` exposes `run(argv: list[str])`. In
`real` mode it shells out via `sudo -n`; in `fake` mode it mutates an
in-memory state dict (netns set, tap dict, bridge dict, ip routes). The env
var `NYC_BACKEND` (default `fake`) chooses which.

## Reconciler

A per-node asyncio task wakes every `NYC_RECONCILE_INTERVAL` seconds (default
5). Each pass:

1. Read `vms`, `volumes` for `node_id == this node`.
2. List local resources via the client (vm dirs on disk, netns by prefix,
   volume files in the volumes dir).
3. For each local resource without a DB row → tear it down.
4. For each DB row without a backing resource → recreate it.

A `POST /reconcile` endpoint triggers an immediate pass and waits for it.

## Directory-level isolation

Every node folder owns its `vms/`, `volumes/`, and netns/tap/bridge names.
Names are derived from the **VM/volume/VPC UUID**, which is globally unique —
two nodes on the same host never collide. Bridge names are prefixed with the
node short-id (`br-<6char>-<6char>`) to also stay unique in the host
namespace.

## Staging

`scripts/stage.sh N` boots N dadar nodes in `./stage/node{1..N}/`, populates
`assets/` with `vmlinux`, `rootfs.ext4`, and `firecracker` (idempotent), and
runs the e2e test in `tests/test_stage_e2e.py`. Defaults to fake backend;
`--real` flips to live Firecracker (requires `/dev/kvm` and passwordless
sudo).

The same e2e test against the same staging script must pass on a single
machine (this repo) and on multiple bare-metal nodes once the cross-node
overlay lands. Directory-level isolation is what makes that true.
