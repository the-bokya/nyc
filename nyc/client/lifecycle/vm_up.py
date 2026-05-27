"""Compose: env + host bridge + per-VM netns wired through veth + nbr0 + tap0.

Layout:
  host:           br-<node>-<vpc>  with gateway IP on the VPC's CIDR
  host:           vmh-<vm>         (veth host side, joined to host bridge)
  netns vm-<vm>:  vmn-<vm>         (veth ns side, bridged in nbr0)
  netns vm-<vm>:  nbr0             (bridge joining vmn and tap0)
  netns vm-<vm>:  tap0             (firecracker NIC, no IP)
  guest:          eth0 ← kernel boot arg `ip=10.x.x.y::10.x.x.1:...`
"""
from dataclasses import dataclass
from pathlib import Path

from nyc.client.env import setup as env_setup
from nyc.client.network import bridge, namespace, ns_bridge, tap, veth
from nyc.client.network.allocate import gateway_cidr
from nyc.client.vm import boot, config, create
from nyc.client.volume import attach


@dataclass(frozen=True)
class VmSpec:
    vm_id: str
    node_id: str
    vpc_id: str
    ip: str
    cidr: str
    data_volume_path: Path | None
    assets: dict
    vms_dir: Path
    firecracker_bin: Path


def run(spec: VmSpec) -> Path:
    paths = env_setup.run(spec.vms_dir, spec.vm_id, spec.assets)
    if spec.data_volume_path is not None:
        attach.run(paths.root, spec.data_volume_path)
    _network(spec)
    _spawn(paths, spec)
    return paths.root


def _network(spec: VmSpec) -> None:
    ns = _ns_name(spec.vm_id)
    host_veth, ns_veth = _veth_names(spec.vm_id)
    bridge.ensure(bridge.name_for(spec.node_id, spec.vpc_id), gateway_cidr(spec.cidr))
    namespace.create(ns)
    _wire_veth(spec, ns, host_veth, ns_veth)
    _wire_tap(ns, ns_veth)


def _wire_veth(spec: VmSpec, ns: str, host_veth: str, ns_veth: str) -> None:
    veth.create_pair(host_veth, ns_veth)
    veth.place_in_ns(ns_veth, ns)
    bridge.attach(bridge.name_for(spec.node_id, spec.vpc_id), host_veth)
    veth.up(host_veth)


def _wire_tap(ns: str, ns_veth: str) -> None:
    ns_bridge.create(ns)
    ns_bridge.attach(ns, ns_veth)
    tap.create(ns, "tap0")
    ns_bridge.attach(ns, "tap0")


def _spawn(paths, spec: VmSpec) -> None:
    cfg = config.VmConfig(vm_id=spec.vm_id, tap_name="tap0", mac=_mac(spec.vm_id),
                          guest_ip=spec.ip, cidr=spec.cidr,
                          has_data_volume=spec.data_volume_path is not None)
    config.build(paths, cfg)
    create.run(paths, spec.vm_id, _ns_name(spec.vm_id), spec.firecracker_bin)
    boot.run(paths)


def _ns_name(vm_id: str) -> str:
    return f"vm-{vm_id[:8]}"


def _veth_names(vm_id: str) -> tuple[str, str]:
    return f"vmh-{vm_id[:8]}", f"vmn-{vm_id[:8]}"


def _mac(vm_id: str) -> str:
    h = vm_id.replace("-", "")[:10]
    return "02:" + ":".join(h[i:i+2] for i in range(0, 10, 2))
