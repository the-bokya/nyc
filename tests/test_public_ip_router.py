"""Tests for /vms/{id}/public-ip and /public-ips with fake backend + config env vars."""
import os

import pytest

# Fake a public IP pool via env (picked up by nyc.config.pubip)
os.environ.setdefault("NYC_PUBLIC_IPS", "203.0.113.10,203.0.113.11")
os.environ.setdefault("NYC_PUBLIC_IFACE", "eth0")
os.environ.setdefault("NYC_PUBIP_PROVIDER", "scaleway")


@pytest.fixture
def vpc(http):
    return http.post("/vpcs", json={"name": "pitest", "cidr": "10.3.0.0/24"}).json()


@pytest.fixture
def vm(http, vpc):
    return http.post("/vms", json={"name": "piv", "vpc_id": vpc["id"]}).json()


def test_attach_public_ip(http, vm):
    r = http.post(f"/vms/{vm['id']}/public-ip")
    assert r.status_code == 201
    d = r.json()
    assert d["address"] in ("203.0.113.10", "203.0.113.11")
    assert d["vm_id"] == vm["id"]
    assert d["status"] == "attached"


def test_list_public_ips(http, vm):
    http.post(f"/vms/{vm['id']}/public-ip")
    listing = http.get("/public-ips").json()
    assert any(p["vm_id"] == vm["id"] for p in listing)


def test_detach_public_ip(http, vm):
    http.post(f"/vms/{vm['id']}/public-ip")
    assert http.delete(f"/vms/{vm['id']}/public-ip").status_code == 204
    listing = http.get("/public-ips").json()
    assert not any(p["vm_id"] == vm["id"] for p in listing)


def test_nat_rules_created(http, vm):
    from nyc.client.privops_fake import STATE
    r = http.post(f"/vms/{vm['id']}/public-ip")
    address = r.json()["address"]
    vm_ip = vm["ip"]
    pre_rules = STATE["iptables"]["nat"]["rules"].get("NYC-PREROUTING", [])
    post_rules = STATE["iptables"]["nat"]["rules"].get("NYC-POSTROUTING", [])
    assert ("-d", address, "-j", "DNAT", "--to-destination", vm_ip) in pre_rules
    assert ("-s", vm_ip, "-j", "SNAT", "--to-source", address) in post_rules


def test_attach_unknown_vm(http):
    r = http.post("/vms/no-such-vm/public-ip")
    assert r.status_code == 404
