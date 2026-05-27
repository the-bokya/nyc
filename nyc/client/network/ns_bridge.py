"""Internal bridge living inside a VM's netns.

Joins ns_veth (to the host VPC bridge) and tap0 (firecracker) so the guest
sits on the VPC's L2 segment. The bridge inside the netns is unnamed-by-id
since the netns provides its own namespace — `nbr0` per netns is fine.
"""
from nyc.client import privops

NAME = "nbr0"


def create(ns: str) -> None:
    privops.run(["ip", "netns", "exec", ns, "ip", "link", "add", NAME, "type", "bridge"])
    privops.run(["ip", "netns", "exec", ns, "ip", "link", "set", NAME, "up"])


def attach(ns: str, link: str) -> None:
    privops.run(["ip", "netns", "exec", ns, "ip", "link", "set", link, "master", NAME])
    privops.run(["ip", "netns", "exec", ns, "ip", "link", "set", link, "up"])
