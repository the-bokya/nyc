"""Start a stopped VM: respawn firecracker from the on-disk config.json.

The netns/veth/tap/bridge survive `vm_stop`, so this only re-creates the
firecracker process inside the existing netns and boots it.
"""
from pathlib import Path

from nyc.client.env.paths import for_vm
from nyc.client.vm import boot, create


def run(vms_dir: Path, vm_id: str, ns: str, firecracker_bin: Path) -> None:
    paths = for_vm(vms_dir, vm_id)
    create.run(paths, vm_id, ns, firecracker_bin)
    boot.run(paths)
