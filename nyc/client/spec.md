# client

Pure-Python Firecracker client. No HTTP server, no globals, no `dadar`
imports. Routers depend on this; this depends on no nyc-internal module
above it.

## Modules

```
env/       per-VM directory setup (rootfs symlink, kernel symlink, ssh key)
vm/        firecracker process lifecycle + JSON config builder
network/   netns, taps, bridges, IP allocation
volume/    data volume create/delete
privops.py sudo shim + backend selector
privops_fake.py in-memory state mutator for NYC_BACKEND=fake
```

Every action is a file with one public `run(...)` function ≤ 12 lines.
Helpers stay in the same file (also ≤ 12 lines). Cross-file imports prefer
the action functions, not the helpers.

## Backend selection

`privops.backend()` reads `NYC_BACKEND`. Default is `fake`.

- `fake`: state mutations land in `privops_fake.STATE`. Tests call
  `privops.reset_state()` per-fixture.
- `real`: `subprocess.run(["sudo", "-n", *argv])`. Requires passwordless
  sudo for `ip`, `mkfs.ext4`, `mount`, `umount`, `brctl`, `firecracker`,
  `kill`, `truncate`. The staging script writes a sudoers fragment scoped to
  these commands.

The client code never branches on backend — only `privops.run()` does.

## Lifecycle in one screen

```
env.setup(vm_dir, vm_id)        → writes rootfs symlink, kernel symlink, ssh key
network.namespace.create(ns)
network.tap.create(ns, tap, ip, peer_ip)
network.bridge.ensure(br, cidr)
network.bridge.attach(br, host_veth)
vm.config.build(vm_dir, cfg)    → firecracker JSON config on disk
vm.create.run(vm_dir, cfg)      → spawns `firecracker --api-sock ... --config ...`
vm.boot.run(vm_dir, cfg)        → issues InstanceStart over the API socket
vm.status.run(vm_dir)           → returns "running" | "stopped"
vm.kill.run(vm_dir)             → SIGTERM, then SIGKILL after grace
network.tap.delete(ns, tap)
network.namespace.delete(ns)
env.teardown(vm_dir)            → rm -rf vm_dir
```

Data volume:

```
volume.create.run(path, size_mb)   # truncate -s, mkfs.ext4
volume.attach.run(vm_dir, path)    # symlink path into vm_dir/data.ext4
volume.delete.run(path)            # unlink
```
