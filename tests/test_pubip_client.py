"""Unit tests for pubip/host.py + pubip/nat.py using the fake backend."""
from nyc.client.privops_fake import STATE
from nyc.client.pubip import host as pubip_host
from nyc.client.pubip import nat as pubip_nat


def test_bind_adds_address():
    pubip_host.bind("1.2.3.4", "eth0")
    assert "1.2.3.4/32" in STATE["addrs"].get("eth0", [])


def test_bind_idempotent():
    pubip_host.bind("1.2.3.4", "eth0")
    pubip_host.bind("1.2.3.4", "eth0")
    assert STATE["addrs"]["eth0"].count("1.2.3.4/32") == 1


def test_unbind_removes_address():
    pubip_host.bind("1.2.3.4", "eth0")
    pubip_host.unbind("1.2.3.4", "eth0")
    assert "1.2.3.4/32" not in STATE["addrs"].get("eth0", [])


def test_nat_attach_dnat_rule():
    pubip_nat.attach("1.2.3.4", "10.0.0.5")
    rules = STATE["iptables"]["nat"]["rules"].get("NYC-PREROUTING", [])
    assert ("-d", "1.2.3.4", "-j", "DNAT", "--to-destination", "10.0.0.5") in rules


def test_nat_attach_snat_before_masquerade():
    """SNAT (scoped to inbound replies) must appear before the general MASQUERADE."""
    from nyc.client.network import nat as net_nat
    net_nat.ensure("10.0.0.0/24")

    pubip_nat.attach("1.2.3.4", "10.0.0.5")
    rules = STATE["iptables"]["nat"]["rules"].get("NYC-POSTROUTING", [])
    snat = ("-s", "10.0.0.5", "-m", "conntrack", "--ctorigdst", "1.2.3.4",
            "-j", "SNAT", "--to-source", "1.2.3.4")
    masq = ("-s", "10.0.0.0/24", "!", "-d", "10.0.0.0/24", "-j", "MASQUERADE")
    assert snat in rules
    assert masq in rules
    assert rules.index(snat) < rules.index(masq)


def test_nat_attach_idempotent():
    pubip_nat.attach("1.2.3.4", "10.0.0.5")
    pubip_nat.attach("1.2.3.4", "10.0.0.5")
    rules = STATE["iptables"]["nat"]["rules"].get("NYC-PREROUTING", [])
    dnat = ("-d", "1.2.3.4", "-j", "DNAT", "--to-destination", "10.0.0.5")
    assert rules.count(dnat) == 1


def test_nat_detach_removes_rules():
    pubip_nat.attach("1.2.3.4", "10.0.0.5")
    pubip_nat.detach("1.2.3.4", "10.0.0.5")
    pre_rules = STATE["iptables"]["nat"]["rules"].get("NYC-PREROUTING", [])
    post_rules = STATE["iptables"]["nat"]["rules"].get("NYC-POSTROUTING", [])
    assert ("-d", "1.2.3.4", "-j", "DNAT", "--to-destination", "10.0.0.5") not in pre_rules
    assert ("-s", "10.0.0.5", "-m", "conntrack", "--ctorigdst", "1.2.3.4",
            "-j", "SNAT", "--to-source", "1.2.3.4") not in post_rules
