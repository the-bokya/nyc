"""Resolve filesystem paths nyc needs from a dadar node folder.

A dadar node folder is the cwd of `dadar run`. nyc plants its own runtime
state alongside dadar's: `vms/`, `volumes/` inside that folder. Shared assets
(kernel, rootfs, firecracker binary) live in the repo's `assets/` and `bin/`
directories, which the staging script populates.
"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    node_folder: Path
    repo_root: Path

    @property
    def vms_dir(self) -> Path:
        return self.node_folder / "vms"

    @property
    def volumes_dir(self) -> Path:
        return self.node_folder / "volumes"

    @property
    def firecracker_bin(self) -> Path:
        override = os.environ.get("NYC_FIRECRACKER")
        return Path(override) if override else self.repo_root / "bin" / "firecracker"

    @property
    def kernel(self) -> Path:
        return self.repo_root / "assets" / "vmlinux"

    @property
    def rootfs(self) -> Path:
        return self.repo_root / "assets" / "rootfs.ext4"

    @property
    def ssh_key(self) -> Path:
        return self.repo_root / "assets" / "id_ed25519"


def resolve(node_folder: Path | None = None) -> Paths:
    folder = node_folder or Path.cwd()
    return Paths(node_folder=folder.resolve(), repo_root=_find_repo_root(folder))


def _find_repo_root(start: Path) -> Path:
    for d in [start.resolve(), *start.resolve().parents]:
        if (d / "pyproject.toml").exists() and (d / "nyc").is_dir():
            return d
    return Path(__file__).resolve().parents[2]
