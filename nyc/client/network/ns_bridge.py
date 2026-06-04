"""Internal bridge living inside a VM's netns.

Joins the ns-side veth (to the host bridge) and a tap (firecracker) so the
guest sits on an L2 segment. The bridge name is parameterized: the VPC stack
uses the default `nbr0`; the public stack uses `pbr1` in the same netns.
"""
from nyc.client import privops

NAME = "nbr0"


def create(ns: str, name: str = NAME) -> None:
    privops.run(["ip", "netns", "exec", ns, "ip", "link", "add", name, "type", "bridge"])
    privops.run(["ip", "netns", "exec", ns, "ip", "link", "set", name, "up"])


def attach(ns: str, link: str, name: str = NAME) -> None:
    privops.run(["ip", "netns", "exec", ns, "ip", "link", "set", link, "master", name])
    privops.run(["ip", "netns", "exec", ns, "ip", "link", "set", link, "up"])
