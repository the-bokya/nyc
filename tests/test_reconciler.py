"""Reconciler: kills orphans (resource exists, no DB row)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nyc.client.privops_fake import STATE
from nyc.client.env import setup as env_setup
from nyc.config import resolve
from nyc.tables import Vms


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


def _backdate(orm, vm_id, minutes):
    old = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    Vms(orm).docs.update(where={"id": vm_id}, set={"created_at": old})


def test_ttl_reaps_expired_vm(http, node, monkeypatch):
    monkeypatch.chdir(node["tmp_path"])
    monkeypatch.setenv("NYC_VM_TTL_MINUTES", "30")
    vpc = http.post("/vpcs", json={"name": "ttl", "cidr": "10.112.0.0/24"}).json()
    vm = http.post("/vms", json={"name": "old", "vpc_id": vpc["id"]}).json()
    _backdate(node["orm"], vm["id"], minutes=60)  # older than the 30-min TTL
    report = http.post("/reconcile").json()
    assert vm["id"] in report["ttl"]["reaped"]
    assert http.get(f"/vms/{vm['id']}").status_code == 404


def test_ttl_keeps_fresh_vm(http, node, monkeypatch):
    monkeypatch.chdir(node["tmp_path"])
    monkeypatch.setenv("NYC_VM_TTL_MINUTES", "30")
    vpc = http.post("/vpcs", json={"name": "fresh", "cidr": "10.113.0.0/24"}).json()
    vm = http.post("/vms", json={"name": "young", "vpc_id": vpc["id"]}).json()
    report = http.post("/reconcile").json()
    assert vm["id"] not in report["ttl"]["reaped"]
    assert http.get(f"/vms/{vm['id']}").status_code == 200


def test_ttl_disabled_keeps_old_vm(http, node, monkeypatch):
    monkeypatch.chdir(node["tmp_path"])
    monkeypatch.delenv("NYC_VM_TTL_MINUTES", raising=False)  # TTL off
    vpc = http.post("/vpcs", json={"name": "off", "cidr": "10.114.0.0/24"}).json()
    vm = http.post("/vms", json={"name": "immortal", "vpc_id": vpc["id"]}).json()
    _backdate(node["orm"], vm["id"], minutes=999)
    report = http.post("/reconcile").json()
    assert report["ttl"]["reaped"] == []
    assert http.get(f"/vms/{vm['id']}").status_code == 200
