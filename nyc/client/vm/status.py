import os

from nyc.client import privops
from nyc.client.env.paths import VmPaths


def run(paths: VmPaths) -> str:
    if privops.backend() == "fake":
        return _fake_status(paths)
    return _real_status(paths)


def _real_status(paths: VmPaths) -> str:
    if not paths.pid_file.exists():
        return "stopped"
    pid = int(paths.pid_file.read_text().strip() or "0")
    if pid <= 0:
        return "stopped"
    try:
        os.kill(pid, 0)
        return "running"
    except OSError:
        return "stopped"


def _fake_status(paths: VmPaths) -> str:
    from nyc.client.privops_fake import STATE
    sock = str(paths.api_sock)
    entry = STATE["fc_socks"].get(sock)
    return "running" if entry and entry.get("running") else "stopped"
