"""Reconciler: kills orphans (resource exists, no DB row)."""
from pathlib import Path

from nyc.client.privops_fake import STATE
from nyc.client.env import setup as env_setup
from nyc.config import resolve


def test_reconcile_kills_orphan_vm_dir(http, node, tmp_path, monkeypatch):
    monkeypatch.chdir(node["tmp_path"])
    paths = resolve()
    assets = {"rootfs": tmp_path / "rootfs", "kernel": tmp_path / "kernel", "ssh_key": tmp_path / "key"}
    for p in assets.values():
        p.touch()
    env_setup.run(paths.vms_dir, "orphan-vm-id-here", assets)
    report = http.post("/reconcile").json()
    assert "orphan-vm-id-here" in report["vms"]["killed"]
    assert not (paths.vms_dir / "orphan-vm-id-here").exists()


def test_reconcile_kills_orphan_volume_file(http, node, monkeypatch):
    monkeypatch.chdir(node["tmp_path"])
    paths = resolve()
    paths.volumes_dir.mkdir(parents=True, exist_ok=True)
    orphan_path = str(paths.volumes_dir / "orphan-vol.ext4")
    STATE["files"][orphan_path] = 1024
    report = http.post("/reconcile").json()
    assert "orphan-vol.ext4" in report["volumes"]["deleted"]
    assert orphan_path not in STATE["files"]


def test_reconcile_preserves_known_vm(http, monkeypatch, node):
    monkeypatch.chdir(node["tmp_path"])
    vpc = http.post("/vpcs", json={"name": "kept", "cidr": "10.111.0.0/24"}).json()
    vm = http.post("/vms", json={"name": "keep", "vpc_id": vpc["id"]}).json()
    report = http.post("/reconcile").json()
    assert vm["id"] not in report["vms"]["killed"]
    assert http.get(f"/vms/{vm['id']}").status_code == 200
