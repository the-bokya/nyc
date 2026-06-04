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
class PubIpEntry:
    address: str
    mac: str
    prefix: str = "32"


@dataclass(frozen=True)
class PubipConfig:
    iface: str          # physical public NIC (for provision/teardown)
    ips: list           # list[PubIpEntry]
    gateway: str | None
    bridge: str         # public bridge name (default pub0)


def pubip(node_folder: Path | None = None) -> PubipConfig:
    """Read public-IP config (NYC_PUBLIC_* env wins over config.toml).

    NYC_PUBLIC_IPS accepts "addr|mac,addr|mac" CSV.
    config.toml accepts public_ips as array of inline tables: [{address="...", mac="..."}].
    """
    folder = (node_folder or Path.cwd()).resolve()
    data = _read_toml(folder)
    s = lambda env, key, default=None: (os.environ.get(env) or data.get(key) or default) or None
    ss = lambda env, key, default: str(os.environ.get(env) or data.get(key) or default)

    raw_ips = os.environ.get("NYC_PUBLIC_IPS") or data.get("public_ips") or []
    if isinstance(raw_ips, str):
        ips = []
        for item in raw_ips.split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split("|", 1)
            ips.append(PubIpEntry(address=parts[0], mac=parts[1] if len(parts) > 1 else ""))
    elif isinstance(raw_ips, list):
        ips = []
        for e in raw_ips:
            if isinstance(e, dict):
                ips.append(PubIpEntry(address=e["address"], mac=e["mac"],
                                      prefix=e.get("prefix", "32")))
            else:
                ips.append(PubIpEntry(address=str(e), mac=""))
    else:
        ips = []

    return PubipConfig(
        iface=ss("NYC_PUBLIC_IFACE", "public_iface", "eth0"),
        ips=ips,
        gateway=s("NYC_PUBIP_GATEWAY", "pubip_gateway"),
        bridge=ss("NYC_PUBLIC_BRIDGE", "public_bridge", "pub0"),
    )
