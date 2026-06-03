# nyc networking — architectural reference

This document explains, from the ground up, the Linux networking that makes a
`nyc` VPC work: how one microVM gets an isolated NIC, how many VMs on one host
share a private L2 segment, how that segment is stretched across physical hosts
with VXLAN, and how VMs reach the internet. It is **concept-first** — it
explains *what each construct is and why we use it*, not the specifics of our
codebase. Every `ip` / `bridge` / `iptables` / `sysctl` command we issue is
explained here.

If you have never built a software network by hand, read top to bottom. Each
section adds exactly one new idea.

**Contents:** §0 the goal · §1 primitives (netns · veth · bridge · tap · guest
IP) · §2 one VM on one host · §3 VXLAN across hosts (+ the FDB) · §4 the anycast
gateway · §5 internet (forwarding + NAT) · §6 guest DNS · §7 naming & limits ·
§8 teardown order · §9 command reference. The exact names/regexes and the
deterministic VNI/MAC functions live in code (`client/network/`,
`scripts/teardown.py`); this doc is the *why*.

---

## 0. The goal

A **VPC** is a private virtual network. We want:

1. Each microVM to have its own NIC, isolated from the host and other VMs.
2. All VMs in the same VPC — even on different physical machines — to behave as
   if plugged into one Ethernet switch (one **L2 broadcast domain**), addressed
   from one CIDR (e.g. `172.16.0.0/16`).
3. Each VM to reach the public internet.

Linux already contains every switch, cable, and router we need, in software. We
just have to wire them.

---

## 1. The primitives

### 1.1 Network namespace — "a separate networking world"

A **network namespace** (netns) is an isolated copy of the kernel's networking
stack: its own interfaces, routing table, ARP table, firewall. Interfaces in
one netns cannot see another's. The host boots in the *root* namespace.

```
ip netns add  vm-1a2b3c4d      # create an empty networking world
ip netns del  vm-1a2b3c4d      # destroy it (and everything inside it)
ip netns exec vm-1a2b3c4d CMD  # run CMD inside that world
```

We give **each VM its own netns**. That is the isolation boundary: a VM's tap
device, its bridge, its routes all live inside `vm-<id>` and cannot collide with
or snoop on anything outside.

### 1.2 veth pair — "a virtual patch cable"

A **veth** is a pair of interfaces joined like the two ends of a cable: a frame
that enters one end exits the other. The two ends can live in *different*
namespaces, so a veth is how you connect a netns to the outside.

```
ip link add  vmh-1a2b3c4d type veth peer name vmn-1a2b3c4d  # create the pair
ip link set  vmn-1a2b3c4d netns vm-1a2b3c4d                 # push one end into the VM's netns
ip link set  vmh-1a2b3c4d up                                # bring the host end up
```

`vmh-*` ("host") stays in the root namespace; `vmn-*` ("ns") is moved inside the
VM's netns. Now there is a cable from the host into the VM's isolated world.

### 1.3 Bridge — "a virtual Ethernet switch"

A **Linux bridge** is a software switch. Interfaces *enslaved* to it ("ports")
are switched together at L2: the bridge learns which MAC lives on which port and
forwards frames accordingly; unknown/broadcast frames are flooded to all ports.
A bridge can also carry an IP of its own, making it a host on that segment.

```
ip link add  br0 type bridge          # create the switch
ip link set  br0 address 02:..:..     # (optional) pin its MAC — see anycast gateway
ip addr add  172.16.0.1/16 dev br0    # give the switch an IP (acts as gateway)
ip link set  br0 up
ip link set  vmh-1a2b3c4d master br0  # plug a cable end into a switch port
```

We use **one bridge per VPC per host**. Its IP is the VPC **gateway** (the first
usable address of the CIDR). Every VM's host-side veth is a port on it.

### 1.4 TAP — "a virtual NIC a userspace program owns"

A **tap** device looks like an Ethernet NIC to the kernel, but its other side is
a file descriptor held by a userspace process. Whatever that process writes
appears as a received frame; whatever the kernel transmits, the process reads.
**Firecracker** opens a tap and presents it to the guest as `eth0`.

