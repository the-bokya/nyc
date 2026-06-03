# client/lifecycle

The two composition functions that turn the lower-level primitives (`env`,
`network`, `vm`, `volume`) into a working VM and back again. This is the only
client module that orchestrates across the others; everything below it does
exactly one thing. Routers and the reconciler call **these**, not the
primitives.

## Actions

| File | Public fn | Does |
|---|---|---|
| `vm_up.py` | `run(spec: VmSpec) -> Path` | Build a VM end to end; returns its dir. |
| `vm_down.py` | `run(vms_dir, vm_id)` | Tear a VM down completely. |
| `vm_stop.py` | `run(vms_dir, vm_id)` | Kill firecracker only; keep all else on disk. |
| `vm_start.py` | `run(vms_dir, vm_id, ns, firecracker_bin)` | Respawn firecracker from the on-disk `config.json`. |

## `VmSpec` (input to `vm_up`)

Frozen dataclass: `vm_id`, `vm_name`, `node_id`, `vpc_id`, `ip`, `cidr`,
`data_volume_path: Path | None`, `assets: dict`, `vms_dir: Path`,
`firecracker_bin: Path`, `ssh_pubkey: str | None`. The caller (router) resolves
the IP via `network.allocate.pick_ip` and the volume path before constructing
this.

## Bring-up order (`vm_up`)

1. `env.setup` — CoW-copy rootfs (writable per-VM), symlink kernel + ssh key.
2. `vm.inject.run` — one debugfs session on the per-VM rootfs copy:
   - `/root/.ssh/authorized_keys` — writes `ssh_pubkey` (if provided)
   - `/etc/resolv.conf` — writes `nameserver <dns>` (always)
   - `/etc/fstab` + a systemd `home.mount` unit — mounts `/dev/vdb` at `/home`
     (if data volume; the unit is belt-and-suspenders for images lacking the
     fstab generator)

   The Firecracker Ubuntu CI image has no cloud-init; offline debugfs edits on
   the per-VM copy are the only way to inject per-VM config at rest.
3. If `data_volume_path`: `volume.attach` it as `data.ext4`.
4. `_network`: ensure the VPC bridge (with gateway IP) → create netns →
   veth pair (host side joins the VPC bridge) → in-netns `nbr0` + `tap0`.
5. `_spawn`: build `VmConfig` → `config.build` → `vm.create` (inside the
   netns) → `vm.boot`.

Drive order in the Firecracker config:
- `vda` rootfs — writable per-VM copy, `is_root_device=true`
- `vdb` data   — present only when `data_volume_path` is set; fstab mounts at `/home`

The guest MAC is derived deterministically from `vm_id` (`_mac`), and the
netns/veth names from `vm_id[:8]` (`_ns_name`, `_veth_names`) — so the same VM
always maps to the same interfaces, which is what makes teardown and the
reconciler able to find resources by id alone.

## Tear-down order (`vm_down`)

`kill` firecracker → `_network_down` → `env.teardown` (rmtree the dir).
`_network_down` deletes the netns **first** (kernel auto-removes the ns-side
veth, `nbr0`, `tap0`), then the host-side veth. Each network step is wrapped in
`_safe` so a missing resource (already-gone VM, partial earlier failure) never
blocks the rest of the teardown — important because the reconciler calls this
to clean up orphans.

## Stop / start / reboot (`vm_stop`, `vm_start`)

**stop** keeps everything except the firecracker process (netns, tap, veth,
bridge, volume, IP, the `config.json`, and the DB row are all preserved), so
**start** is a cheap respawn — `vm.create` (inside the still-existing netns)
then `vm.boot`, reading the on-disk config. No `VmSpec`, no network re-wiring.
The router resolves `ns = vm-<vm_id[:8]>` and `firecracker_bin` (from
`config.resolve`) and passes them in, keeping the client layer pure. **reboot**
is just stop+start. A `stopped` VM keeps its dir, so the reconciler (which only
reaps dirs with no DB row) leaves it alone — it is not an orphan.
