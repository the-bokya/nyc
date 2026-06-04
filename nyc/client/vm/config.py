"""Build the firecracker JSON config the binary reads on `--config-file`.

Kernel boot args include `ip=<vm>::<gw>:<netmask>::eth0:off` so the guest's
eth0 is configured at kernel init time, before userspace runs.

When public_tap/public_mac are set a second network interface (eth1) is added.
eth1 is configured by the injected nyc-pubip.service, not kernel boot args.

Drive order (determines /dev/vd* names in guest):
  vda  rootfs   — writable per-VM copy, is_root_device=true
  vdb  data     — present only when has_data_volume; fstab mounts it at /home
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
    public_tap: str | None = None
    public_mac: str | None = None


def build(paths: VmPaths, cfg: VmConfig) -> Path:
    payload = _payload(paths, cfg)
    paths.config.write_text(json.dumps(payload, indent=2))
    return paths.config


def _payload(paths: VmPaths, cfg: VmConfig) -> dict:
    drives = [_root_drive(paths)] + ([_data_drive(paths)] if cfg.has_data_volume else [])
    nics = [{"iface_id": "eth0", "host_dev_name": cfg.tap_name, "guest_mac": cfg.mac}]
    if cfg.public_tap:
        nics.append({"iface_id": "eth1", "host_dev_name": cfg.public_tap,
                     "guest_mac": cfg.public_mac})
    return {
        "boot-source":        {"kernel_image_path": str(paths.kernel), "boot_args": _boot_args(cfg)},
        "drives":             drives,
        "machine-config":     {"vcpu_count": cfg.vcpu_count, "mem_size_mib": cfg.mem_mib},
        "network-interfaces": nics,
    }


def _boot_args(cfg: VmConfig) -> str:
    ip = f"ip={cfg.guest_ip}::{gateway(cfg.cidr)}:{netmask(cfg.cidr)}::eth0:off:{cfg.dns}"
    return f"console=ttyS0 reboot=k panic=1 pci=off {ip}"


def _root_drive(paths: VmPaths) -> dict:
    return {"drive_id": "rootfs", "path_on_host": str(paths.rootfs), "is_root_device": True,  "is_read_only": False}


def _data_drive(paths: VmPaths) -> dict:
    return {"drive_id": "data",   "path_on_host": str(paths.data),   "is_root_device": False, "is_read_only": False}
