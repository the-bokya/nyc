"""POST /vms/spawn — turnkey VM: default VPC, auto volume, seed disk.

Single-node fixture, so the random node pick always resolves to this node and
the spawn runs locally (the cross-node pin/forward path is covered in e2e).
"""
from nyc.client.privops_fake import STATE
from nyc.defaults import DEFAULT_VPC_CIDR, DEFAULT_VPC_NAME

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY test@spawn"


def _spawn(http, **over):
    body = {"vm_name": "sp", "ssh_key": KEY, **over}
    r = http.post("/vms/spawn", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_spawn_lands_in_default_vpc(http):
    vm = _spawn(http)
    assert vm["status"] == "running"
    assert vm["ip"].startswith("172.16.")
    vpcs = http.get("/vpcs").json()
    assert [v["name"] for v in vpcs] == [DEFAULT_VPC_NAME]
    assert vpcs[0]["cidr"] == DEFAULT_VPC_CIDR
    assert vm["vpc_id"] == vpcs[0]["id"]


def test_spawn_autocreates_tracked_volume(http):
    vm = _spawn(http, size_mb=2048)
    assert vm["data_volume_id"]
    vol = http.get(f"/volumes/{vm['data_volume_id']}").json()
    assert vol["size_mb"] == 2048
    assert vol["name"] == "sp-data"
    assert vol["node_id"] == vm["node_id"]


def test_spawn_defaults(http):
    vm = _spawn(http)
    got = http.get(f"/vms/{vm['id']}").json()
    assert got["vcpu_count"] == 1 and got["mem_mib"] == 512
    assert http.get(f"/volumes/{vm['data_volume_id']}").json()["size_mb"] == 1024


def test_spawn_honours_overrides(http):
    vm = _spawn(http, vcpu_count=4, mem_mib=2048)
    got = http.get(f"/vms/{vm['id']}").json()
    assert got["vcpu_count"] == 4 and got["mem_mib"] == 2048


def test_spawn_creates_writable_rootfs_copy(http):
    vm = _spawn(http)
    suffix = f"{vm['id']}/rootfs.ext4"
    assert any(dest.endswith(suffix) for _, dest in STATE["copies"])


def test_spawn_creates_seed_disk(http):
    vm = _spawn(http)
    suffix = f"{vm['id']}/seed.ext4"
    # mkfs.ext4 records the seed path in files; debugfs write records the argv
    assert any(k.endswith(suffix) for k in STATE["files"])
    assert any(argv[-1].endswith(suffix) for argv in STATE["debugfs"])


def test_explicit_create_also_copies_rootfs_and_creates_seed(http):
    vpc = http.post("/vpcs", json={"name": "net", "cidr": "10.5.0.0/24"}).json()
    vm = http.post("/vms", json={"name": "plain", "vpc_id": vpc["id"]}).json()
    assert any(dest.endswith(f"{vm['id']}/rootfs.ext4") for _, dest in STATE["copies"])
    assert any(argv[-1].endswith(f"{vm['id']}/seed.ext4") for argv in STATE["debugfs"])


def test_spawn_reuses_one_default_vpc(http):
    a = _spawn(http)
    b = _spawn(http)
    assert a["vpc_id"] == b["vpc_id"]
    assert a["ip"] != b["ip"]
    assert len(http.get("/vpcs").json()) == 1


def test_local_only_header_filters_to_owner(http, node):
    vm = _spawn(http)
    local = http.get("/vms", headers={"X-Nyc-Local": "1"}).json()
    assert all(v["node_id"] == node["node_id"] for v in local)
    assert any(v["id"] == vm["id"] and v["live_status"] == "running" for v in local)