```
ip netns exec vm-1a2b3c4d ip tuntap add dev tap0 mode tap   # create tap0 in the VM's netns
```

The tap has **no IP** on the host side — it is a pure L2 passthrough. The *guest*
puts an IP on its `eth0`.

### 1.5 The guest's IP — set by the kernel at boot, no DHCP

Firecracker boots the guest with a kernel command line. The `ip=` parameter
configures `eth0` before userspace even starts, so there is no DHCP server to
run:

```
ip=<guest-ip>::<gateway>:<netmask>::eth0:off:<dns>
   └ 172.16.0.2   └172.16.0.1 └255.255.0.0    └ default route via gateway
```

The third field is the gateway → the kernel installs a **default route** via it.
The trailing field seeds a DNS server. (We *also* bake `/etc/resolv.conf` into
the image, because some guests ignore the kernel's DNS hint — see §6.)

---

## 2. One VM, one host

Putting §1 together for a single VM. There is a subtlety: the tap must end up on
the **same L2 segment as the host bridge**, but the tap lives *inside* the VM's
netns while the bridge lives in the root namespace. You cannot enslave a tap in
one netns to a bridge in another. So we use a **second, tiny bridge inside the
netns** (`nbr0`) to join the ns-side veth and the tap:

```
 root namespace                         netns vm-1a2b3c4d
 ┌───────────────────────┐              ┌──────────────────────────────┐
 │  br-<node>-<vpc>       │              │   nbr0 (bridge)              │
 │  ip 172.16.0.1/16      │              │    ├── vmn-1a2b3c4d (veth)   │
 │   │                    │   veth pair  │    └── tap0 ──── firecracker │
 │   └ vmh-1a2b3c4d ──────┼──────────────┼──→ vmn-1a2b3c4d              │
 └───────────────────────┘              └──────────────────────────────┘
                                                      │ tap0 ↔ guest eth0
                                                      ▼
                                         guest: eth0 = 172.16.0.2/16, gw .1
```

Frame path, guest → gateway: `eth0 → tap0 → nbr0 → vmn → (cable) → vmh → VPC
bridge`, where the bridge (172.16.0.1) terminates it. The whole VM-side is
sealed inside the netns; only the one veth cable crosses out.

**Many VMs, same VPC, same host:** each gets its own netns + veth + `nbr0` +
`tap0`, and every host-side veth is plugged into the *same* VPC bridge. They are
now on one switch → they talk directly at L2. Different VPCs get different
bridges and never mix.

---

## 3. Stretching the VPC across hosts — VXLAN

So far a VPC bridge lives on one host. Two VMs of the same VPC on **different**
hosts sit on two unconnected bridges. We need to connect those bridges over the
physical ("underlay") network — here the private `10.1.0.0/24` LAN.

### 3.1 What VXLAN is

**VXLAN** (Virtual eXtensible LAN) tunnels Ethernet frames inside UDP packets.
A VM's L2 frame is wrapped — `[outer IP/UDP][VXLAN header][original L2 frame]` —
sent across the underlay to another host, unwrapped, and injected onto that
host's bridge. The two bridges now behave as one switch. This is an **overlay**
network (the virtual L2) riding on an **underlay** (the physical L3 LAN).

Key terms:

- **VTEP** (VXLAN Tunnel EndPoint): the device that wraps/unwraps. On Linux it's
  a `vxlan` interface. Each host has one per VPC, enslaved to that VPC's bridge.
- **VNI** (VXLAN Network Identifier, 24-bit): the tunnel's "VLAN tag". All hosts
  must use the **same VNI** for the same VPC, or the segments won't join. We
  derive it deterministically from the VPC id so every host agrees with no
  coordination.
- **dstport 4789**: the standard VXLAN UDP port.
- **local `<underlay-ip>`**: the source address this VTEP uses for the outer
  packet — the host's private LAN IP (`10.1.0.x`).

```
ip link add vx-<node>-<vpc> type vxlan id <vni> dstport 4789 local 10.1.0.14
ip link set vx-<node>-<vpc> master br-<node>-<vpc>   # enslave to the VPC bridge
ip link set vx-<node>-<vpc> up
```

Now the VPC bridge has two kinds of ports: local VM veths, and the VXLAN tunnel
to the rest of the cluster.

### 3.2 How a bridge decides where to send a frame: the FDB

A bridge keeps a **forwarding database** (FDB): `MAC → port`. For a VXLAN port,
the FDB entry also needs the **remote underlay IP** to tunnel to (`MAC → vxlan
dev, dst <underlay-ip>`). Two problems:

1. **Where do unicast entries come from?** Either *learning* (the bridge records
   the source MAC + arriving VTEP of received frames) or a control plane fills
   them in. We rely on learning (default on): once VM-a hears from VM-b, it knows
   VM-b's MAC sits behind VM-b's host VTEP.
2. **What about traffic with no known destination?** Broadcasts (ARP), multicast,
   and not-yet-learned unicast — collectively **BUM** traffic — must reach *every*
   host in the VPC. Two ways to flood it:
   - **Multicast underlay:** all VTEPs join an IP multicast group; BUM goes to the
     group. Simple, but needs multicast support on the LAN — often unavailable.
   - **Head-end replication (what we use):** the sender unicasts a *copy* to each
     peer VTEP. No multicast needed. You tell the kernel the peer list with a
     special "all-zeros" FDB entry per peer:

```
bridge fdb append 00:00:00:00:00:00 dev vx-<node>-<vpc> dst 10.1.0.15
bridge fdb append 00:00:00:00:00:00 dev vx-<node>-<vpc> dst 10.1.0.2
```

The all-zeros MAC means "the default flood list." Each `append` adds one peer.
BUM frames are replicated to every listed peer; the host owning the destination
MAC delivers it locally, the rest ignore it. As the cluster's node set changes,
we re-reconcile this list (add/remove `dst` entries) so it always matches the
live peers.

`bridge fdb show dev <vx>` lists current entries; `bridge fdb del <mac> dev <vx>
dst <ip>` removes one.

---

## 4. The gateway across an overlay — the anycast trick

Each VM's default route points at the VPC gateway (`172.16.0.1`). On a single
host, the bridge owns that IP and routes/NATs the VM's internet traffic. With
the overlay, the gateway IP would have to exist on **every** host's bridge (so a
VM egresses via its *local* host, not by trombone-ing to one central node). But
the same IP on multiple bridges that are L2-joined by VXLAN is, naively, a
duplicate-address conflict.

