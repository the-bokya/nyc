"""Bake a public ssh key into a VM's own rootfs so the guest accepts it.

Offline edit via `debugfs` (e2fsprogs): no mount, no loopback, no boot. The
shared rootfs is read-only and identical for every VM, so per-VM keys require
a per-VM rootfs copy (see `env.setup(copy_rootfs=True)`); this writes
`/root/.ssh/authorized_keys` into that copy. Routed through `privops` as a
single batch (`debugfs -f`) so `fake` records it and `real` runs it.
"""
import tempfile
from pathlib import Path

from nyc.client import privops

_SCRIPT = """\
mkdir /root/.ssh
rm /root/.ssh/authorized_keys
write {keyfile} /root/.ssh/authorized_keys
set_inode_field /root/.ssh/authorized_keys mode 0100600
set_inode_field /root/.ssh/authorized_keys uid 0
set_inode_field /root/.ssh/authorized_keys gid 0
set_inode_field /root/.ssh mode 040700
"""


def run(rootfs: Path, pubkey: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        keyfile = Path(d) / "authorized_keys"
        keyfile.write_text(pubkey.strip() + "\n")
        cmds = Path(d) / "debugfs.cmds"
        cmds.write_text(_SCRIPT.format(keyfile=keyfile))
        privops.run(["debugfs", "-w", "-f", str(cmds), str(rootfs)])
