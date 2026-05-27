"""Per-VM directory setup: one method, idempotent, symlinks to shared assets."""
from nyc.client.env import setup, teardown


def test_setup_creates_all_links(tmp_path):
    rootfs, kernel, key = (tmp_path / n for n in ("rootfs.ext4", "vmlinux", "id_ed25519"))
    for f in (rootfs, kernel, key):
        f.touch()
    paths = setup.run(tmp_path / "vms", "vm-1234", {"rootfs": rootfs, "kernel": kernel, "ssh_key": key})
    assert paths.rootfs.is_symlink() and paths.rootfs.resolve() == rootfs
    assert paths.kernel.is_symlink() and paths.kernel.resolve() == kernel
    assert paths.ssh_key.is_symlink() and paths.ssh_key.resolve() == key


def test_setup_is_idempotent(tmp_path):
    rootfs, kernel, key = (tmp_path / n for n in ("rootfs.ext4", "vmlinux", "id_ed25519"))
    for f in (rootfs, kernel, key):
        f.touch()
    assets = {"rootfs": rootfs, "kernel": kernel, "ssh_key": key}
    setup.run(tmp_path / "vms", "vm-x", assets)
    setup.run(tmp_path / "vms", "vm-x", assets)
    assert (tmp_path / "vms" / "vm-x" / "rootfs.ext4").is_symlink()


def test_teardown_removes_dir(tmp_path):
    rootfs, kernel, key = (tmp_path / n for n in ("rootfs.ext4", "vmlinux", "id_ed25519"))
    for f in (rootfs, kernel, key):
        f.touch()
    paths = setup.run(tmp_path / "vms", "vm-d", {"rootfs": rootfs, "kernel": kernel, "ssh_key": key})
    teardown.run(paths.root)
    assert not paths.root.exists()
