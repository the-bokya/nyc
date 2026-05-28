"""Build the firecracker JSON config the binary reads on `--config-file`.

Kernel boot args include `ip=<vm>::<gw>:<netmask>::eth0:off` so the guest's
eth0 is configured at kernel init time, before userspace runs. This is what
makes SSH-to-guest work without a DHCP server.
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
    drives = [_root_drive(paths)] + ([_data_drive(paths)] if cfg.has_data_volume else [])
    return {
        "boot-source":     {"kernel_image_path": str(paths.kernel), "boot_args": _boot_args(cfg)},
        "drives":          drives,
        "machine-config":  {"vcpu_count": cfg.vcpu_count, "mem_size_mib": cfg.mem_mib},
        "network-interfaces": [{"iface_id": "eth0", "host_dev_name": cfg.tap_name, "guest_mac": cfg.mac}],
    }


def _boot_args(cfg: VmConfig) -> str:
    # ip=<client>:<server>:<gw>:<netmask>:<host>:<dev>:<autoconf>:<dns0>
    # The trailing dns0 field seeds the guest resolver at kernel init; we also
    # bake /etc/resolv.conf into the rootfs since some images ignore it.
    ip = f"ip={cfg.guest_ip}::{gateway(cfg.cidr)}:{netmask(cfg.cidr)}::eth0:off:{cfg.dns}"
    return f"console=ttyS0 reboot=k panic=1 pci=off {ip}"


def _root_drive(paths: VmPaths) -> dict:
    return {"drive_id": "rootfs", "path_on_host": str(paths.rootfs), "is_root_device": True, "is_read_only": True}


def _data_drive(paths: VmPaths) -> dict:
    return {"drive_id": "data", "path_on_host": str(paths.data), "is_root_device": False, "is_read_only": False}
