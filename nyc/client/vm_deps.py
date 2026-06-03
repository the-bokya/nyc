"""Teardown cascade for VM-related resources.

Called from both DELETE /vms and the vms_pass orphan reaper so the same
cleanup logic runs in both paths.
"""
from dadar.orm import Client

from nyc.client.pubip import backend as pubip_backend
from nyc.client.pubip import host as pubip_host
from nyc.client.pubip import nat as pubip_nat
from nyc.config import pubip as pubip_cfg
from nyc.tables import Domains, Proxies, PublicIps, Vms


def teardown(vm_id: str, client: Client) -> None:
    """Detach public IP, delete domains, clear proxy row for vm_id."""
    _teardown_public_ip(vm_id, client)
    Domains(client).docs.delete(where={"vm_id": vm_id})
    proxy = Proxies(client).docs.get(where={"vm_id": vm_id})
    if proxy is not None:
        Proxies(client).docs.delete(where={"vm_id": vm_id})


def _teardown_public_ip(vm_id: str, client: Client) -> None:
    pip = PublicIps(client).docs.get(where={"vm_id": vm_id, "status": "attached"})
    if pip is None:
        return
    d = pip.__dict__
    vm = Vms(client).docs.get(where={"id": vm_id})
    vm_ip = vm.__dict__["ip"] if vm else None
    if vm_ip:
        try:
            pubip_nat.detach(d["address"], vm_ip)
        except Exception:
            pass
        try:
            pubip_host.unbind(d["address"], d["iface"])
        except Exception:
            pass
    try:
        pubip_backend.release(pubip_cfg(), d["address"])
    except Exception:
        pass
    PublicIps(client).docs.delete(where={"id": d["id"]})
