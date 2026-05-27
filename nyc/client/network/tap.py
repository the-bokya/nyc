"""tap interface lifecycle inside a netns. No IP — the tap is a passthrough
for firecracker's vhost; the guest gets its IP via kernel boot args."""
from nyc.client import privops


def create(ns: str, name: str = "tap0") -> None:
    privops.run(["ip", "netns", "exec", ns, "ip", "tuntap", "add", "dev", name, "mode", "tap"])


def delete(ns: str, name: str = "tap0") -> None:
    privops.run(["ip", "netns", "exec", ns, "ip", "link", "del", name])
