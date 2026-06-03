"""pyinfra deploy — idempotent reverse of provision.py.

  pyinfra inventory.py teardown.py               # plain down (keeps LVM data + checkout)
  NYC_PURGE=1 pyinfra inventory.py teardown.py    # also remove packages + checkout + VG

deploy.py drives this (after a best-effort API VM delete) and sets NYC_PURGE for
`--purge`. Every step tolerates already-gone state, so a partial earlier run
never blocks teardown — which is why the kernel/iptables/package cleanups are
imperative shell rather than declarative ops.
"""
import os

from pyinfra import host
from pyinfra.facts.server import Home
from pyinfra.operations import files, server

d = host.data
home = host.get_fact(Home)
purge = os.environ.get("NYC_PURGE") == "1"

remote_dir = d.remote_dir.replace("~", home)
node_folder = f"{remote_dir}/nyc/node"
state = f"{home}/.nyc"


def _purge_pkgs_cmd() -> str:
    # apt-get remove only packages absent from the pre-`up` snapshot AND in the
    # known nyc install set, so we never touch anything we didn't add.
    return (
        f'pre={state}/pre_pkgs; [ -f "$pre" ] || exit 0; '
        'cur="$(mktemp)"; dpkg -l | awk \'/^ii/{print $2}\' | sort > "$cur"; '
        'added="$(comm -13 "$pre" "$cur")"; rm -f "$cur"; '
        'known="git curl e2fsprogs iproute2 iptables ca-certificates"; rm=""; '
        'for p in $known; do echo "$added" | grep -qx "$p" && rm="$rm $p"; done; '
        '[ -n "$rm" ] && sudo -n DEBIAN_FRONTEND=noninteractive apt-get remove -y $rm || true'
    )


server.shell(
    name="stop + remove services",
    commands=[
        "sudo -n systemctl disable --now nyc-node.service 2>/dev/null || true",
        "sudo -n systemctl disable --now nyc-caddy.service 2>/dev/null || true",
        "sudo -n rm -f /etc/systemd/system/nyc-node.service /etc/systemd/system/nyc-caddy.service",
        "sudo -n systemctl daemon-reload",
    ],
)

if purge and d.lvm_vg:
    # The VG holds VM data, so only wipe it on --purge (a plain down preserves it).
    server.shell(
        name=f"purge LVM vg {d.lvm_vg}",
        commands=[
            f"sudo -n vgchange -an {d.lvm_vg} 2>/dev/null || true",
            f"sudo -n vgremove -f -y {d.lvm_vg} 2>/dev/null || true",
        ] + ([f"sudo -n pvremove -ff -y {d.lvm_device} 2>/dev/null || true"] if d.lvm_device else []),
    )

server.shell(
    name="purge netns + links (anchored regexes)",
    commands=[
        r"ip netns list 2>/dev/null | awk '{print $1}' | grep -E '^vm-[0-9a-f]{8}$' "
        r'| while read -r ns; do sudo -n ip netns del "$ns" 2>/dev/null || true; done',
        r"ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d'@' -f1 "
        r"| grep -E '^(br-[0-9a-f]{4}-[0-9a-f]{4}|vx-[0-9a-f]{4}-[0-9a-f]{4}|vm[hn]-[0-9a-f]{8})$' "
        r'| while read -r dev; do sudo -n ip link del "$dev" 2>/dev/null || true; done',
    ],
)

server.shell(
    name="purge iptables chains",
    commands=[
        "sudo -n iptables -t nat -D POSTROUTING -j NYC-POSTROUTING 2>/dev/null || true",
        "sudo -n iptables -D FORWARD -j NYC-FORWARD 2>/dev/null || true",
        "sudo -n iptables -t nat -F NYC-POSTROUTING 2>/dev/null || true",
        "sudo -n iptables -t nat -X NYC-POSTROUTING 2>/dev/null || true",
        "sudo -n iptables -F NYC-FORWARD 2>/dev/null || true",
        "sudo -n iptables -X NYC-FORWARD 2>/dev/null || true",
    ],
)

server.shell(
    name="restore ip_forward",
    commands=[
        f'[ -f {state}/pre_ip_forward ] '
        f'&& sudo -n sysctl -w "net.ipv4.ip_forward=$(cat {state}/pre_ip_forward)" >/dev/null || true',
    ],
)
files.file(name="remove sysctl drop-in", path="/etc/sysctl.d/99-nyc.conf", present=False, _sudo=True)
server.shell(name="reload sysctl", commands=["sudo -n sysctl --system >/dev/null 2>&1 || true"])

if purge:
    server.shell(name="purge added packages", commands=[_purge_pkgs_cmd()])

files.directory(name="remove node folder", path=node_folder, present=False)
if purge:
    files.directory(name="remove checkout", path=remote_dir, present=False)
files.file(name="remove sudoers", path="/etc/sudoers.d/nyc", present=False, _sudo=True)
files.directory(name="remove state dir", path=state, present=False)
