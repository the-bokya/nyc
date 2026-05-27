def test_create_and_list(http):
    r = http.post("/vpcs", json={"name": "default", "cidr": "10.10.0.0/24"})
    assert r.status_code == 201, r.text
    vpc = r.json()
    assert vpc["name"] == "default" and vpc["cidr"] == "10.10.0.0/24"
    listing = http.get("/vpcs").json()
    assert any(v["id"] == vpc["id"] for v in listing)


def test_get_one(http):
    new = http.post("/vpcs", json={"name": "a", "cidr": "10.20.0.0/24"}).json()
    assert http.get(f"/vpcs/{new['id']}").json()["name"] == "a"


def test_delete(http):
    new = http.post("/vpcs", json={"name": "x", "cidr": "10.30.0.0/24"}).json()
    assert http.delete(f"/vpcs/{new['id']}").status_code == 204
    assert http.get(f"/vpcs/{new['id']}").status_code == 404


def test_bad_cidr_rejected(http):
    r = http.post("/vpcs", json={"name": "bad", "cidr": "not-a-cidr"})
    assert r.status_code == 400


def test_unique_name(http):
    http.post("/vpcs", json={"name": "dup", "cidr": "10.40.0.0/24"})
    r = http.post("/vpcs", json={"name": "dup", "cidr": "10.50.0.0/24"})
    assert r.status_code >= 400


def test_delete_blocked_when_vms_attached(http):
    vpc = http.post("/vpcs", json={"name": "blk", "cidr": "10.60.0.0/24"}).json()
    http.post("/vms", json={"name": "v1", "vpc_id": vpc["id"]})
    r = http.delete(f"/vpcs/{vpc['id']}")
    assert r.status_code == 409
