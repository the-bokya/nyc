"""Snapshots, golden images, spawn-from-golden, and the thin-independence
guarantee (deleting a snapshot/golden never affects clones)."""
from nyc.client.privops_fake import STATE
from nyc.client.volume import lv, names
from nyc.config import volume_vg

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY test@snap"


def _volume(http, size_mb=64):
    return http.post("/volumes", json={"name": "src", "size_mb": size_mb}).json()


def _snapshot(http, vol_id, name="snap1"):
    r = http.post("/snapshots", json={"name": name, "volume_id": vol_id})
    assert r.status_code == 201, r.text
    return r.json()


def _image(http, snap_id, name="golden1"):
    r = http.post("/images", json={"name": name, "from_snapshot": snap_id})
    assert r.status_code == 201, r.text
    return r.json()


def test_snapshot_of_volume(http, node):
    vol = _volume(http, size_mb=128)
    snap = _snapshot(http, vol["id"])
    assert snap["role"] == "snapshot" and snap["parent"] == vol["id"] and snap["size_mb"] == 128
    vg = volume_vg(node["node_id"])
    assert lv.exists(vg, names.snap(snap["id"]))
    assert [s["id"] for s in http.get("/snapshots").json()] == [snap["id"]]
    assert http.get(f"/snapshots/{snap['id']}").json()["name"] == "snap1"


def test_image_from_snapshot(http, node):
    snap = _snapshot(http, _volume(http)["id"])
    img = _image(http, snap["id"])
    assert img["role"] == "golden" and img["parent"] == snap["id"]
    vg = volume_vg(node["node_id"])
    assert lv.exists(vg, names.gold(img["id"]))
    assert [i["id"] for i in http.get("/images").json()] == [img["id"]]
    # an image is not listed under /snapshots, and vice versa
    assert http.get("/snapshots").json()[0]["id"] == snap["id"]


def test_spawn_from_golden_clones_that_image(http, node):
    snap = _snapshot(http, _volume(http)["id"])
    img = _image(http, snap["id"])
    vm = http.post("/vms/spawn", json={"vm_name": "g", "ssh_key": KEY, "image": img["id"]}).json()
    assert vm["status"] == "running"
    vg = volume_vg(node["node_id"])
    rootfs = STATE["lvm"]["lvs"][(vg, names.rootfs(vm["id"]))]
    assert rootfs["origin"] == names.gold(img["id"])  # rootfs is a clone of the golden, not the default


def test_spawn_without_image_uses_default_golden(http, node):
    vm = http.post("/vms/spawn", json={"vm_name": "d", "ssh_key": KEY}).json()
    vg = volume_vg(node["node_id"])
    assert STATE["lvm"]["lvs"][(vg, names.rootfs(vm["id"]))]["origin"] == "gold-default"


def test_deleting_snapshot_keeps_golden_and_clones(http, node):
    """The user's 'delete the snapshot tomorrow' question: thin independence."""
    vol = _volume(http)
    snap = _snapshot(http, vol["id"])
    img = _image(http, snap["id"])
    vm = http.post("/vms/spawn", json={"vm_name": "x", "ssh_key": KEY, "image": img["id"]}).json()
    assert http.delete(f"/snapshots/{snap['id']}").status_code == 204
    vg = volume_vg(node["node_id"])
    assert not lv.exists(vg, names.snap(snap["id"]))   # snapshot gone
    assert lv.exists(vg, names.gold(img["id"]))        # golden survives
    assert lv.exists(vg, names.rootfs(vm["id"]))       # the VM's rootfs clone survives


def test_volume_clone_from_snapshot(http, node):
    snap = _snapshot(http, _volume(http, size_mb=256)["id"])
    clone = http.post("/volumes", json={"name": "clone", "from_snapshot": snap["id"]}).json()
    assert clone["size_mb"] == 256  # inherits the snapshot's size
    vg = volume_vg(node["node_id"])
    assert STATE["lvm"]["lvs"][(vg, names.data(clone["id"]))]["origin"] == names.snap(snap["id"])


def test_resize_volume(http):
    vol = _volume(http, size_mb=64)
    resized = http.patch(f"/volumes/{vol['id']}", json={"size_mb": 256}).json()
    assert resized["size_mb"] == 256
    assert http.get(f"/volumes/{vol['id']}").json()["size_mb"] == 256


def test_image_from_unknown_snapshot_400(http):
    r = http.post("/images", json={"name": "bad", "from_snapshot": "nope"})
    assert r.status_code == 400


def test_spawn_image_on_other_node_rejected(http, node):
    """A golden owned by another node can't be cloned here yet (verify same-node)."""
    from nyc.tables import Snapshots
    foreign = {"id": "foreign-gold", "node_id": "some-other-node", "name": "f",
               "role": "golden", "parent": None, "lv_name": names.gold("foreign-gold"),
               "size_mb": 0, "created_at": "2026-01-01T00:00:00+00:00"}
    Snapshots(node["orm"]).docs.insert(foreign)
    # pin to THIS node so it doesn't forward to the (nonexistent) owner; the
    # same-node verify then rejects it.
    r = http.post("/vms/spawn", json={"vm_name": "z", "ssh_key": KEY, "image": "foreign-gold"},
                  headers={"X-Nyc-Pin": node["node_id"]})
    assert r.status_code == 409


def test_reconcile_prunes_orphan_snapshot_lv(http, node, monkeypatch):
    monkeypatch.chdir(node["tmp_path"])
    vg = volume_vg(node["node_id"])
    lv.snapshot(vg, "gold-default", names.snap("orphan-snap"), readonly=True)  # no row
    report = http.post("/reconcile").json()
    assert names.snap("orphan-snap") in report["snapshots"]["deleted"]
    assert not lv.exists(vg, names.snap("orphan-snap"))
