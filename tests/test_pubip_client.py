"""Unit tests for client/pubip/pool.py and the L2 wiring in vm_up."""
import os

import pytest

os.environ.setdefault("NYC_PUBLIC_IPS", "203.0.113.10|de:ad:be:ef:00:01,203.0.113.11|de:ad:be:ef:00:02")
os.environ.setdefault("NYC_PUBLIC_IFACE", "ens3")
os.environ.setdefault("NYC_PUBLIC_BRIDGE", "pub0")
os.environ.setdefault("NYC_PUBIP_GATEWAY", "62.210.0.1")

from nyc.client.pubip import pool as pubip_pool
from nyc.config import pubip as pubip_cfg, PubIpEntry, PubipConfig


def _cfg(*extra_ips):
    ips = [
        PubIpEntry(address="203.0.113.10", mac="de:ad:be:ef:00:01"),
        PubIpEntry(address="203.0.113.11", mac="de:ad:be:ef:00:02"),
    ] + list(extra_ips)
    return PubipConfig(iface="ens3", ips=ips, gateway="62.210.0.1", bridge="pub0")


def test_acquire_returns_first_free():
    cfg = _cfg()
    addr, gw, mac, prefix = pubip_pool.acquire(cfg, set())
    assert addr == "203.0.113.10"
    assert mac == "de:ad:be:ef:00:01"
    assert prefix == "32"
    assert gw == "62.210.0.1"


def test_acquire_skips_used():
    cfg = _cfg()
    addr, _gw, mac, _prefix = pubip_pool.acquire(cfg, {"203.0.113.10"})
    assert addr == "203.0.113.11"
    assert mac == "de:ad:be:ef:00:02"


def test_acquire_raises_when_exhausted():
    cfg = _cfg()
    with pytest.raises(RuntimeError, match="no free public IPs"):
        pubip_pool.acquire(cfg, {"203.0.113.10", "203.0.113.11"})


def test_release_is_noop():
    cfg = _cfg()
    pubip_pool.release(cfg, "203.0.113.10")  # must not raise


def test_config_parses_env_csv():
    cfg = pubip_cfg()
    addrs = [e.address for e in cfg.ips]
    macs = [e.mac for e in cfg.ips]
    assert "203.0.113.10" in addrs
    assert "de:ad:be:ef:00:01" in macs
    assert cfg.bridge == "pub0"
    assert cfg.gateway == "62.210.0.1"


def test_wire_public_ip_enslaves_pvh_to_pub0(http, node):
    """After attaching a public IP, pvh-* must be enslaved to pub0."""
    from nyc.client.privops_fake import STATE

    r = http.post("/vpcs", json={"name": "net", "cidr": "10.9.0.0/24"})
    vpc_id = r.json()["id"]
    vm = http.post("/vms", json={"name": "pv", "vpc_id": vpc_id}).json()
    vm_id = vm["id"]

    r = http.post(f"/vms/{vm_id}/public-ip")
    assert r.status_code == 201

    pvh = f"pvh-{vm_id[:8]}"
    assert pvh in STATE["links"], f"pvh link {pvh} not created"
    assert STATE["links"][pvh].get("master") == "pub0"


def test_wire_public_ip_creates_tap1(http, node):
    """After attaching, tap1 must exist in the VM's netns."""
    from nyc.client.privops_fake import STATE

    r = http.post("/vpcs", json={"name": "net2", "cidr": "10.10.0.0/24"})
    vpc_id = r.json()["id"]
    vm = http.post("/vms", json={"name": "pv2", "vpc_id": vpc_id}).json()
    vm_id = vm["id"]

    http.post(f"/vms/{vm_id}/public-ip")

    assert "tap1" in STATE["links"]


def test_wire_public_ip_reruns_inject(http, node):
    """Attaching a public IP recreates the VM — debugfs must be called twice
    (initial spawn + recreate), proving inject ran with the public_ip arg."""
    from nyc.client.privops_fake import STATE

    r = http.post("/vpcs", json={"name": "net3", "cidr": "10.11.0.0/24"})
    vpc_id = r.json()["id"]
    vm = http.post("/vms", json={"name": "pv3", "vpc_id": vpc_id}).json()
    vm_id = vm["id"]

    http.post(f"/vms/{vm_id}/public-ip")

    # Two debugfs calls for this VM's rootfs: initial spawn + recreate after attach
    calls = [argv for argv in STATE["debugfs"] if argv[-1].endswith(f"{vm_id}/rootfs.ext4")]
    assert len(calls) >= 2, f"expected ≥2 debugfs calls (initial+recreate), got {len(calls)}"
