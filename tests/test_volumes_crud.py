def test_create_and_list(http):
    r = http.post("/volumes", json={"name": "data1", "size_mb": 64})
    assert r.status_code == 201, r.text
    vol = r.json()
    assert vol["size_mb"] == 64 and vol["status"] == "ready"
    listing = http.get("/volumes").json()
    assert any(v["id"] == vol["id"] for v in listing)


def test_get_one(http):
    new = http.post("/volumes", json={"name": "v", "size_mb": 16}).json()
    assert http.get(f"/volumes/{new['id']}").json()["name"] == "v"


def test_delete(http):
    new = http.post("/volumes", json={"name": "to-del", "size_mb": 8}).json()
    assert http.delete(f"/volumes/{new['id']}").status_code == 204
    assert http.get(f"/volumes/{new['id']}").status_code == 404


def test_node_id_recorded(http, node):
    vol = http.post("/volumes", json={"name": "owned", "size_mb": 8}).json()
    assert vol["node_id"] == node["node_id"]


def test_volume_lv_actually_created(http, node):
    from nyc.client.volume import lv, names
    from nyc.config import volume_vg
    vol = http.post("/volumes", json={"name": "fsentry", "size_mb": 32}).json()
    vg = volume_vg(node["node_id"])
    assert vol["path"] == f"/dev/{vg}/{names.data(vol['id'])}"
    assert lv.exists(vg, names.data(vol["id"]))


def test_delete_blocked_when_attached(http):
    vpc = http.post("/vpcs", json={"name": "v", "cidr": "10.70.0.0/24"}).json()
    vol = http.post("/volumes", json={"name": "attached", "size_mb": 8}).json()
    http.post("/vms", json={"name": "vm", "vpc_id": vpc["id"], "data_volume_id": vol["id"]})
    r = http.delete(f"/volumes/{vol['id']}")
    assert r.status_code == 409
