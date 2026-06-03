"""Snapshots + golden images, generic over root and data disks: snapshot a
data volume or a VM's root, promote to an image, and spawn cloning the root
(root_image) and/or the data disk (data_image). Plus the thin-independence
guarantee and the root-must-be-bootable guard."""
from nyc.client.privops_fake import STATE
from nyc.client.volume import lv, names
from nyc.config import volume_vg

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY test@snap"


def _volume(http, size_mb=64):
    return http.post("/volumes", json={"name": "src", "size_mb": size_mb}).json()


def _snapshot(http, *, volume_id=None, vm_id=None, name="snap1"):
    body = {"name": name, **({"volume_id": volume_id} if volume_id else {"vm_id": vm_id})}
    r = http.post("/snapshots", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _image(http, snap_id, name="golden1"):
    r = http.post("/images", json={"name": name, "from_snapshot": snap_id})
    assert r.status_code == 201, r.text
    return r.json()


def _spawn(http, **over):
    r = http.post("/vms/spawn", json={"vm_name": "v", "ssh_key": KEY, **over})
    assert r.status_code == 201, r.text
    return r.json()


def _root_image(http):
    """Bake a bootable root image: spawn a template, snapshot its root, promote."""
    tmpl = _spawn(http, vm_name="tmpl")
    snap = _snapshot(http, vm_id=tmpl["id"], name="rsnap")
    return _image(http, snap["id"], name="rootimg"), snap, tmpl


def _data_image(http, size_mb=128):
    snap = _snapshot(http, volume_id=_volume(http, size_mb)["id"], name="dsnap")
    return _image(http, snap["id"], name="dataimg")


# --- snapshots over both disk kinds --------------------------------------

def test_snapshot_of_volume_is_data(http, node):
    vol = _volume(http, size_mb=128)
    snap = _snapshot(http, volume_id=vol["id"])
    assert snap["disk"] == "data" and snap["parent"] == vol["id"] and snap["size_mb"] == 128
    assert lv.exists(volume_vg(node["node_id"]), names.snap(snap["id"]))


def test_snapshot_of_vm_root_is_root(http, node):
    vm = _spawn(http)
    snap = _snapshot(http, vm_id=vm["id"])
    assert snap["disk"] == "root" and snap["parent"] == vm["id"]
    vg = volume_vg(node["node_id"])
    assert STATE["lvm"]["lvs"][(vg, names.snap(snap["id"]))]["origin"] == names.rootfs(vm["id"])


def test_snapshot_requires_exactly_one_source(http):
    assert http.post("/snapshots", json={"name": "x"}).status_code == 400
    both = {"name": "x", "volume_id": _volume(http)["id"], "vm_id": _spawn(http)["id"]}
    assert http.post("/snapshots", json=both).status_code == 400


def test_image_inherits_disk_role(http):
    data_img = _data_image(http)
    assert data_img["disk"] == "data"
    root_img, _, _ = _root_image(http)
    assert root_img["disk"] == "root"


# --- spawn from images ----------------------------------------------------

def test_spawn_root_image_clones_that_root(http, node):
    img, _snap, _tmpl = _root_image(http)
    vm = _spawn(http, root_image=img["id"])
    assert vm["status"] == "running"
    vg = volume_vg(node["node_id"])
    assert STATE["lvm"]["lvs"][(vg, names.rootfs(vm["id"]))]["origin"] == img["lv_name"]


def test_spawn_without_image_uses_default_golden(http, node):
    vm = _spawn(http)
    vg = volume_vg(node["node_id"])
    assert STATE["lvm"]["lvs"][(vg, names.rootfs(vm["id"]))]["origin"] == "gold-default"


def test_spawn_data_image_clones_the_data_disk(http, node):
    img = _data_image(http, size_mb=128)
    vm = _spawn(http, data_image=img["id"])
    vol = http.get(f"/volumes/{vm['data_volume_id']}").json()
    assert vol["size_mb"] == 128  # inherits the image's size, not the default
    vg = volume_vg(node["node_id"])
    assert STATE["lvm"]["lvs"][(vg, names.data(vm["data_volume_id"]))]["origin"] == img["lv_name"]


def test_spawn_root_image_rejects_a_data_image(http):
    """The exact brick: a data-disk image used as root is refused, not booted."""
    img = _data_image(http)
    r = http.post("/vms/spawn", json={"vm_name": "brick", "ssh_key": KEY, "root_image": img["id"]})
    assert r.status_code == 400


# --- node-locality + lineage ---------------------------------------------

def test_deleting_snapshot_keeps_golden_and_clones(http, node):
    """Thin independence: drop the snapshot, the image + VM clones survive."""
    img, snap, _tmpl = _root_image(http)
    vm = _spawn(http, root_image=img["id"])
    assert http.delete(f"/snapshots/{snap['id']}").status_code == 204
    vg = volume_vg(node["node_id"])
    assert not lv.exists(vg, names.snap(snap["id"]))
    assert lv.exists(vg, names.gold(img["id"]))
    assert lv.exists(vg, names.rootfs(vm["id"]))


def test_volume_clone_from_snapshot(http, node):
    snap = _snapshot(http, volume_id=_volume(http, size_mb=256)["id"])
    clone = http.post("/volumes", json={"name": "clone", "from_snapshot": snap["id"]}).json()
    assert clone["size_mb"] == 256
    vg = volume_vg(node["node_id"])
    assert STATE["lvm"]["lvs"][(vg, names.data(clone["id"]))]["origin"] == names.snap(snap["id"])


def test_resize_volume(http):
    vol = _volume(http, size_mb=64)
    assert http.patch(f"/volumes/{vol['id']}", json={"size_mb": 256}).json()["size_mb"] == 256
    assert http.get(f"/volumes/{vol['id']}").json()["size_mb"] == 256


def test_image_from_unknown_snapshot_400(http):
    assert http.post("/images", json={"name": "bad", "from_snapshot": "nope"}).status_code == 400


def _insert_golden(orm, gid, node_id, disk):
    from nyc.tables import Snapshots
    Snapshots(orm).docs.insert({"id": gid, "node_id": node_id, "name": gid, "role": "golden",
                                "disk": disk, "parent": None, "lv_name": names.gold(gid),
                                "size_mb": 0, "created_at": "2026-01-01T00:00:00+00:00"})


def test_spawn_root_image_on_other_node_rejected(http, node):
    _insert_golden(node["orm"], "foreign-gold", "some-other-node", "root")
    r = http.post("/vms/spawn", json={"vm_name": "z", "ssh_key": KEY, "root_image": "foreign-gold"},
                  headers={"X-Nyc-Pin": node["node_id"]})  # pin local so it verifies instead of forwarding
    assert r.status_code == 409


def test_spawn_images_on_different_nodes_rejected(http, node):
    _insert_golden(node["orm"], "root-on-a", "node-A", "root")
    _insert_golden(node["orm"], "data-on-b", "node-B", "data")
    r = http.post("/vms/spawn", json={"vm_name": "z", "ssh_key": KEY,
                                      "root_image": "root-on-a", "data_image": "data-on-b"})
    assert r.status_code == 409


def test_reconcile_prunes_orphan_snapshot_lv(http, node, monkeypatch):
    monkeypatch.chdir(node["tmp_path"])
    vg = volume_vg(node["node_id"])
    lv.snapshot(vg, "gold-default", names.snap("orphan-snap"), readonly=True)  # no row
    report = http.post("/reconcile").json()
    assert names.snap("orphan-snap") in report["snapshots"]["deleted"]
    assert not lv.exists(vg, names.snap("orphan-snap"))
