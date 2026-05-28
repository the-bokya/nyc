"""Offline per-VM rootfs configuration via debugfs.

Cloud-init is absent from the Firecracker Ubuntu image; we configure
per-VM concerns by editing the writable per-VM rootfs copy before boot.
One debugfs session covers SSH key, resolv.conf, and optional data-volume
mount (fstab entry + explicit systemd unit — belt and suspenders for minimal
images that may lack systemd-fstab-generator).
No mount, no loop device, no root required.
"""
import tempfile
from pathlib import Path

from nyc.client import privops
from nyc.client.env.paths import VmPaths

_MOUNT_UNIT = """\
[Unit]
Description=Mount /home data volume
DefaultDependencies=no
Before=local-fs.target
After=local-fs-pre.target

[Mount]
What=/dev/vdb
Where=/home
Type=ext4
Options=defaults

[Install]
WantedBy=local-fs.target
"""


def run(paths: VmPaths, ssh_pubkey: str | None, dns: str, has_data_volume: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        cmds = d / "cmds"
        cmds.write_text("\n".join(_lines(d, ssh_pubkey, dns, has_data_volume)) + "\n")
        privops.run(["debugfs", "-w", "-f", str(cmds), str(paths.rootfs)])


def _lines(d: Path, ssh_pubkey: str | None, dns: str, has_data_volume: bool) -> list[str]:
    out = []
    if ssh_pubkey:
        out += _ssh(d, ssh_pubkey)
    out += _resolv(d, dns)
    if has_data_volume:
        out += _fstab(d) + _home_mount_unit(d)
    return out


def _ssh(d: Path, pubkey: str) -> list[str]:
    f = d / "authorized_keys"
    f.write_text(pubkey.strip() + "\n")
    return [
        "mkdir /root/.ssh",
        "rm /root/.ssh/authorized_keys",
        f"write {f} /root/.ssh/authorized_keys",
        "set_inode_field /root/.ssh/authorized_keys mode 0100600",
        "set_inode_field /root/.ssh/authorized_keys uid 0",
        "set_inode_field /root/.ssh/authorized_keys gid 0",
        "set_inode_field /root/.ssh mode 040700",
    ]


def _resolv(d: Path, dns: str) -> list[str]:
    f = d / "resolv.conf"
    f.write_text(f"nameserver {dns}\n")
    return [
        "rm /etc/resolv.conf",
        f"write {f} /etc/resolv.conf",
        "set_inode_field /etc/resolv.conf mode 0100644",
        "set_inode_field /etc/resolv.conf uid 0",
        "set_inode_field /etc/resolv.conf gid 0",
    ]


def _fstab(d: Path) -> list[str]:
    f = d / "fstab"
    f.write_text("/dev/vdb\t/home\text4\tdefaults,nofail\t0\t2\n")
    return [
        "rm /etc/fstab",
        f"write {f} /etc/fstab",
        "set_inode_field /etc/fstab mode 0100644",
        "set_inode_field /etc/fstab uid 0",
        "set_inode_field /etc/fstab gid 0",
    ]


def _home_mount_unit(d: Path) -> list[str]:
    f = d / "home.mount"
    f.write_text(_MOUNT_UNIT)
    return [
        "mkdir /etc/systemd/system/local-fs.target.wants",
        f"write {f} /etc/systemd/system/home.mount",
        "set_inode_field /etc/systemd/system/home.mount mode 0100644",
        "symlink /etc/systemd/system/local-fs.target.wants/home.mount ../home.mount",
    ]
