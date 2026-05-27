"""veth pair lifecycle. One pair per VM: host-side joins the VPC bridge,
ns-side enters the VM's netns and is bridged with tap0 inside."""
from nyc.client import privops


def create_pair(host_name: str, ns_name: str) -> None:
    privops.run(["ip", "link", "add", host_name, "type", "veth", "peer", "name", ns_name])


def place_in_ns(name: str, ns: str) -> None:
    privops.run(["ip", "link", "set", name, "netns", ns])


def up(name: str) -> None:
    privops.run(["ip", "link", "set", name, "up"])


def delete(name: str) -> None:
    privops.run(["ip", "link", "del", name])
