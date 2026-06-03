"""Tests for the /domains router."""
import os

import pytest

os.environ.setdefault("NYC_DOMAIN", "example.com")


@pytest.fixture
def vpc(http):
    return http.post("/vpcs", json={"name": "net2", "cidr": "10.2.0.0/24"}).json()


@pytest.fixture
def vm(http, vpc):
    return http.post("/vms", json={"name": "dv", "vpc_id": vpc["id"]}).json()


def test_create_domain_with_fqdn(http, vm):
    r = http.post("/domains", json={"vm_id": vm["id"], "fqdn": "a.example.com"})
    assert r.status_code == 201
    d = r.json()
    assert d["fqdn"] == "a.example.com"
    assert d["vm_id"] == vm["id"]


def test_create_domain_with_subdomain(http, vm):
    r = http.post("/domains", json={"vm_id": vm["id"], "subdomain": "b"})
    assert r.status_code == 201
    assert r.json()["fqdn"] == "b.example.com"


def test_list_domains(http, vm):
    http.post("/domains", json={"vm_id": vm["id"], "fqdn": "c.example.com"})
    listing = http.get("/domains").json()
    assert any(d["fqdn"] == "c.example.com" for d in listing)


def test_delete_domain(http, vm):
    d = http.post("/domains", json={"vm_id": vm["id"], "fqdn": "del.example.com"}).json()
    assert http.delete(f"/domains/{d['id']}").status_code == 204
    assert not any(x["id"] == d["id"] for x in http.get("/domains").json())


def test_create_domain_unknown_vm(http):
    r = http.post("/domains", json={"vm_id": "no-such-vm", "fqdn": "x.example.com"})
    assert r.status_code == 404


def test_create_domain_missing_subdomain_and_fqdn(http, vm):
    r = http.post("/domains", json={"vm_id": vm["id"]})
    assert r.status_code == 400


def test_delete_domain_not_found(http):
    assert http.delete("/domains/no-such-id").status_code == 404
