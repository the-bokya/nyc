"""LVM substrate: lv primitives + pool.ensure, against the fake backend.

No DB, no node fixture — just the in-memory LVM model in privops_fake.STATE.
The autouse reset_state fixture (conftest) gives each test a clean slate.
"""
from nyc.client.volume import lv, pool
from nyc.config import LvmConfig


def _cfg(tmp_path) -> LvmConfig:
    return LvmConfig(vg="nyc", thinpool="pool", device=None,
                     node_folder=tmp_path, loop_size_gb=20, base_rootfs_mb=4096)


def test_ensure_builds_substrate(tmp_path):
    vg = pool.ensure("node-aaaa", _cfg(tmp_path), tmp_path / "rootfs.ext4")
    assert vg == "nyc-node-aaa"  # loopback => per-node vg (vg + node_id[:8])
    assert lv.vg_exists(vg)
    assert {lv.exists(vg, n) for n in ("pool", "base-rootfs", "gold-default")} == {True}


def test_ensure_is_idempotent(tmp_path):
    cfg = _cfg(tmp_path)
    vg = pool.ensure("node-aaaa", cfg, tmp_path / "rootfs.ext4")
    before = sorted(l["lv_name"] for l in lv.list_lvs(vg))
    pool.ensure("node-aaaa", cfg, tmp_path / "rootfs.ext4")
    assert sorted(l["lv_name"] for l in lv.list_lvs(vg)) == before


def test_default_golden_is_readonly_snapshot_of_base(tmp_path):
    from nyc.client.privops_fake import STATE
    vg = pool.ensure("node-aaaa", _cfg(tmp_path), tmp_path / "rootfs.ext4")
    gold = STATE["lvm"]["lvs"][(vg, "gold-default")]
    assert gold["origin"] == "base-rootfs"
    assert gold["attr"].startswith("Vr")  # read-only thin snapshot


def test_thin_clone_then_remove_is_independent(tmp_path):
    vg = pool.ensure("node-aaaa", _cfg(tmp_path), tmp_path / "rootfs.ext4")
    lv.clone(vg, "gold-default", "rootfs-vm1")
    assert lv.exists(vg, "rootfs-vm1")
    # deleting the golden must not remove the clone (thin independence)
    lv.remove(vg, "gold-default")
    assert not lv.exists(vg, "gold-default")
    assert lv.exists(vg, "rootfs-vm1")


def test_create_thin_returns_device_path(tmp_path):
    vg = pool.ensure("node-aaaa", _cfg(tmp_path), tmp_path / "rootfs.ext4")
    dev = lv.create_thin(vg, "pool", "data-xyz", 64)
    assert dev == f"/dev/{vg}/data-xyz"
    assert lv.exists(vg, "data-xyz")
    lv.remove(vg, "data-xyz")
    assert not lv.exists(vg, "data-xyz")


def test_lifespan_on_startup_builds_substrate(node, monkeypatch):
    """dadar run wires on_startup into the FastAPI lifespan; unit tests bypass
    it, so prove here that entering the lifespan really runs pool.ensure."""
    from fastapi.testclient import TestClient

    from dadar.api import build
    from nyc.app import app as nyc_app
    from nyc.client import privops
    from nyc.config import volume_vg

    monkeypatch.chdir(node["tmp_path"])
    privops.reset_state()  # wipe what the fixture's pool.ensure built
    vg = volume_vg(node["node_id"])
    assert not lv.exists(vg, pool.GOLD_DEFAULT)
    app = build(node["orm"], node["node_id"], user_routers=nyc_app.routers,
                on_startup=nyc_app.on_startup, on_shutdown=nyc_app.on_shutdown)
    with TestClient(app):  # entering the context runs lifespan startup
        assert lv.exists(vg, pool.GOLD_DEFAULT)
