# tests

NYC_BACKEND is forced to `fake` in `conftest.py` before any nyc module
imports. No mocks for the database — every test fixture spins up a real
rqlited in a temp dir on free ports (inherits the dadar pattern).

| File | What it covers |
|---|---|
| `conftest.py`             | rqlite + FastAPI fixtures, autouse privops state reset, `pool.ensure` (lifespan is bypassed) |
| `test_privops_fake.py`    | argv parser → STATE invariants |
| `test_lvm_pool.py`        | `lv` primitives + `pool.ensure` substrate (incl. thin independence) + lifespan-runs-on_startup |
| `test_client_env.py`      | env.setup clones rootfs from a golden + symlinks kernel/key; teardown removes dir + clone LV |
| `test_client_network.py`  | IP allocator, netns / bridge / tap wiring |
| `test_client_overlay.py`  | `overlay.vni_for`/`anycast_mac` determinism; `vxlan` ensure + FDB reconcile |
| `test_vpcs_crud.py`       | /vpcs full CRUD, validation, blocked-delete |
| `test_volumes_crud.py`    | /volumes full CRUD (thin LVs), validation, blocked-delete |
| `test_snapshots_crud.py`  | /snapshots (data + VM-root) + /images CRUD, spawn `root_image`/`data_image`, root-must-be-bootable guard, same-node + different-node rejection, deletion independence, volume-from-snapshot, resize, orphan prune |
| `test_vms_crud.py`        | /vms CRUD + stop/start/reboot, IP assignment, netns and bridge side-effects |
| `test_spawn.py`           | /vms/spawn turnkey path: default VPC, auto volume, rootfs clone + inject |
| `test_proxy.py`           | `_proxy._base_url` targets the registry `host`, not loopback |
| `test_reconciler.py`      | orphan VM dirs (+ rootfs LV) / volume LVs killed, known kept, TTL reaping |
| `test_overlay_pass.py`    | reconciler re-syncs each local VPC's VXLAN FDB; no-op on loopback |
| `test_stage_e2e.py`       | drives the staging script; asserts cross-node propagation |
