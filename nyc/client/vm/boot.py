"""Send InstanceStart to a running firecracker process.

With `--config-file`, modern firecracker auto-starts after consuming the
config — no API call needed. We keep this function so callers have a stable
'boot' verb and so the fake backend can mark the socket 'running'.
"""
from nyc.client import privops
from nyc.client.env.paths import VmPaths


def run(paths: VmPaths) -> None:
    if privops.backend() == "fake":
        from nyc.client.privops_fake import STATE
        STATE["fc_socks"][str(paths.api_sock)] = {"vm_dir": str(paths.root), "running": True}
        return
