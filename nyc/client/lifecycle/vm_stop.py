"""Stop a VM: kill the firecracker process, keep everything else on disk.

netns, tap, veth, bridge, volume, IP and the config.json are all preserved so
`vm_start` is a cheap respawn. The DB row stays (the router flips its status).
"""
from pathlib import Path

from nyc.client.env.paths import for_vm
from nyc.client.vm import kill


def run(vms_dir: Path, vm_id: str) -> None:
    kill.run(for_vm(vms_dir, vm_id))
