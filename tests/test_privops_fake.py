"""The fake privops backend has to faithfully mirror the argv shapes
the client emits. Test each kernel op the client uses."""
from nyc.client import privops
from nyc.client.privops_fake import STATE


def test_netns_lifecycle():
    privops.run(["ip", "netns", "add", "ns1"])
    assert "ns1" in STATE["netns"]
    out = privops.run(["ip", "netns", "list"])
    assert "ns1" in out
    privops.run(["ip", "netns", "del", "ns1"])
    assert "ns1" not in STATE["netns"]


def test_tap_via_netns_exec():
    privops.run(["ip", "netns", "add", "ns2"])
    privops.run(["ip", "netns", "exec", "ns2", "ip", "tuntap", "add", "dev", "tap0", "mode", "tap"])
    assert "tap0" in STATE["links"]


def test_veth_pair():
    privops.run(["ip", "link", "add", "vmh-x", "type", "veth", "peer", "name", "vmn-x"])
    assert "vmh-x" in STATE["links"] and "vmn-x" in STATE["links"]


def test_addr_add():
    privops.run(["ip", "addr", "add", "10.0.0.1/24", "dev", "eth0"])
    assert "10.0.0.1/24" in STATE["addrs"]["eth0"]


def test_volume_truncate_and_mkfs():
    privops.run(["truncate", "-s", "100M", "/tmp/vol.img"])
    assert STATE["files"]["/tmp/vol.img"] == 100 * 1024 * 1024
    privops.run(["mkfs.ext4", "-F", "/tmp/vol.img"])
    assert "/tmp/vol.img" in STATE["files"]


def test_firecracker_spawn_marks_running():
    privops.run(["firecracker", "--api-sock", "/tmp/api.sock", "--id", "vm-id"])
    assert STATE["fc_socks"]["/tmp/api.sock"]["running"] is True


def test_backend_default_is_fake(monkeypatch):
    monkeypatch.delenv("NYC_BACKEND", raising=False)
    assert privops.backend() == "fake"


def test_reset_state_clears_everything():
    privops.run(["ip", "netns", "add", "ns-junk"])
    privops.reset_state()
    assert STATE["netns"] == set()
