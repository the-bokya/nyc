"""Resolve filesystem paths nyc needs from a dadar node folder.

A dadar node folder is the cwd of `dadar run`. nyc plants its own runtime
state alongside dadar's: `vms/`, `volumes/` inside that folder. Shared assets
(kernel, rootfs, firecracker binary) live in the repo's `assets/` and `bin/`
directories, which the staging script populates.
"""
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def _read_toml(folder: Path) -> dict:
    """Read this node's config.toml. Stdlib only — the client must stay
    dadar-free, and dadar ignores the nyc-specific keys (lvm_*) we add here."""
    path = folder / "config.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


@dataclass(frozen=True)
class Paths:
    node_folder: Path
    repo_root: Path

    @property
    def vms_dir(self) -> Path:
        return self.node_folder / "vms"

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


@dataclass(frozen=True)
class LvmConfig:
    """LVM substrate for this node. `device` set => that block device is the PV
    (prod, one device per machine). `device` None => loopback over a sparse file
    in the node folder (local/staging), and the VG name is scoped per node so
    several staged nodes on one host never share a VG."""
    vg: str
    thinpool: str
    device: str | None
    node_folder: Path
    loop_size_gb: int
    base_rootfs_mb: int

    @property
    def loopback(self) -> bool:
        return self.device is None

    @property
    def loop_file(self) -> Path:
        return self.node_folder / ".lvm" / "pv.img"

    def vg_for(self, node_id: str) -> str:
        return self.vg if self.device else f"{self.vg}-{node_id[:8]}"


def lvm(node_folder: Path | None = None) -> LvmConfig:
    """Read the node's LVM config from config.toml (NYC_LVM_* env wins)."""
    folder = (node_folder or Path.cwd()).resolve()
    data = _read_toml(folder)
    s = lambda env, key, default: str(os.environ.get(env) or data.get(key) or default)
    i = lambda env, key, default: int(os.environ.get(env) or data.get(key) or default)
    return LvmConfig(
        vg=s("NYC_LVM_VG", "lvm_vg", "nyc"),
        thinpool=s("NYC_LVM_THINPOOL", "lvm_thinpool", "pool"),
        device=os.environ.get("NYC_LVM_DEVICE") or data.get("lvm_device") or None,
        node_folder=folder,
        loop_size_gb=i("NYC_LVM_LOOP_GB", "lvm_loop_gb", 20),
        base_rootfs_mb=i("NYC_LVM_ROOTFS_MB", "lvm_rootfs_mb", 4096),
    )


def volume_vg(node_id: str, node_folder: Path | None = None) -> str:
    return lvm(node_folder).vg_for(node_id)


def cluster_domain(node_folder: Path | None = None) -> str | None:
    """Cluster root domain (e.g. 'example.com'). Env NYC_DOMAIN wins."""
    override = os.environ.get("NYC_DOMAIN")
    if override:
        return override
    folder = (node_folder or Path.cwd()).resolve()
    return _read_toml(folder).get("domain") or None


@dataclass(frozen=True)
class PubipConfig:
    provider: str
    iface: str
    addresses: list[str]
    gateway: str | None
    scaleway_zone: str | None
    scaleway_project_id: str | None
    scaleway_server_id: str | None
    secret_key: str | None  # SCW_SECRET_KEY env


def pubip(node_folder: Path | None = None) -> PubipConfig:
    """Read public-IP config (NYC_PUBIP_* env wins over config.toml)."""
    folder = (node_folder or Path.cwd()).resolve()
    data = _read_toml(folder)
    s = lambda env, key, default=None: (os.environ.get(env) or data.get(key) or default) or None
    ss = lambda env, key, default: str(os.environ.get(env) or data.get(key) or default)
    raw_ips = os.environ.get("NYC_PUBLIC_IPS") or data.get("public_ips") or []
    if isinstance(raw_ips, str):
        raw_ips = [a.strip() for a in raw_ips.split(",") if a.strip()]
    return PubipConfig(
        provider=ss("NYC_PUBIP_PROVIDER", "pubip_provider", "scaleway"),
        iface=ss("NYC_PUBLIC_IFACE", "public_iface", "eth0"),
        addresses=list(raw_ips),
        gateway=s("NYC_PUBIP_GATEWAY", "pubip_gateway"),
        scaleway_zone=s("NYC_SCW_ZONE", "scaleway_zone"),
        scaleway_project_id=s("NYC_SCW_PROJECT_ID", "scaleway_project_id"),
        scaleway_server_id=s("NYC_SCW_SERVER_ID", "scaleway_server_id"),
        secret_key=os.environ.get("SCW_SECRET_KEY"),
    )
