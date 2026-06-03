"""Run a shell script inside a guest VM over root-namespace SSH.

The owner node's root namespace has the VPC bridge carrying a connected route
to the VPC CIDR, so `ssh root@<vm_ip>` works directly from the node process
(no netns wrapper needed). The shared asset key `assets/id_ed25519` is baked
into every rootfs by `scripts/provision.py:_bake_cmd`.
"""
from nyc.client import privops

_SSH = [
    "ssh",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=10",
]


def run(ip: str, key: str, script: str, timeout: int = 60) -> str:
    argv = [*_SSH, "-i", key, f"root@{ip}", "bash", "-s"]
    return privops.run(argv, input=script)
