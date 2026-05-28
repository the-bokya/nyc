# client/network

All Linux networking primitives a VM needs: VPC bridges, the VXLAN overlay,
NAT/internet egress, network namespaces, veth pairs, in-netns bridges, taps,
and CIDR/IP math. Every mutating action goes through `privops.run`, so `fake`
records intent and `real` runs `ip`/`bridge`/`iptables`/`sysctl`.

**For the ground-up concepts** (what a netns/veth/bridge/tap/VXLAN/FDB/anycast
gateway/NAT is and why), read `../../../NETWORKING.md`. This file is the
per-module contract only.

## The topology being built

One VPC = one bridge per node, joined across nodes by a per-VPC VXLAN tunnel.
One VM = one netns wired to that bridge through a veth pair, with `tap0`
(firecracker's NIC) bridged to the veth inside the netns. The bridge is an
**anycast gateway**: same IP + MAC on every node, so each VM egresses to the
internet via its local node. (`client/lifecycle/vm_up` composes these in order.)

```
host:           br-<node4>-<vpc4>   bridge, anycast gateway IP + MAC
host:           vx-<node4>-<vpc4>   VXLAN VTEP (id=vni, local=node IP), enslaved to the bridge
host:           vmh-<vm8>           veth host side, joined to the VPC bridge
netns vm-<vm8>: vmn-<vm8>           veth ns side
netns vm-<vm8>: nbr0                bridge joining vmn-<vm8> and tap0
netns vm-<vm8>: tap0                firecracker NIC, no IP
guest:          eth0                configured via kernel ip= boot arg (incl. dns)
host (per VPC): iptables NYC-* + net.ipv4.ip_forward=1  → NAT to the internet
```

## Actions

| File | Public fns | Does |
|---|---|---|
| `allocate.py` | `pick_ip(cidr, used)`, `gateway(cidr)`, `netmask(cidr)`, `gateway_cidr(cidr)` | CIDR math. `gateway` is the first host; `pick_ip` returns the first free non-gateway host or raises if the VPC is full. Pure, no privops. |
| `overlay.py` | `vni_for(vpc_id)`, `anycast_mac(vpc_id)` | Deterministic per-VPC VXLAN id (`[1,2²⁴)`) and shared gateway MAC. Pure — every node derives the same values, no coordination. |
| `namespace.py` | `create`, `delete`, `exists`, `list_all` | `ip netns` lifecycle. |
| `veth.py` | `create_pair`, `place_in_ns`, `up`, `delete` | veth pair lifecycle (one pair per VM). |
| `bridge.py` | `name_for(node,vpc)`, `ensure(br, ip_cidr, mac=None)`, `delete`, `attach(br, veth)`, `exists` | Per-VPC host bridge. `mac=` pins the anycast gateway MAC. |
| `vxlan.py` | `name_for(node,vpc)`, `ensure(name, vni, local_ip, bridge)`, `set_fdb(name, peers)`, `delete`, `exists` | Per-VPC VXLAN VTEP + head-end FDB (unicast replication to each peer, no multicast). `set_fdb` reconciles the flood list to exactly `peers`. |
| `nat.py` | `ensure(cidr)`, `delete(cidr)` | `ip_forward` + `NYC-POSTROUTING`/`NYC-FORWARD` masquerade & accept rules. Idempotent via `iptables -C`/`-nL`. Intra-VPC traffic (`! -d cidr`) is not NAT'd. |
| `ns_bridge.py` | `create(ns)`, `attach(ns, link)` | `nbr0` bridge *inside* a netns, joining veth + tap0. |
| `tap.py` | `create(ns, name)`, `delete(ns, name)` | `tap0` inside a netns. No IP — passthrough for firecracker's vhost. |

The peer list and the node's own underlay IP come from the dadar `nodes`
registry; the **app layer** (`nyc.peers`, used by the vms router and the
reconciler) resolves them and passes plain values in, keeping `client/` pure.

## Naming rules (IFNAMSIZ = 15 chars)

Interface names are kept ≤ 15 chars on purpose:

- Bridge: `br-<node[:4]>-<vpc[:4]>` (12 chars); VXLAN: `vx-<node[:4]>-<vpc[:4]>` —
  two nodes on one host never collide on the same VPC.
- veth: `vmh-<vm[:8]>` / `vmn-<vm[:8]>`.
- In-netns bridge is always `nbr0`; the netns gives it its own namespace, so a
  fixed name is safe.

## Teardown ordering

`vm_down` deletes the **netns first**: the kernel then auto-removes the
ns-side veth peer, `nbr0`, and `tap0`. Only the host-side veth needs an
explicit `veth.delete`. Don't reorder this. Per-VPC infra (bridge, VXLAN, NAT
rules) is shared and outlives individual VMs — it is removed at environment
teardown, not per-VM.

## Backend notes

`bridge.exists`/`vxlan.exists` check `STATE["links"]` on `fake` and probe `ip`
on `real`. `nat` uses `iptables -C`/`-nL` for idempotency: on `real` a missing
rule/chain exits non-zero (raising `PrivopsError`); the `fake` backend models
chains/rules in `STATE["iptables"]` and raises the same error on a miss, so the
client's check-then-add logic is identical on both. VXLAN FDB lives in
`STATE["fdb"]`.
