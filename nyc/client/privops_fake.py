"""In-memory privileged-op simulator for tests.

Mirrors the surface of `ip`, `mkfs.ext4`, `mount`, and `firecracker` closely
enough that the real client code paths don't change. State lives in `STATE`
and is reset per test via `reset_state()`.

The point isn't fidelity to the kernel — it's that the client's argv shape is
exercised end to end. The reconciler asks `ip netns list`; the fake answers
with the netnses we've created. That's the contract.
"""
from __future__ import annotations

from typing import Any


class PrivopsError(RuntimeError):
    """Raised on a non-zero privileged op. Defined here (not in privops.py) so
    the fake backend can raise the same type real `sudo` failures produce —
    e.g. `iptables -C`/`-nL` misses, which the client uses for idempotency."""


STATE: dict[str, Any] = {}

# Built-in chains per iptables table — seeded so chain-existence checks behave.
_IPT_BUILTIN = {
    "filter": {"INPUT", "FORWARD", "OUTPUT"},
    "nat":    {"PREROUTING", "INPUT", "OUTPUT", "POSTROUTING"},
}


def reset_state() -> None:
    STATE.clear()
    STATE.update({
        "netns":     set(),       # {netns_name}
        "links":     {},          # link_name -> {"netns": str|None, "kind": str, "peer": str|None}
        "addrs":     {},          # link_name -> [ip/prefix]
        "routes":    [],          # list of dicts
        "bridges":   set(),       # {bridge_name}
        "files":     {},          # path -> size_bytes
        "mounts":    {},          # mountpoint -> source
        "fc_socks":  {},          # api_sock_path -> {"vm_dir": str, "running": bool}
        "sysctl":    {},          # key -> value
        "fdb":       {},          # dev -> {(mac, dst)}
        "copies":    [],          # [(source, dest)] from `cp` (per-VM rootfs clone)
        "debugfs":   [],          # [argv] from `debugfs` (offline rootfs edits)
        "iptables":  {t: {"chains": set(c), "builtin": set(c), "rules": {}}
                      for t, c in _IPT_BUILTIN.items()},
    })


reset_state()


def fake_run(argv: list[str], input: str | None) -> str:
    head = argv[0] if argv else ""
    handler = _HANDLERS.get(head)
    if handler is None:
        return ""
    return handler(argv, input)


def _ip(argv, _input):
    # argv = ["ip", "netns", "add", "ns1"]  etc.
    if len(argv) < 2:
        return ""
    sub = argv[1]
    fn = _IP_SUB.get(sub)
    return fn(argv, _input) if fn else ""


def _netns(argv, _input):
    op = argv[2] if len(argv) > 2 else "list"
    name = argv[3] if len(argv) > 3 else None
    if op == "add":
        STATE["netns"].add(name)
    elif op == "del" or op == "delete":
        STATE["netns"].discard(name)
        for link, meta in list(STATE["links"].items()):
            if meta.get("netns") == name:
                STATE["links"].pop(link, None)
                STATE["addrs"].pop(link, None)
    elif op == "list":
        return "\n".join(sorted(STATE["netns"])) + ("\n" if STATE["netns"] else "")
    elif op == "exec":
        return _netns_exec(argv)
    return ""


def _netns_exec(argv):
    # ["ip", "netns", "exec", ns, ...rest] — rest runs "inside" the ns. For
    # the fake, just dispatch the nested command. Real backend prepends
    # `ip netns exec ns` and the kernel handles isolation.
    rest = argv[4:]
    return fake_run(list(rest), None) if rest else ""


def _link(argv, _input):
    # ["ip", "link", "add", name, "type", kind, ...] or "set", "del"
    op = argv[2]
    if op == "add":
        name = argv[3]
        kind = argv[argv.index("type") + 1] if "type" in argv else "veth"
        peer = argv[argv.index("peer") + 2] if "peer" in argv else None
        STATE["links"][name] = {"netns": None, "kind": kind, "peer": peer}
        if peer:
            STATE["links"][peer] = {"netns": None, "kind": "veth", "peer": name}
    elif op == "set":
        name = argv[3]
        link = STATE["links"].setdefault(name, {"netns": None, "kind": "tap", "peer": None})
        if "netns" in argv:
            link["netns"] = argv[argv.index("netns") + 1]
        if "master" in argv:
            link["master"] = argv[argv.index("master") + 1]
        if "address" in argv:
            link["address"] = argv[argv.index("address") + 1]
    elif op == "del" or op == "delete":
        STATE["links"].pop(argv[3], None)
        STATE["addrs"].pop(argv[3], None)
    return ""


def _addr(argv, _input):
    # ["ip", "addr", "add", "10.0.0.1/24", "dev", "tap0"]
    if argv[2] == "add":
        ip = argv[3]
        dev = argv[argv.index("dev") + 1]
        STATE["addrs"].setdefault(dev, []).append(ip)
    elif argv[2] == "del":
        ip = argv[3]
        dev = argv[argv.index("dev") + 1]
        addrs = STATE["addrs"].get(dev, [])
        if ip in addrs:
            addrs.remove(ip)
    return ""


def _route(argv, _input):
    # ["ip", "route", "add", "10.0.0.2/32", "dev", "tap0"]
    op = argv[2]
    entry = {"cmd": argv[3:]}
    if op == "add":
        STATE["routes"].append(entry)
    elif op == "del":
        STATE["routes"][:] = [r for r in STATE["routes"] if r != entry]
    return ""


def _tuntap(argv, _input):
    # ["ip", "tuntap", "add", "dev", "tap0", "mode", "tap"]
    if argv[2] == "add":
        name = argv[argv.index("dev") + 1]
        STATE["links"][name] = {"netns": None, "kind": "tap", "peer": None}
    elif argv[2] == "del":
        STATE["links"].pop(argv[argv.index("dev") + 1], None)
    return ""


