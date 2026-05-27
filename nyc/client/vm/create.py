"""Spawn the firecracker process. Returns pid; the run loop is firecracker's own."""
import subprocess
from pathlib import Path

from nyc.client import privops
from nyc.client.env.paths import VmPaths


def run(paths: VmPaths, vm_id: str, ns: str, firecracker_bin: Path) -> int:
    if privops.backend() == "fake":
        return _fake(paths, vm_id)
    return _real(paths, vm_id, ns, firecracker_bin)


def _real(paths: VmPaths, vm_id: str, ns: str, firecracker_bin: Path) -> int:
    log = (paths.root / "firecracker.log").open("a")
    argv = ["sudo", "-n", "ip", "netns", "exec", ns,
            str(firecracker_bin), "--api-sock", str(paths.api_sock),
            "--id", vm_id, "--config-file", str(paths.config)]
    proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)
    paths.pid_file.write_text(str(proc.pid))
    return proc.pid


def _fake(paths: VmPaths, vm_id: str) -> int:
    privops.run(["firecracker", "--api-sock", str(paths.api_sock), "--id", vm_id])
    paths.pid_file.write_text("0")
    return 0