The fix is an **anycast gateway**: every host's VPC bridge carries the **same IP
*and* the same MAC** (we derive the MAC deterministically from the VPC id, so all
hosts agree). Why this is safe:

- A Linux bridge **consumes frames addressed to its own MAC locally** — it hands
  them up to the host's IP stack and never floods them out a port. So a VM's
  gateway-bound frame is always terminated by its **local** bridge and never
  crosses the VXLAN. Internet egress is therefore always local and distributed.
- Because gateway-bound frames never traverse the tunnel, the duplicate IP/MAC
  never actually meet on the wire. The only redundancy is that a broadcast ARP
  "who has 172.16.0.1?" floods to peers, and a remote bridge *also* answers —
  but with the *same* MAC, so the guest gets identical answers. Harmless.

> Future optimization: enabling ARP/ND suppression on the VXLAN
> (`bridge link set dev <vx> neigh_suppress on`) plus per-VM neighbor entries
> would stop that ARP from crossing the tunnel at all. Not required for
> correctness at small scale; documented here for completeness.

VM-to-VM traffic (same subnet, different hosts) does **not** involve the gateway
— it is pure L2 over the VXLAN, using each VM's own unique MAC.

---

## 5. Internet access — forwarding + NAT

A VM's packet to `1.1.1.1` arrives at its local bridge/gateway. Two things must
happen on the host for it to reach the internet and come back.

### 5.1 Enable routing

By default Linux does not forward packets between interfaces. Turn it on:

```
sysctl -w net.ipv4.ip_forward=1
```

(Persisted via a file in `/etc/sysctl.d/` so it survives reboots.)

### 5.2 Masquerade (source NAT)

