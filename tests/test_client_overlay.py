"""Overlay derivations, VXLAN head-end FDB, and NAT — all against the fake backend."""
import pytest

from nyc.client import privops
from nyc.client.network import bridge, nat, overlay, vxlan
from nyc.client.privops_fake import STATE


# --- deterministic derivations -------------------------------------------------

def test_vni_deterministic_and_in_range():
    a = overlay.vni_for("vpc-abc")
    assert a == overlay.vni_for("vpc-abc")
    assert 1 <= a <= 2 ** 24 - 1


def test_vni_differs_per_vpc():
    assert overlay.vni_for("vpc-a") != overlay.vni_for("vpc-b")


def test_anycast_mac_is_local_unicast_and_stable():
    m = overlay.anycast_mac("vpc-abc")
    assert m == overlay.anycast_mac("vpc-abc")
    assert m.startswith("02:") and len(m.split(":")) == 6
    assert int(m.split(":")[0], 16) & 0x01 == 0          # unicast
    assert int(m.split(":")[0], 16) & 0x02 == 0x02       # locally administered


# --- vxlan ---------------------------------------------------------------------

def test_vxlan_ensure_creates_device_enslaved():
    name = vxlan.name_for("nodeABCD", "vpcWXYZ")
    vxlan.ensure(name, 4242, "10.1.0.14", "br-node-vpc")
    assert name in STATE["links"]
    assert STATE["links"][name]["kind"] == "vxlan"
    assert STATE["links"][name].get("master") == "br-node-vpc"


def test_vxlan_ensure_idempotent():
    name = vxlan.name_for("n", "v")
    vxlan.ensure(name, 1, "10.1.0.14", "br")
    vxlan.ensure(name, 1, "10.1.0.14", "br")  # no error, still one device
    assert name in STATE["links"]


def test_set_fdb_reconciles_to_exact_peer_set():
    name = vxlan.name_for("n", "v")
    vxlan.ensure(name, 7, "10.1.0.14", "br")
    vxlan.set_fdb(name, ["10.1.0.15", "10.1.0.2"])
    assert vxlan._fdb_peers(name) == {"10.1.0.15", "10.1.0.2"}
    vxlan.set_fdb(name, ["10.1.0.2"])               # drop one, keep one
    assert vxlan._fdb_peers(name) == {"10.1.0.2"}


def test_name_for_within_ifnamsiz():
    assert len(vxlan.name_for("abcdef12", "34567890")) <= 15


# --- nat -----------------------------------------------------------------------

def test_nat_ensure_is_idempotent_and_masquerades():
    nat.ensure("172.16.0.0/16")
    nat.ensure("172.16.0.0/16")  # second call must not duplicate
    rules = STATE["iptables"]["nat"]["rules"][nat.POST]
    masq = [r for r in rules if "MASQUERADE" in r]
    assert len(masq) == 1
    assert STATE["sysctl"]["net.ipv4.ip_forward"] == "1"


def test_nat_jump_rules_installed_once():
    nat.ensure("172.16.0.0/16")
    nat.ensure("172.16.0.0/16")
    post_jumps = STATE["iptables"]["nat"]["rules"]["POSTROUTING"]
    assert post_jumps.count(("-j", nat.POST)) == 1
    fwd_jumps = STATE["iptables"]["filter"]["rules"]["FORWARD"]
    assert fwd_jumps.count(("-j", nat.FWD)) == 1


def test_nat_delete_removes_masquerade():
    nat.ensure("172.16.0.0/16")
    nat.delete("172.16.0.0/16")
    rules = STATE["iptables"]["nat"]["rules"][nat.POST]
    assert not any("MASQUERADE" in r for r in rules)


def test_iptables_check_raises_when_absent():
    with pytest.raises(privops.PrivopsError):
        privops.run(["iptables", "-t", "nat", "-C", "POSTROUTING", "-j", "GHOST"])


# --- anycast bridge ------------------------------------------------------------

def test_bridge_ensure_sets_anycast_mac():
    mac = overlay.anycast_mac("vpc-xyz")
    bridge.ensure("br-a-b", "172.16.0.1/16", mac=mac)
    assert STATE["links"]["br-a-b"]["address"] == mac
