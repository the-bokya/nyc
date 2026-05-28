# client/network

All Linux networking primitives a VM needs: VPC bridges, network namespaces,
veth pairs, in-netns bridges, taps, and CIDR/IP math. Every mutating action
goes through `privops.run`, so `fake` records intent and `real` runs `ip`.

## The topology being built

One VPC = one bridge per node. One VM = one netns wired to that bridge through
a veth pair, with `tap0` (firecracker's NIC) bridged to the veth inside the
netns. (`client/lifecycle/vm_up` composes these in order.)

```
host:           br-<node4>-<vpc4>   bridge, holds the VPC gateway IP
host:           vmh-<vm8>           veth host side, joined to the VPC bridge
netns vm-<vm8>: vmn-<vm8>           veth ns side
netns vm-<vm8>: nbr0                bridge joining vmn-<vm8> and tap0
netns vm-<vm8>: tap0                firecracker NIC, no IP
guest:          eth0                configured via kernel ip= boot arg
```

## Actions

| File | Public fns | Does |
|---|---|---|
| `allocate.py` | `pick_ip(cidr, used)`, `gateway(cidr)`, `netmask(cidr)`, `gateway_cidr(cidr)` | CIDR math. `gateway` is the first host; `pick_ip` returns the first free non-gateway host or raises if the VPC is full. Pure, no privops. |
| `namespace.py` | `create`, `delete`, `exists`, `list_all` | `ip netns` lifecycle. |
| `veth.py` | `create_pair`, `place_in_ns`, `up`, `delete` | veth pair lifecycle (one pair per VM). |
| `bridge.py` | `name_for(node,vpc)`, `ensure(br, ip_cidr)`, `delete`, `attach(br, veth)`, `exists` | Per-VPC host bridge. |
| `ns_bridge.py` | `create(ns)`, `attach(ns, link)` | `nbr0` bridge *inside* a netns, joining veth + tap0. |
| `tap.py` | `create(ns, name)`, `delete(ns, name)` | `tap0` inside a netns. No IP — passthrough for firecracker's vhost. |

## Naming rules (IFNAMSIZ = 15 chars)

Interface names are kept ≤ 15 chars on purpose:

- Bridge: `br-<node[:4]>-<vpc[:4]>` (12 chars) — two nodes on one host never
  collide on the same VPC.
- veth: `vmh-<vm[:8]>` / `vmn-<vm[:8]>`.
- In-netns bridge is always `nbr0`; the netns gives it its own namespace, so a
  fixed name is safe.

## Teardown ordering

`vm_down` deletes the **netns first**: the kernel then auto-removes the
ns-side veth peer, `nbr0`, and `tap0`. Only the host-side veth needs an
explicit `veth.delete`. Don't reorder this.

## Backend notes

`bridge.exists` checks `STATE["bridges"]`/`STATE["links"]` on `fake` and parses
`ip -o link show type bridge` on `real`. Everything else is fire-and-forget
through `privops.run`.
