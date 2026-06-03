# client/pubip

Public IP host wiring and backend dispatch.

## Files

| File | Purpose |
|---|---|
| `host.py`              | `bind(address, iface)` / `unbind(address, iface)` — add/remove `<address>/32` on the host interface via `ip addr`. Idempotent: guards add with `ip addr show`. |
| `nat.py`               | 1:1 DNAT/SNAT: `attach(public_ip, vm_ip)` installs a DNAT rule in `NYC-PREROUTING` (internet → vm) and inserts a SNAT rule at position 1 in `NYC-POSTROUTING` (vm egresses on its public IP, before the general MASQUERADE). `detach` removes both. Same `_ensure_rule`/`_rule_exists` idempotency helpers as `network/nat.py`. |
| `backend.py`           | Dispatches `acquire(cfg, used)` / `release(cfg, address)` to the appropriate backend module. |
| `backends/scaleway.py` | `acquire` picks a free address from `cfg.addresses` (the pool declared in `cluster.toml`). `release` is a no-op (IP stays on the server at Scaleway). Seam for optional `flexible-ip/v1alpha1` API ordering left as `# TODO`. |
| `backends/static.py`   | Same pool semantics as Scaleway, `provider='static'`. |

## DNAT/SNAT ordering

The SNAT rule for each VM is **inserted at position 1** in `NYC-POSTROUTING`
(via `iptables -I`) so it precedes the general MASQUERADE rule added by
`network/nat.py`. This ensures the VM's return traffic exits on its dedicated
public IP rather than the node's default source address.

## Config

`nyc.config.PubipConfig` holds `provider`, `iface`, `addresses`, `gateway`.
Read from `config.toml` (written by `scripts/provision.py`) with env overrides
`NYC_PUBIP_PROVIDER`, `NYC_PUBLIC_IFACE`, `NYC_PUBLIC_IPS`, `NYC_PUBIP_GATEWAY`.

## Idempotency in the fake backend

`ip addr show dev <iface>` is implemented in `privops_fake._addr` (returns the
addresses in `STATE["addrs"][iface]`). `iptables -I` is handled by
`_ipt_insert`, which prepends the rule to the fake chain. Tests can assert
rule presence and ordering via `STATE["iptables"]`.
