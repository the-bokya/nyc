"""Per-VM directory setup: rootfs is a thin clone of the golden image, kernel
and ssh key are symlinked, teardown removes the dir and the clone LV."""
from nyc.client.env import setup, teardown
from nyc.client.volume import lv, names, pool
from nyc.config import LvmConfig


def _vg(tmp_path) -> str:
    cfg = LvmConfig(vg="nyc", thinpool="pool", device=None, node_folder=tmp_path,
                    loop_size_gb=20, base_rootfs_mb=4096)
    return pool.ensure("node-aaaa", cfg, tmp_path / "rootfs.ext4")


def _assets(tmp_path) -> dict:
    kernel, key = (tmp_path / n for n in ("vmlinux", "id_ed25519"))
    for f in (kernel, key):
        f.touch()
    return {"kernel": kernel, "ssh_key": key}


def test_setup_clones_rootfs_and_links_rest(tmp_path):
    vg, assets = _vg(tmp_path), _assets(tmp_path)
    paths = setup.run(tmp_path / "vms", "vm-1234", assets, vg, pool.GOLD_DEFAULT)
    assert lv.exists(vg, names.rootfs("vm-1234"))  # per-VM rootfs is a thin clone
    assert paths.rootfs.is_symlink() and str(paths.rootfs.readlink()) == f"/dev/{vg}/{names.rootfs('vm-1234')}"
    assert paths.kernel.resolve() == assets["kernel"]
    assert paths.ssh_key.resolve() == assets["ssh_key"]


def test_setup_is_idempotent(tmp_path):
    vg, assets = _vg(tmp_path), _assets(tmp_path)
    setup.run(tmp_path / "vms", "vm-x", assets, vg, pool.GOLD_DEFAULT)
    setup.run(tmp_path / "vms", "vm-x", assets, vg, pool.GOLD_DEFAULT)
    assert (tmp_path / "vms" / "vm-x").is_dir()
    assert lv.exists(vg, names.rootfs("vm-x"))


def test_teardown_removes_dir_and_clone(tmp_path):
    vg, assets = _vg(tmp_path), _assets(tmp_path)
    paths = setup.run(tmp_path / "vms", "vm-d", assets, vg, pool.GOLD_DEFAULT)
    teardown.run(paths.root, vg)
    assert not paths.root.exists()
    assert not lv.exists(vg, names.rootfs("vm-d"))
