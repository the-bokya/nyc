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

STATE: dict[str, Any] = {}


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

_HANDLERS = {
    "ip":          _ip,
    "brctl":       _brctl,
    "mkfs.ext4":   _mkfs,
    "truncate":    _truncate,
    "mount":       _mount,
    "umount":      _umount,
    "firecracker": _firecracker,
    "kill":        _kill,
}
