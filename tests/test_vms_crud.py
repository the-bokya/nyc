import pytest


@pytest.fixture
def vpc(http):
    return http.post("/vpcs", json={"name": "net", "cidr": "10.99.0.0/24"}).json()


def test_create_assigns_ip_in_cidr(http, vpc):
    r = http.post("/vms", json={"name": "vm1", "vpc_id": vpc["id"]})
    assert r.status_code == 201, r.text
    vm = r.json()
    assert vm["ip"].startswith("10.99.0.")
    assert vm["status"] == "running"


def test_two_vms_get_distinct_ips(http, vpc):
    a = http.post("/vms", json={"name": "a", "vpc_id": vpc["id"]}).json()
    b = http.post("/vms", json={"name": "b", "vpc_id": vpc["id"]}).json()
    assert a["ip"] != b["ip"]


def test_list_includes_live_status(http, vpc):
    http.post("/vms", json={"name": "lvm", "vpc_id": vpc["id"]})
    listing = http.get("/vms").json()
    assert all("live_status" in v for v in listing)
    assert any(v["live_status"] == "running" for v in listing)


def test_get_one(http, vpc):
    vm = http.post("/vms", json={"name": "g", "vpc_id": vpc["id"]}).json()
    assert http.get(f"/vms/{vm['id']}").json()["name"] == "g"


def test_delete(http, vpc):
    vm = http.post("/vms", json={"name": "d", "vpc_id": vpc["id"]}).json()
    assert http.delete(f"/vms/{vm['id']}").status_code == 204
    assert http.get(f"/vms/{vm['id']}").status_code == 404


def test_unknown_vpc_rejected(http):
    r = http.post("/vms", json={"name": "x", "vpc_id": "no-such-vpc"})
    assert r.status_code == 400


def test_attach_data_volume(http, vpc):
    vol = http.post("/volumes", json={"name": "vol", "size_mb": 8}).json()
    vm = http.post("/vms", json={"name": "with-vol", "vpc_id": vpc["id"],
                                 "data_volume_id": vol["id"]}).json()
    assert vm["data_volume_id"] == vol["id"]


def test_netns_created_for_vm(http, vpc):
    from nyc.client.privops_fake import STATE
    vm = http.post("/vms", json={"name": "n", "vpc_id": vpc["id"]}).json()
    assert f"vm-{vm['id'][:8]}" in STATE["netns"]


def test_bridge_created_per_vpc(http, vpc, node):
    from nyc.client.network.bridge import name_for
    from nyc.client.privops_fake import STATE
    http.post("/vms", json={"name": "v1", "vpc_id": vpc["id"]})
    expected = name_for(node["node_id"], vpc["id"])
    assert expected in STATE["links"] or expected in STATE["bridges"]


def test_delete_cleans_netns(http, vpc):
    from nyc.client.privops_fake import STATE
    vm = http.post("/vms", json={"name": "c", "vpc_id": vpc["id"]}).json()
    ns = f"vm-{vm['id'][:8]}"
    assert ns in STATE["netns"]
    http.delete(f"/vms/{vm['id']}")
    assert ns not in STATE["netns"]
