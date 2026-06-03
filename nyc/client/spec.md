# client

Pure-Python Firecracker client. No HTTP server, no globals, no `dadar`
imports. Routers depend on this; this depends on no nyc-internal module
above it.

## Modules

```
env/       per-VM directory setup (rootfs = thin clone of a golden, kernel + ssh-key symlinks)
vm/        firecracker process lifecycle + JSON config builder + rootfs inject
network/   netns, taps, bridges, VXLAN overlay, NAT, IP allocation
volume/    LVM thin volumes: lv primitives, pool substrate, data volumes,
           snapshots/goldens, role-naming, attach
privops.py sudo shim + backend selector (exports PrivopsError)
privops_fake.py in-memory state mutator for NYC_BACKEND=fake (incl. an LVM model)
```

`network/` is the bulk of the surface — per-VPC anycast bridges joined across
nodes by a VXLAN tunnel, plus iptables NAT for internet egress. See
`network/spec.md` for the per-module contract and `../../NETWORKING.md` for
ground-up concepts.

Every action is a file with one public `run(...)` function ≤ 12 lines.
Helpers stay in the same file (also ≤ 12 lines). Cross-file imports prefer
the action functions, not the helpers.

## Backend selection

`privops.backend()` reads `NYC_BACKEND`. Default is `fake`.

- `fake`: state mutations land in `privops_fake.STATE`. Tests call
  `privops.reset_state()` per-fixture.
- `real`: `subprocess.run(["sudo", "-n", *argv])` for the kernel/namespace ops
  — `ip`, `bridge`, `iptables`, `sysctl`, `mount`, `umount`, `firecracker`,
  `kill` — **and** the LVM/device toolchain (`lvm`/`pv*`/`vg*`/`lv*`, `losetup`,
  `dmsetup`, `dd`, `mkfs.ext4`, `debugfs`, `resize2fs`), since LV device nodes
  are root-owned. Only `truncate` is in `_NO_SUDO` (it creates the loopback
  backing file the user owns; sudo'ing it would make it root-owned so a later
  `rm -rf` as the user fails). The staging/deploy scripts write a sudoers
  fragment scoped to the sudo'd set. A missing iptables rule/chain (`-C`/`-nL`)
  exits non-zero, surfaced as `PrivopsError` — used for check-then-add
  idempotency; `lv.list_lvs`/`vg_exists` likewise treat a `PrivopsError` (e.g.
  VG absent) as empty.

The client code never branches on backend — only `privops.run()` does.

## Lifecycle in one screen

`lifecycle/vm_up.run(spec)` composes bring-up (see `network/spec.md` for the
full interface layout):

```
env.setup(vms_dir, vm_id, assets, vg, rootfs_origin)
                                → thin-clones the golden LV into a per-VM rootfs LV
                                  (writable CoW overlay), symlinks its device + kernel + ssh key
vm.inject.run(paths, ...)       → debugfs edits on the per-VM rootfs clone (its device):
                                   /root/.ssh/authorized_keys (if ssh_pubkey),
                                   /etc/resolv.conf (always), /etc/fstab (if data vol)
volume.attach(vm_dir, device)   → symlinks the data volume LV device into the VM dir (if data volume)
network.bridge.ensure(br, gateway_cidr, mac=anycast_mac(vpc))  → anycast gateway
network.vxlan.ensure + set_fdb  → per-VPC tunnel to peers (skipped single-host)
network.nat.ensure(cidr)        → ip_forward + masquerade for internet egress
network.namespace.create(ns)
veth pair → place ns side → attach host side to bridge → up
network.ns_bridge.create(ns) + tap.create(ns, tap0) joined in nbr0
vm.config.build(vm_dir, cfg)    → firecracker JSON config on disk
                                   drives: rootfs (rw) [+ data (rw)]
vm.create.run(vm_dir, cfg)      → spawns `firecracker --api-sock ... --config ...`
vm.boot.run(vm_dir, cfg)        → issues InstanceStart over the API socket
```

Teardown (`lifecycle/vm_down(vms_dir, vm_id, vg)`) deletes the netns first — the
kernel auto-removes the ns-side veth, `nbr0`, and `tap0`; only the host-side
veth needs an explicit delete — then `env.teardown` rmtrees the dir and
`lvremove`s the per-VM rootfs clone LV. Per-VPC infra (bridge, VXLAN, NAT) and
the data volume are shared/independent and outlive a single VM.

Data volume (a thin LV in the node's pool):

```
volume.create.run(vg, pool, name, size_mb)   # lvcreate --type thin + mkfs.ext4 on the device
volume.attach.run(vm_dir, device)             # symlink /dev/<vg>/<lv> into vm_dir/data.ext4
volume.delete.run(vg, name)                   # lvremove -f
```

Substrate + images (see `volume/spec.md`): `volume.pool.ensure` builds the VG +
thin pool + default golden at startup; `volume.snapshot.{create,golden}` take a
read-only snapshot of a volume / derive a golden from a snapshot; a per-VM
rootfs is `lv.clone` of a golden.
