"""Build a cloud-init NoCloud seed disk (seed.ext4, labelled cidata).

Creates meta-data (instance-id + hostname) and user-data (cloud-config with
optional SSH key and /home mount) in a 1 MiB ext4. Cloud-init on Ubuntu
detects it via the cidata label and applies config on first boot.
All ops go through debugfs — no mount, no loop device, no root required.
"""
import tempfile
from pathlib import Path

from nyc.client import privops
from nyc.client.env.paths import VmPaths

_WRITE = """\
write {meta_data} meta-data
write {user_data} user-data
"""


def create(paths: VmPaths, vm_id: str, vm_name: str,
           ssh_pubkey: str | None, has_data_volume: bool) -> None:
    privops.run(["truncate", "-s", "1M", str(paths.seed)])
    privops.run(["mkfs.ext4", "-F", "-L", "cidata", str(paths.seed)])
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "meta-data").write_text(_meta_data(vm_id, vm_name))
        (d / "user-data").write_text(_user_data(ssh_pubkey, has_data_volume))
        cmds = d / "cmds"
        cmds.write_text(_WRITE.format(meta_data=d / "meta-data", user_data=d / "user-data"))
        privops.run(["debugfs", "-w", "-f", str(cmds), str(paths.seed)])


def _meta_data(vm_id: str, vm_name: str) -> str:
    return f"instance-id: {vm_id}\nlocal-hostname: {vm_name}\n"


def _user_data(ssh_pubkey: str | None, has_data_volume: bool) -> str:
    lines = ["#cloud-config"]
    if ssh_pubkey:
        lines += [
            "users:",
            "  - name: root",
            "    ssh_authorized_keys:",
            f"      - {ssh_pubkey.strip()}",
        ]
    if has_data_volume:
        lines += [
            "mounts:",
            '  - [/dev/vdb, /home, ext4, "defaults,nofail", 0, 2]',
        ]
    return "\n".join(lines) + "\n"
