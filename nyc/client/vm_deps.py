"""Teardown cascade for VM-related resources.

Called from both DELETE /vms and the vms_pass orphan reaper so the same
cleanup logic runs in both paths.
"""
from dadar.orm import Client

from nyc.tables import Domains, Proxies, PublicIps


def teardown(vm_id: str, client: Client) -> None:
    """Delete public IP row, domains, and proxy row for vm_id."""
    _teardown_public_ip(vm_id, client)
    Domains(client).docs.delete(where={"vm_id": vm_id})
    proxy = Proxies(client).docs.get(where={"vm_id": vm_id})
    if proxy is not None:
        Proxies(client).docs.delete(where={"vm_id": vm_id})


def _teardown_public_ip(vm_id: str, client: Client) -> None:
    pip = PublicIps(client).docs.get(where={"vm_id": vm_id, "status": "attached"})
    if pip is None:
        return
    # The eth1 wiring (pvh-*, pvn-*, pbr1, tap1) dies with the VM's netns in vm_down.
    # No host-side NAT or address to clean up in the L2 model.
    PublicIps(client).docs.delete(where={"id": pip.__dict__["id"]})
