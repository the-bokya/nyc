import os
import time

from nyc.client import privops
from nyc.client.env.paths import VmPaths


def run(paths: VmPaths) -> None:
    if not paths.pid_file.exists():
        _fake_cleanup(paths)
        return
    pid = int(paths.pid_file.read_text().strip() or "0")
    if pid > 0:
        _terminate(pid)
    paths.pid_file.unlink(missing_ok=True)
    _fake_cleanup(paths)


def _terminate(pid: int) -> None:
    try:
        privops.run(["kill", str(pid)])
        _wait_gone(pid, timeout=3.0)
    except (PermissionError, OSError):
        pass


def _wait_gone(pid: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def _fake_cleanup(paths: VmPaths) -> None:
    if privops.backend() != "fake":
        return
    from nyc.client.privops_fake import STATE
    STATE["fc_socks"].pop(str(paths.api_sock), None)
