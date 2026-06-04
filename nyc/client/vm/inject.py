"""Offline per-VM rootfs configuration via debugfs.

Cloud-init is absent from the Firecracker Ubuntu image; we configure
per-VM concerns by editing the writable per-VM rootfs copy before boot.
One debugfs session covers SSH key, resolv.conf, optional data-volume
mount, and optional public-IP configuration.
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

_PUBIP_SCRIPT = """\
#!/bin/sh
set -e
ip link set eth1 up
ip addr add {address}/{prefix} dev eth1
ip route add {gateway} dev eth1
ip route add default via {gateway} dev eth1 table 100
ip rule add from {address} table 100
sysctl -w net.ipv4.conf.all.rp_filter=2
"""

_PUBIP_SERVICE = """\
[Unit]
Description=Configure public IP on eth1
After=network.target
DefaultDependencies=no

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/nyc-pubip.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def run(paths: VmPaths, ssh_pubkey: str | None, dns: str, has_data_volume: bool,
        public_ip=None) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        cmds = d / "cmds"
        cmds.write_text("\n".join(_lines(d, ssh_pubkey, dns, has_data_volume, public_ip)) + "\n")
        privops.run(["debugfs", "-w", "-f", str(cmds), str(paths.rootfs)])


def _lines(d: Path, ssh_pubkey: str | None, dns: str, has_data_volume: bool,
           public_ip=None) -> list[str]:
    out = []
    if ssh_pubkey:
        out += _ssh(d, ssh_pubkey)
    out += _resolv(d, dns)
    if has_data_volume:
        out += _fstab(d) + _home_mount_unit(d)
    if public_ip:
        out += _pubip_unit(d, public_ip)
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


def _pubip_unit(d: Path, public_ip) -> list[str]:
    sh = d / "nyc-pubip.sh"
    svc = d / "nyc-pubip.service"
    sh.write_text(_PUBIP_SCRIPT.format(
        address=public_ip.address,
        prefix=public_ip.prefix,
        gateway=public_ip.gateway,
    ))
    svc.write_text(_PUBIP_SERVICE)
    return [
        f"write {sh} /usr/local/sbin/nyc-pubip.sh",
        "set_inode_field /usr/local/sbin/nyc-pubip.sh mode 0100755",
        "set_inode_field /usr/local/sbin/nyc-pubip.sh uid 0",
        "set_inode_field /usr/local/sbin/nyc-pubip.sh gid 0",
        f"write {svc} /etc/systemd/system/nyc-pubip.service",
        "set_inode_field /etc/systemd/system/nyc-pubip.service mode 0100644",
        "set_inode_field /etc/systemd/system/nyc-pubip.service uid 0",
        "set_inode_field /etc/systemd/system/nyc-pubip.service gid 0",
        "mkdir /etc/systemd/system/multi-user.target.wants",
        "symlink /etc/systemd/system/multi-user.target.wants/nyc-pubip.service ../nyc-pubip.service",
    ]
