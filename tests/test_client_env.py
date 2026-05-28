"""Per-VM directory setup: always copies rootfs, symlinks kernel + ssh key."""
from nyc.client.env import setup, teardown
from nyc.client.privops_fake import STATE


def test_setup_copies_rootfs_and_links_rest(tmp_path):
    rootfs, kernel, key = (tmp_path / n for n in ("rootfs.ext4", "vmlinux", "id_ed25519"))
    for f in (rootfs, kernel, key):
        f.touch()
    paths = setup.run(tmp_path / "vms", "vm-1234", {"rootfs": rootfs, "kernel": kernel, "ssh_key": key})
    assert any(dest == str(paths.rootfs) for _, dest in STATE["copies"])
    assert paths.kernel.is_symlink() and paths.kernel.resolve() == kernel
    assert paths.ssh_key.is_symlink() and paths.ssh_key.resolve() == key


def test_setup_is_idempotent(tmp_path):
    rootfs, kernel, key = (tmp_path / n for n in ("rootfs.ext4", "vmlinux", "id_ed25519"))
    for f in (rootfs, kernel, key):
        f.touch()
    assets = {"rootfs": rootfs, "kernel": kernel, "ssh_key": key}
    setup.run(tmp_path / "vms", "vm-x", assets)
    setup.run(tmp_path / "vms", "vm-x", assets)
    assert (tmp_path / "vms" / "vm-x").is_dir()


def test_teardown_removes_dir(tmp_path):
    rootfs, kernel, key = (tmp_path / n for n in ("rootfs.ext4", "vmlinux", "id_ed25519"))
    for f in (rootfs, kernel, key):
        f.touch()
    paths = setup.run(tmp_path / "vms", "vm-d", {"rootfs": rootfs, "kernel": kernel, "ssh_key": key})
    teardown.run(paths.root)
    assert not paths.root.exists()