def _brctl(argv, _input):
    op = argv[1]
    if op == "addbr":
        STATE["bridges"].add(argv[2])
    elif op == "delbr":
        STATE["bridges"].discard(argv[2])
    return ""


def _mkfs(argv, _input):
    # ["mkfs.ext4", "-F", path]
    path = argv[-1]
    STATE["files"].setdefault(path, 0)
    return ""


def _truncate(argv, _input):
    # ["truncate", "-s", "100M", path]
    size_str = argv[argv.index("-s") + 1]
    size = int(size_str.rstrip("MGK")) * (1024 * 1024 if size_str.endswith("M") else 1)
    path = argv[-1]
    STATE["files"][path] = size
    return ""


def _mount(argv, _input):
    src, dst = argv[-2], argv[-1]
    STATE["mounts"][dst] = src
    return ""


def _umount(argv, _input):
    STATE["mounts"].pop(argv[-1], None)
    return ""


def _firecracker(argv, _input):
    # ["firecracker", "--api-sock", path, "--id", vm_id, ...]
    sock = argv[argv.index("--api-sock") + 1]
    STATE["fc_socks"][sock] = {"vm_dir": str(_dir_of(sock)), "running": True}
    return ""


def _kill(argv, _input):
    # ["kill", pid] — best-effort; nothing to do in fake
    return ""


def _cp(argv, _input):
    # ["cp", "--reflink=auto", source, dest] — record the per-VM rootfs clone.
    STATE["copies"].append((argv[-2], argv[-1]))
    return ""


def _debugfs(argv, _input):
    # ["debugfs", "-w", "-f", cmdfile, rootfs] — record the offline rootfs edit.
    STATE["debugfs"].append(list(argv))
    return ""


def _sysctl(argv, _input):
    # ["sysctl", "-w", "key=value"]
    if "-w" in argv:
        key, _, val = argv[argv.index("-w") + 1].partition("=")
        STATE["sysctl"][key] = val
    return ""


def _bridge(argv, _input):
    # ["bridge", "fdb", "append|del|show", ...]
    if len(argv) < 3 or argv[1] != "fdb":
        return ""
    op, dev = argv[2], argv[argv.index("dev") + 1]
    if op == "show":
        rows = STATE["fdb"].get(dev, set())
        return "".join(f"{m} dev {dev} dst {d} self permanent\n" for m, d in sorted(rows))
    entry = (argv[3], argv[argv.index("dst") + 1])
    bucket = STATE["fdb"].setdefault(dev, set())
    bucket.add(entry) if op in ("append", "add", "replace") else bucket.discard(entry)
    return ""


def _iptables(argv, _input):
    args = argv[1:]
    table = "filter"
    if "-t" in args:
        i = args.index("-t"); table = args[i + 1]; args = args[:i] + args[i + 2:]
    t = STATE["iptables"].setdefault(table, {"chains": set(), "builtin": set(), "rules": {}})
    fn = _IPT_OPS.get(args[0]) if args else None
    return fn(t, args[1:]) if fn else ""


def _ipt_new(t, a):       # -N CHAIN
    t["chains"].add(a[0]); t["rules"].setdefault(a[0], []); return ""


def _ipt_list(t, a):      # -nL [CHAIN] — raise if a named chain is absent
    if a and a[0] not in t["chains"]:
        raise PrivopsError(f"iptables: No chain/target/match by that name ({a[0]})")
    return ""


def _ipt_check(t, a):     # -C CHAIN rule... — raise if the exact rule is absent
    if tuple(a[1:]) not in t["rules"].get(a[0], []):
        raise PrivopsError("iptables: Bad rule (does a matching rule exist in that chain?)")
    return ""


def _ipt_append(t, a):    # -A CHAIN rule...
    t["rules"].setdefault(a[0], []).append(tuple(a[1:])); return ""


def _ipt_delete(t, a):    # -D CHAIN rule...
    lst = t["rules"].get(a[0], [])
    if tuple(a[1:]) in lst:
        lst.remove(tuple(a[1:]))
    return ""


def _ipt_flush(t, a):     # -F [CHAIN]
    targets = [a[0]] if a else list(t["rules"])
    for c in targets:
        t["rules"][c] = []
    return ""


def _ipt_xchain(t, a):    # -X [CHAIN]
    drop = [a[0]] if a else [c for c in t["chains"] if c not in t["builtin"]]
    for c in drop:
        t["chains"].discard(c); t["rules"].pop(c, None)
    return ""


def _dir_of(path: str):
    from pathlib import Path
    return Path(path).parent


_IP_SUB = {
    "netns":  _netns,
    "link":   _link,
    "addr":   _addr,
    "route":  _route,
    "tuntap": _tuntap,
}

_IPT_OPS = {
    "-N": _ipt_new,
    "-nL": _ipt_list,
    "-L": _ipt_list,
    "-C": _ipt_check,
    "-A": _ipt_append,
    "-D": _ipt_delete,
    "-F": _ipt_flush,
    "-X": _ipt_xchain,
}

_HANDLERS = {
    "ip":          _ip,
    "brctl":       _brctl,
    "mkfs.ext4":   _mkfs,
    "truncate":    _truncate,
    "mount":       _mount,
    "umount":      _umount,
    "firecracker": _firecracker,
    "kill":        _kill,
    "sysctl":      _sysctl,
    "bridge":      _bridge,
    "iptables":    _iptables,
    "cp":          _cp,
    "debugfs":     _debugfs,
}
