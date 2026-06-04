# client/pubip

Public IP pool and per-VM L2 wiring.

## Model

Each public IP is a **pool entry** declared in `cluster.toml` as an array of inline
tables. Every entry carries:
- `address` — the flexible/elastic IP
- `mac` — the **provider-registered MAC** for that IP (mandatory: the provider's
  L2 anti-spoof drops frames from unregistered MACs)
- `prefix` — optional, defaults to `"32"`

The operator pre-attaches the IPs to the bare-metal server at the provider; nyc
never calls provider APIs to order or attach them.

## File

| File | Purpose |
|---|---|
| `pool.py` | `acquire(cfg, used) -> (address, gateway, mac, prefix)` picks the first free pool entry not in `used`. `release` is a no-op. |

## Wiring

When a VM gets a public IP it boots with a **second NIC** (`eth1`). The wiring is
done entirely inside `client/lifecycle/vm_up._wire_public`:

```
host bridge pub0  (created once at provision over public_iface)
     │ enslaved
  pvh-<vm>  (veth host side)
     │ peer
  pvn-<vm> ── pbr1 ── tap1  (all inside netns vm-<vm>)
                      │
              guest eth1  (public IP, registered MAC via guest_mac)
```

The MAC is set on the Firecracker side via `guest_mac` in the second
`network-interfaces` entry. The guest IP is configured by the injected
`nyc-pubip.service` (a oneshot systemd unit written to the rootfs by
`client/vm/inject._pubip_unit`). The service does:

```sh
ip link set eth1 up
ip addr add <ip>/<prefix> dev eth1
ip route add <gw> dev eth1
ip route add default via <gw> dev eth1 table 100
ip rule add from <ip> table 100
sysctl -w net.ipv4.conf.all.rp_filter=2   # loose: asymmetric path
```

Policy routing ensures replies from the public IP exit eth1 while the
VM's self-initiated traffic (DNS, ACME, etc.) still uses eth0 → host NAT.

## Host bridge `pub0`

`pub0` is created **once at provision** by `scripts/provision.py` using a
netplan template (`templates/pub-bridge.yaml.j2`). Moving the host public IP
onto `pub0` is the riskiest step; the guard `ip link show pub0` makes
re-provision a no-op. The nyc runtime never touches `pub0` itself — it only
creates per-VM `pvh-*` veths and enslaves them to the pre-existing bridge.

## Config

`nyc.config.PubipConfig` holds `iface`, `ips` (`list[PubIpEntry]`), `gateway`,
`bridge`. Read from `config.toml` (written by `scripts/provision.py`) with env
overrides:
- `NYC_PUBLIC_IFACE` — physical public NIC
- `NYC_PUBLIC_IPS` — `addr|mac,addr|mac` CSV
- `NYC_PUBIP_GATEWAY` — default gateway for the public segment
- `NYC_PUBLIC_BRIDGE` — bridge name (default `pub0`)

## Attach / detach lifecycle

- **`POST /vms/{id}/public-ip`**: calls `pool.acquire`, inserts `PublicIps` row,
  then **recreates** the VM (`vm_down` + `vm_up`) so it reboots with `eth1`.
  Document this reboot cost to callers.
- **`DELETE /vms/{id}/public-ip`**: deletes `PublicIps` row, recreates VM without
  `eth1`.
- **`POST /proxy`**: acquires IP+MAC **before** spawning the VM and inserts the
  `PublicIps` row before `_bring_up` runs (via `pre_bring_up` callback), so the
  proxy VM boots with `eth1` directly — no recreate.

## Teardown

`vm_down._network_down` deletes `pvh-<vm>` after the netns is gone (the netns
deletion reaps `pvn-<vm>`, `pbr1`, `tap1` automatically). No host NAT rules to
clean up — there are none.

## Tests

`tests/test_pubip_client.py` — pool unit tests + wiring assertions via fake backend.
`tests/test_public_ip_router.py` — HTTP API tests asserting the PublicIps row,
pvh-* master=pub0, eth1 in config.json, and injected pubip service.
