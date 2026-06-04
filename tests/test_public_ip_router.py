"""Tests for /vms/{id}/public-ip and /public-ips with fake backend + config env vars."""
import json
import os

import pytest

os.environ.setdefault("NYC_PUBLIC_IPS", "203.0.113.10|de:ad:be:ef:00:01,203.0.113.11|de:ad:be:ef:00:02")
os.environ.setdefault("NYC_PUBLIC_IFACE", "ens3")
os.environ.setdefault("NYC_PUBLIC_BRIDGE", "pub0")
os.environ.setdefault("NYC_PUBIP_GATEWAY", "62.210.0.1")


@pytest.fixture
def vpc(http):
    return http.post("/vpcs", json={"name": "pitest", "cidr": "10.3.0.0/24"}).json()


@pytest.fixture
def vm(http, vpc):
    return http.post("/vms", json={"name": "piv", "vpc_id": vpc["id"]}).json()


def test_attach_public_ip_creates_row(http, vm):
    r = http.post(f"/vms/{vm['id']}/public-ip")
    assert r.status_code == 201
    d = r.json()
    assert d["address"] in ("203.0.113.10", "203.0.113.11")
    assert d["vm_id"] == vm["id"]
    assert d["status"] == "attached"
    assert d["mac"] in ("de:ad:be:ef:00:01", "de:ad:be:ef:00:02")


def test_list_public_ips(http, vm):
    http.post(f"/vms/{vm['id']}/public-ip")
    listing = http.get("/public-ips").json()
    assert any(p["vm_id"] == vm["id"] for p in listing)


def test_detach_public_ip(http, vm):
    http.post(f"/vms/{vm['id']}/public-ip")
    assert http.delete(f"/vms/{vm['id']}/public-ip").status_code == 204
    listing = http.get("/public-ips").json()
    assert not any(p["vm_id"] == vm["id"] for p in listing)


def test_attach_wires_eth1_nic(http, vm, node):
    """After attach, the Firecracker config must have an eth1 entry."""
    from nyc.config import resolve
    from nyc.client.env.paths import for_vm

    http.post(f"/vms/{vm['id']}/public-ip")

    paths = for_vm(resolve().vms_dir, vm["id"])
    cfg = json.loads(paths.config.read_text())
    nics = cfg["network-interfaces"]
    assert len(nics) == 2
    eth1 = nics[1]
    assert eth1["iface_id"] == "eth1"
    assert eth1["host_dev_name"] == "tap1"
    assert eth1["guest_mac"] in ("de:ad:be:ef:00:01", "de:ad:be:ef:00:02")


def test_attach_enslaves_pvh_to_pub0(http, vm):
    """pvh-* (public veth host side) must be enslaved to pub0 after attach."""
    from nyc.client.privops_fake import STATE

    http.post(f"/vms/{vm['id']}/public-ip")
    pvh = f"pvh-{vm['id'][:8]}"
    assert pvh in STATE["links"]
    assert STATE["links"][pvh].get("master") == "pub0"


def test_detach_removes_eth1_from_nic_config(http, vm, node):
    """After detach, config.json must have only eth0."""
    from nyc.config import resolve
    from nyc.client.env.paths import for_vm

    http.post(f"/vms/{vm['id']}/public-ip")
    http.delete(f"/vms/{vm['id']}/public-ip")

    paths = for_vm(resolve().vms_dir, vm["id"])
    cfg = json.loads(paths.config.read_text())
    assert len(cfg["network-interfaces"]) == 1
    assert cfg["network-interfaces"][0]["iface_id"] == "eth0"


def test_attach_unknown_vm(http):
    r = http.post("/vms/no-such-vm/public-ip")
    assert r.status_code == 404


def test_double_attach_uses_second_pool_entry(http, vm, vpc):
    """Two VMs should each get a different IP from the pool."""
    vm2 = http.post("/vms", json={"name": "piv2", "vpc_id": vpc["id"]}).json()
    r1 = http.post(f"/vms/{vm['id']}/public-ip")
    r2 = http.post(f"/vms/{vm2['id']}/public-ip")
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["address"] != r2.json()["address"]
