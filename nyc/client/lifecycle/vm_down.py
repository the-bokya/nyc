"""Compose: kill firecracker + tear down netns + remove veth + remove vm_dir."""
from pathlib import Path

from nyc.client.env import teardown as env_teardown
from nyc.client.env.paths import for_vm
from nyc.client.network import namespace, veth
from nyc.client.vm import kill


def run(vms_dir: Path, vm_id: str) -> None:
    paths = for_vm(vms_dir, vm_id)
    if paths.root.exists():
        kill.run(paths)
    _network_down(vm_id)
    env_teardown.run(paths.root)


def _network_down(vm_id: str) -> None:
    ns = f"vm-{vm_id[:8]}"
    host_veth = f"vmh-{vm_id[:8]}"
    # Order matters: delete the netns first (kernel auto-removes the ns-side
    # veth peer and the internal nbr0+tap0), then drop the host-side veth.
    _safe(lambda: namespace.delete(ns))
    _safe(lambda: veth.delete(host_veth))


def _safe(fn) -> None:
    try:
        fn()
    except Exception:
        pass
