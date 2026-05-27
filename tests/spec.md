# tests

NYC_BACKEND is forced to `fake` in `conftest.py` before any nyc module
imports. No mocks for the database — every test fixture spins up a real
rqlited in a temp dir on free ports (inherits the dadar pattern).

| File | What it covers |
|---|---|
| `conftest.py`             | rqlite + FastAPI fixtures, autouse privops state reset |
| `test_privops_fake.py`    | argv parser → STATE invariants |
| `test_client_env.py`      | env.setup symlinks, teardown |
| `test_client_network.py`  | IP allocator, netns / bridge / tap wiring |
| `test_vpcs_crud.py`       | /vpcs full CRUD, validation, blocked-delete |
| `test_volumes_crud.py`    | /volumes full CRUD, validation, blocked-delete |
| `test_vms_crud.py`        | /vms CRUD, IP assignment, netns and bridge side-effects |
| `test_reconciler.py`      | orphan VM dirs and volume files get killed; known resources preserved |
| `test_stage_e2e.py`       | drives the staging script; asserts cross-node propagation |