VM addresses (`172.16.0.0/16`) are private and unroutable on the internet.
**Masquerading** rewrites the packet's source to the host's own public address
on the way out, and reverses it for replies (the kernel's **conntrack** tracks
each connection so return traffic is un-NAT'd back to the VM automatically).

iptables has tables (`nat`, `filter`) containing chains of rules. We keep our
rules in **dedicated chains** (`NYC-POSTROUTING`, `NYC-FORWARD`) so teardown can
remove them cleanly without touching anything else on the box:

```
iptables -t nat    -N NYC-POSTROUTING                 # create our chain
iptables -t nat    -A POSTROUTING -j NYC-POSTROUTING  # send POSTROUTING traffic through it
iptables -t nat    -A NYC-POSTROUTING -s 172.16.0.0/16 ! -d 172.16.0.0/16 -j MASQUERADE
```

`POSTROUTING` is the nat chain traversed just before a packet leaves. The rule
reads: "for packets **from** the VPC but **not to** the VPC, masquerade." The
`! -d` exclusion is important — it means **intra-VPC traffic is never NAT'd**, so
VMs see each other's real addresses; only internet-bound traffic is rewritten.

### 5.3 Allow forwarding through the filter

If the host's `FORWARD` policy is `DROP` (common when Docker/ufw is present),
forwarded packets are dropped unless explicitly accepted. We accept traffic to
and from the VPC:

```
iptables -N NYC-FORWARD
iptables -A FORWARD -j NYC-FORWARD
iptables -A NYC-FORWARD -s 172.16.0.0/16 -j ACCEPT   # VM → out
iptables -A NYC-FORWARD -d 172.16.0.0/16 -j ACCEPT   # replies → VM (dst restored by conntrack)
```

### 5.4 Idempotency

`iptables -A` always appends, so re-running would create duplicates. Before
adding, we check with `iptables -C` (which exits non-zero if the rule is absent)
and only append when missing. Chains are likewise created only if `iptables -nL
<chain>` shows they don't exist. Teardown flushes (`-F`) and deletes (`-X`) the
`NYC-*` chains and removes the jumps.

---

## 6. DNS inside the guest

Routing gets packets to `1.1.1.1`, but the guest still needs to turn names into
addresses. Two complementary measures:

1. The kernel `ip=` boot arg's trailing field seeds a resolver at init.
2. We **bake `/etc/resolv.conf`** (`nameserver 1.1.1.1`) into the rootfs image
   offline with `debugfs` (no mount, no boot-time agent), because some images
   ignore the kernel hint or symlink `resolv.conf` to a systemd-resolved stub
   that isn't running.

---

## 7. Naming and limits

Linux caps interface names at **15 characters** (`IFNAMSIZ`). All names are
derived from short id prefixes to stay within that and to be globally unique
(two emulated nodes on one host must not collide):

| Interface | Pattern | Example |
|---|---|---|
| VPC bridge (root ns) | `br-<node[:4]>-<vpc[:4]>` | `br-1a2b-9f8e` |
| VXLAN VTEP (root ns) | `vx-<node[:4]>-<vpc[:4]>` | `vx-1a2b-9f8e` |
| veth host / ns side | `vmh-<vm[:8]>` / `vmn-<vm[:8]>` | `vmh-1a2b3c4d` |
| VM netns | `vm-<vm[:8]>` | `vm-1a2b3c4d` |
| in-netns bridge | `nbr0` (fixed; the netns is its own namespace) | `nbr0` |

---

## 8. Teardown ordering and why

Deleting a netns makes the kernel **automatically** remove everything inside it
(the ns-side veth peer, `nbr0`, `tap0`). So per-VM teardown only needs:

```
ip netns del vm-<id>        # kernel reaps vmn-*, nbr0, tap0
ip link  del vmh-<id>       # then drop the now-orphaned host-side veth
```

Per-VPC infrastructure (the bridge, the VXLAN device, the NAT rules) is shared
by all VMs of that VPC and is **not** removed on per-VM teardown — only when the
environment is decommissioned. Bulk teardown matches resources by the exact
anchored regexes of the patterns in §7 (e.g. `^br-[0-9a-f]{4}-[0-9a-f]{4}$`) so
it never touches unrelated bridges like `docker0`, `virbr0`, or a user's `br0`.

---

## 9. Full command reference

| Command | Layer | Meaning |
|---|---|---|
| `ip netns add/del/exec NAME` | namespace | create / destroy / run-inside an isolated network world |
| `ip link add A type veth peer name B` | veth | create a virtual cable A↔B |
| `ip link set B netns NS` | veth | move one cable end into a namespace |
| `ip link add NAME type bridge` | bridge | create a software switch |
| `ip link set BR address MAC` | bridge | pin the switch's MAC (anycast gateway) |
| `ip addr add IP/PFX dev BR` | bridge | give the switch an IP (gateway) |
| `ip link set X master BR` | bridge | plug interface X into switch BR |
| `ip tuntap add dev tap0 mode tap` | tap | create a userspace-owned virtual NIC |
| `ip link add NAME type vxlan id VNI dstport 4789 local IP` | vxlan | create a tunnel endpoint for VNI sourced at IP |
| `bridge fdb append 00:..:00 dev VX dst PEER` | vxlan | add a peer to the BUM flood list (head-end replication) |
| `bridge fdb show dev VX` / `del ... dst PEER` | vxlan | list / remove flood-list entries |
| `sysctl -w net.ipv4.ip_forward=1` | routing | allow the host to forward packets between interfaces |
| `iptables -t nat -N/-A POSTROUTING ... -j MASQUERADE` | NAT | rewrite VM source addr to the host's on egress |
| `iptables -N/-A FORWARD ... -j ACCEPT` | filter | permit forwarded VPC traffic when the policy is DROP |
| `iptables -C ...` | (idempotency) | test whether a rule already exists |
| `iptables -F / -X CHAIN` | (teardown) | flush rules / delete a chain |
| `ip=<ip>::<gw>:<mask>::eth0:off:<dns>` (kernel cmdline) | guest | configure guest eth0 + default route + DNS at boot, no DHCP |

---

### Summary of the data path

```
guest eth0 ─ tap0 ─ nbr0 ─ vmn ═(veth)═ vmh ─┬─ VPC bridge (gateway: anycast IP+MAC)
                                              │      │
                                  same-VPC,   │      └─ vx-* ═(VXLAN/UDP 4789 over 10.1.0.0/24)═ other hosts' bridges
                                  remote VM ──┘             (head-end replicated BUM, learned unicast)
                                              │
                            internet ◀── MASQUERADE ◀── ip_forward ◀── gateway (local termination)
```

---

## Public IP: 1:1 DNAT/SNAT

A public IP can be attached to any VM (most usefully the proxy VM) via
`POST /vms/{id}/public-ip`. nyc wires it at the host level:

1. **Host bind**: `ip addr add <public_ip>/32 dev <iface>` — the host's network
   interface starts responding to ARP for the IP (Scaleway flexible IP is
   pre-attached to the server; nyc only binds the local alias).
2. **PREROUTING DNAT** (in `NYC-PREROUTING` chain): internet traffic arriving on
   `<public_ip>` is rewritten to `<vm_ip>` before routing, so the kernel routes
   it into the VPC bridge toward the VM.
3. **POSTROUTING SNAT** (inserted at position 1 in `NYC-POSTROUTING` chain):
   return traffic from `<vm_ip>` is rewritten to `<public_ip>` before leaving the
   host — the VM egresses on its dedicated public IP, not the node's shared source.
   This rule is inserted **before** the general MASQUERADE rule so it wins.

All rules are idempotent (`iptables -C` guard) and re-applied after every reboot
by `pubip_pass` in the reconciler.

```
internet ──► <public_ip>:80
               │  PREROUTING DNAT → <vm_ip>:80
               ▼
           VPC bridge → tap → VM eth0
               │
               └─ return: POSTROUTING SNAT <vm_ip> → <public_ip> ──► internet
```

The pool of public IPs available for a node is declared in `cluster.toml`
(`public_ips = [...]`, `public_iface`, `pubip_gateway`). IPs must already be
attached to the Elastic Metal server at the provider (Scaleway flexible IPs).
nyc does not call the Scaleway API to order or attach IPs.
