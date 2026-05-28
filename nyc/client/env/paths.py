"""Filesystem layout for a single VM directory.

```
<vms_dir>/<vm_id>/
├── rootfs.ext4   per-VM CoW copy of assets/rootfs.ext4 (writable)
├── seed.ext4     cloud-init NoCloud seed (cidata label)
├── vmlinux       -> symlink to assets/vmlinux
├── id_ed25519    -> symlink to assets/id_ed25519 (private key for ssh-in)
├── id_ed25519.pub
├── config.json   (firecracker JSON config, written by vm.config.build)
├── api.sock      (firecracker socket, created on boot)
├── data.ext4     -> symlink to volume file, if a data volume is attached
└── pid           (firecracker pid, written on boot)
```
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VmPaths:
    root: Path

    @property
    def rootfs(self) -> Path:  return self.root / "rootfs.ext4"
    @property
    def seed(self) -> Path:    return self.root / "seed.ext4"
    @property
    def kernel(self) -> Path:  return self.root / "vmlinux"
    @property
    def ssh_key(self) -> Path: return self.root / "id_ed25519"
    @property
    def config(self) -> Path:  return self.root / "config.json"
    @property
    def api_sock(self) -> Path: return self.root / "api.sock"
    @property
    def data(self) -> Path:    return self.root / "data.ext4"
    @property
    def pid_file(self) -> Path: return self.root / "pid"
    @property
    def log_fifo(self) -> Path: return self.root / "log.fifo"


def for_vm(vms_dir: Path, vm_id: str) -> VmPaths:
    return VmPaths(root=(vms_dir / vm_id))
