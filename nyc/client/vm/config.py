"""Build the firecracker JSON config the binary reads on `--config-file`.

Kernel boot args include `ip=<vm>::<gw>:<netmask>::eth0:off` so the guest's
eth0 is configured at kernel init time, before userspace runs. This is what
makes SSH-to-guest work without a DHCP server.

Drive order (determines /dev/vd* names in guest):
  vda  rootfs   — writable per-VM copy, is_root_device=true
  vdb  data     — present only when has_data_volume; cloud-init mounts at /home
  vdc  seed     — cloud-init NoCloud cidata (or vdb when no data volume)
"""
import json
from dataclasses import dataclass
from pathlib import Path

from nyc.client.env.paths import VmPaths
from nyc.client.network.allocate import gateway, netmask


@dataclass(frozen=True)
class VmConfig:
    vm_id: str
    tap_name: str
    mac: str
    guest_ip: str
    cidr: str
    has_data_volume: bool = False
    vcpu_count: int = 1
    mem_mib: int = 512
    dns: str = "1.1.1.1"


def build(paths: VmPaths, cfg: VmConfig) -> Path:
    payload = _payload(paths, cfg)
    paths.config.write_text(json.dumps(payload, indent=2))
    return paths.config


def _payload(paths: VmPaths, cfg: VmConfig) -> dict:
    drives = [_root_drive(paths)]
    if cfg.has_data_volume:
        drives.append(_data_drive(paths))
    drives.append(_seed_drive(paths))
    return {
        "boot-source":        {"kernel_image_path": str(paths.kernel), "boot_args": _boot_args(cfg)},
        "drives":             drives,
        "machine-config":     {"vcpu_count": cfg.vcpu_count, "mem_size_mib": cfg.mem_mib},
        "network-interfaces": [{"iface_id": "eth0", "host_dev_name": cfg.tap_name, "guest_mac": cfg.mac}],
    }


def _boot_args(cfg: VmConfig) -> str:
    ip = f"ip={cfg.guest_ip}::{gateway(cfg.cidr)}:{netmask(cfg.cidr)}::eth0:off:{cfg.dns}"
    return f"console=ttyS0 reboot=k panic=1 pci=off {ip}"


def _root_drive(paths: VmPaths) -> dict:
    return {"drive_id": "rootfs", "path_on_host": str(paths.rootfs), "is_root_device": True,  "is_read_only": False}


def _data_drive(paths: VmPaths) -> dict:
    return {"drive_id": "data",   "path_on_host": str(paths.data),   "is_root_device": False, "is_read_only": False}


def _seed_drive(paths: VmPaths) -> dict:
    return {"drive_id": "seed",   "path_on_host": str(paths.seed),   "is_root_device": False, "is_read_only": True}
