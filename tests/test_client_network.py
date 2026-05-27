from nyc.client.network import allocate, bridge, namespace, ns_bridge, tap, veth
from nyc.client.privops_fake import STATE


def test_pick_ip_skips_gateway():
    ip = allocate.pick_ip("10.0.0.0/29", used=set())
    assert ip != "10.0.0.1"  # gateway
    assert ip == "10.0.0.2"


def test_pick_ip_skips_used():
    ip = allocate.pick_ip("10.0.0.0/29", used={"10.0.0.2"})
    assert ip == "10.0.0.3"


def test_pick_ip_raises_when_full():
    import pytest
    full = {"10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5", "10.0.0.6"}
    with pytest.raises(RuntimeError):
        allocate.pick_ip("10.0.0.0/29", used=full)


def test_namespace_create_and_delete():
    namespace.create("nstest")
    assert "nstest" in namespace.list_all()
    namespace.delete("nstest")
    assert "nstest" not in namespace.list_all()


def test_bridge_ensure_idempotent():
    bridge.ensure("br-test", "10.0.0.1/24")
    bridge.ensure("br-test", "10.0.0.1/24")  # no-op second time
    assert "br-test" in STATE["bridges"] or "br-test" in STATE["links"]


def test_veth_pair_create_and_place():
    namespace.create("nsw")
    veth.create_pair("vmh-test", "vmn-test")
    veth.place_in_ns("vmn-test", "nsw")
    assert "vmh-test" in STATE["links"]
    assert STATE["links"]["vmn-test"]["netns"] == "nsw"


def test_tap_inside_netns_has_no_ip():
    namespace.create("nstap")
    tap.create("nstap", "tap0")
    assert "tap0" in STATE["links"]
    assert "tap0" not in STATE["addrs"]


def test_ns_bridge_attaches_links():
    namespace.create("nsb")
    ns_bridge.create("nsb")
    tap.create("nsb", "tap0")
    ns_bridge.attach("nsb", "tap0")
    assert STATE["links"]["tap0"].get("master") == ns_bridge.NAME


def test_gateway_and_netmask_helpers():
    assert allocate.gateway("10.99.0.0/24") == "10.99.0.1"
    assert allocate.netmask("10.99.0.0/24") == "255.255.255.0"
    assert allocate.gateway_cidr("10.99.0.0/24") == "10.99.0.1/24"
