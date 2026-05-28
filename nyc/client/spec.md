# client

Pure-Python Firecracker client. No HTTP server, no globals, no `dadar`
imports. Routers depend on this; this depends on no nyc-internal module
above it.

## Modules

```
env/       per-VM directory setup (rootfs CoW copy, kernel symlink, ssh key)
vm/        firecracker process lifecycle + JSON config builder + rootfs inject
network/   netns, taps, bridges, VXLAN overlay, NAT, IP allocation
volume/    data volume create/delete
privops.py sudo shim + backend selector (exports PrivopsError)
privops_fake.py in-memory state mutator for NYC_BACKEND=fake
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
  `kill`. The file-only ops in `_NO_SUDO` (`truncate`, `mkfs.ext4`, `cp`,
  `debugfs`) run **unprivileged**: they only touch files the user already owns
  (the volume image, the per-VM rootfs copy), and sudo'ing them would make those
  root-owned so a later teardown/`rm -rf` as the user fails. The staging script
  writes a sudoers fragment scoped to the sudo'd set.
  A missing iptables rule/chain (`-C`/`-nL`) exits non-zero, surfaced as
  `PrivopsError` — the client uses that for check-then-add idempotency.

The client code never branches on backend — only `privops.run()` does.

## Lifecycle in one screen

`lifecycle/vm_up.run(spec)` composes bring-up (see `network/spec.md` for the
full interface layout):

```
env.setup(vm_dir, vm_id)        → CoW copies rootfs (writable), symlinks kernel + ssh key
vm.inject.run(paths, ...)       → debugfs edits on the per-VM rootfs copy:
                                   /root/.ssh/authorized_keys (if ssh_pubkey),
                                   /etc/resolv.conf (always), /etc/fstab (if data vol)
volume.attach(vm_dir, path)     → symlinks data.ext4 into the VM dir (if data volume)
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

Teardown (`lifecycle/vm_down`) deletes the netns first — the kernel auto-removes
the ns-side veth, `nbr0`, and `tap0`; only the host-side veth needs an explicit
delete. Per-VPC infra (bridge, VXLAN, NAT) is shared and outlives a single VM.

Data volume:

```
volume.create.run(path, size_mb)   # truncate -s, mkfs.ext4
volume.attach.run(vm_dir, path)    # symlink path into vm_dir/data.ext4
volume.delete.run(path)            # unlink
```
