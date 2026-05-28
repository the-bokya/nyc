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

## `VmSpec` (input to `vm_up`)

Frozen dataclass: `vm_id`, `node_id`, `vpc_id`, `ip`, `cidr`,
`data_volume_path: Path | None`, `assets: dict`, `vms_dir: Path`,
`firecracker_bin: Path`. The caller (router) resolves the IP via
`network.allocate.pick_ip` and the volume path before constructing this.

## Bring-up order (`vm_up`)

1. `env.setup` — make the VM dir and symlink rootfs/kernel/ssh-key.
2. If `data_volume_path`: `volume.attach` it as `data.ext4`.
3. `_network`: ensure the VPC bridge (with gateway IP) → create netns →
   veth pair (host side joins the VPC bridge) → in-netns `nbr0` + `tap0`.
4. `_spawn`: build `VmConfig` → `config.build` → `vm.create` (inside the
   netns) → `vm.boot`.

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
