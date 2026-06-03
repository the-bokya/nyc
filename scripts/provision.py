"""pyinfra deploy — idempotent bare-metal node setup for nyc.

  pyinfra inventory.py provision.py             # all nodes
  pyinfra inventory.py provision.py --limit n1   # one node

deploy.py drives this (bootstrap first, then joiners) and runs the post-provision
API smoke. Per-host config comes from inventory.py via host.data; the literal
unit/sudoers/Caddyfile artifacts live in templates/. Native operations (apt,
sysctl, files.template, systemd) carry their own idempotency; the inherently
imperative steps (uv installer, repo sync, rootfs bake) are check-then-act shell.
"""
from pathlib import Path

from pyinfra import host
from pyinfra.facts.server import Home, Which
from pyinfra.operations import apt, files, server, systemd

TEMPLATES = Path(__file__).resolve().parent / "templates"

d = host.data
home = host.get_fact(Home)
# uv installs to ~/.local/bin; use the real path if it's already there (re-runs).
uv_path = host.get_fact(Which, command="uv") or f"{home}/.local/bin/uv"
# Runtime-resolved uv for shell steps that run *after* the install op below.
UV = '"$(command -v uv || echo "$HOME/.local/bin/uv")"'

remote_dir = d.remote_dir.replace("~", home)
nyc_dir = f"{remote_dir}/nyc"
dadar_dir = f"{remote_dir}/dadar"
node_folder = f"{nyc_dir}/node"
config = f"{node_folder}/config.toml"
state = f"{home}/.nyc"  # pre-`up` snapshots, outside the (purge-able) checkout


def _bake_cmd() -> str:
    # Bake the shared pubkey + resolv.conf into the base rootfs via debugfs, so
    # plain `POST /vms` VMs are reachable for the ssh-jump deliverable.
    return (
        f'pub="$(cat {nyc_dir}/assets/id_ed25519.pub)"; '
        'cmds="$(mktemp)"; ak="$(mktemp)"; rc="$(mktemp)"; '
        'printf "%s\\n" "$pub" > "$ak"; '
        f'printf "nameserver {d.dns}\\n" > "$rc"; '
        '( echo "mkdir /root/.ssh"; '
        'echo "rm /root/.ssh/authorized_keys"; '
        'echo "write $ak /root/.ssh/authorized_keys"; '
        'echo "set_inode_field /root/.ssh/authorized_keys mode 0100600"; '
        'echo "set_inode_field /root/.ssh mode 040700"; '
        'echo "rm /etc/resolv.conf"; '
        'echo "write $rc /etc/resolv.conf"; '
        'echo "set_inode_field /etc/resolv.conf mode 0100644" ) > "$cmds"; '
        f'debugfs -w -f "$cmds" {nyc_dir}/assets/rootfs.ext4 || true; '
        'rm -f "$cmds" "$ak" "$rc"'
    )


def _lvm_config_cmds() -> list[str]:
    # Append nyc's lvm_* keys (dadar ignores them); strip prior lines first so a
    # re-provision never duplicates a key and breaks the TOML parse.
    cmds = [
        f'sed -i "/^lvm_/d" {config}',
        f'echo "lvm_vg = \\"{d.lvm_vg}\\"" >> {config}',
        f'echo "lvm_thinpool = \\"{d.lvm_thinpool}\\"" >> {config}',
    ]
    if d.lvm_device:
        cmds.append(f'echo "lvm_device = \\"{d.lvm_device}\\"" >> {config}')
    return cmds


def _nyc_config_cmds() -> list[str]:
    # Write domain / public-IP keys to config.toml. Strip prior lines first so
    # re-provision never duplicates. public_ips is written as a TOML array.
    ips = d.public_ips  # list[str] from inventory.py
    ips_toml = "[" + ", ".join(f'"{a}"' for a in ips) + "]"
    cmds = [
        f'sed -i "/^domain\\b/d;/^pubip_provider/d;/^public_iface/d;/^public_ips/d;/^pubip_gateway/d" {config}',
    ]
    if d.domain:
        cmds.append(f'echo "domain = \\"{d.domain}\\"" >> {config}')
    if d.pubip_provider:
        cmds.append(f'echo "pubip_provider = \\"{d.pubip_provider}\\"" >> {config}')
    if d.public_iface:
        cmds.append(f'echo "public_iface = \\"{d.public_iface}\\"" >> {config}')
    if ips:
        cmds.append(f'echo "public_ips = {ips_toml}" >> {config}')
    if d.pubip_gateway:
        cmds.append(f'echo "pubip_gateway = \\"{d.pubip_gateway}\\"" >> {config}')
    return cmds


server.shell(
    name="preflight: kvm + arch + sudo",
    commands=["test -e /dev/kvm", '[ "$(uname -m)" = x86_64 ]', "sudo -n true"],
)

server.shell(
    name="snapshot installed packages",
    commands=[
        f"mkdir -p {state}",
        f"[ -f {state}/pre_pkgs ] || dpkg -l | awk '/^ii/{{print $2}}' | sort > {state}/pre_pkgs",
    ],
)
apt.packages(
    name="apt packages",
    packages=["git", "curl", "e2fsprogs", "iproute2", "iptables", "ca-certificates", "lvm2"],
    update=True,
    _sudo=True,
)
server.shell(
    name="install uv",
    commands=["command -v uv >/dev/null 2>&1 || curl -fsSL https://astral.sh/uv/install.sh | sh"],
)
server.shell(
    name="install caddy (static binary)",
    commands=[
        "command -v caddy >/dev/null 2>&1 || { "
        't="$(mktemp)"; '
        "curl -fsSL -o \"$t\" 'https://caddyserver.com/api/download?os=linux&arch=amd64'; "
        'sudo -n install -m 0755 "$t" /usr/bin/caddy; rm -f "$t"; }'
    ],
)
server.shell(
    name="add login user to kvm group",
    commands=[f"sudo -n usermod -aG kvm {d.ssh_user} 2>/dev/null || true"],
)

server.shell(
    name=f"sync repo @ {d.ref}",
    commands=[
        f"mkdir -p {remote_dir}",
        f"[ -d {remote_dir}/.git ] || git -C {remote_dir} init -q",
        f"git -C {remote_dir} remote get-url origin >/dev/null 2>&1 "
        f"|| git -C {remote_dir} remote add origin {d.repo_url}",
        f"git -C {remote_dir} remote set-url origin {d.repo_url}",
        f"GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new' git -C {remote_dir} fetch origin {d.ref}",
        f"git -C {remote_dir} checkout -f FETCH_HEAD",
        f"GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new' git -C {remote_dir} submodule update --init --recursive",
    ],
)

server.shell(
    name="uv sync dadar + nyc",
    commands=[f"cd {dadar_dir} && {UV} sync", f"cd {nyc_dir} && {UV} sync"],
)
server.shell(
    name="install firecracker + rqlited",
    commands=[
        f"cd {nyc_dir} && ./scripts/install_firecracker.sh",
        f"cd {dadar_dir} && ./scripts/install_rqlite.sh",
    ],
)

server.shell(name="fetch artifacts", commands=[f"cd {nyc_dir} && ./scripts/fetch_artifacts.sh"])
files.put(
    name="upload shared VM private key",
    src=d.vm_key, dest=f"{nyc_dir}/assets/id_ed25519", mode="600",
)
files.put(
    name="upload shared VM public key",
    src=d.vm_pub, dest=f"{nyc_dir}/assets/id_ed25519.pub",
)
server.shell(name="bake shared key + resolv.conf into base rootfs", commands=[_bake_cmd()])

server.shell(
    name="snapshot ip_forward",
    commands=[
        f"mkdir -p {state}",
        f"[ -f {state}/pre_ip_forward ] || cat /proc/sys/net/ipv4/ip_forward > {state}/pre_ip_forward",
    ],
)
server.sysctl(
    name="enable ip_forward",
    key="net.ipv4.ip_forward", value=1,
    persist=True, persist_file="/etc/sysctl.d/99-nyc.conf",
    _sudo=True,
)

files.template(
    name="sudoers",
    src=str(TEMPLATES / "sudoers.j2"), dest="/etc/sudoers.d/nyc", mode="440",
    ssh_user=d.ssh_user, firecracker=f"{nyc_dir}/bin/firecracker",
    _sudo=True,
)
server.shell(name="validate sudoers", commands=["sudo -n visudo -cf /etc/sudoers.d/nyc"])

server.shell(
    name="dadar init",
    commands=[
        f"mkdir -p {node_folder}",
        f"cd {node_folder} && {UV} run --project {nyc_dir} dadar init "
        f"--host {d.node_host} --public-host {d.public_host} --domain {d.domain} "
        f"--http-port {d.http_port} --rqlite-http-port {d.rqlite_http_port} "
        f"--rqlite-raft-port {d.rqlite_raft_port}",
    ],
)
server.shell(name="write lvm config", commands=_lvm_config_cmds())
server.shell(name="write nyc config", commands=_nyc_config_cmds())

node_unit = files.template(
    name="nyc-node.service",
    src=str(TEMPLATES / "nyc-node.service.j2"),
    dest="/etc/systemd/system/nyc-node.service", mode="644",
    ssh_user=d.ssh_user, node_folder=node_folder, nyc_dir=nyc_dir, uv=uv_path,
    exec_args="--bootstrap" if d.role == "bootstrap" else f"--join {d.join_target}",
    vm_ttl_minutes=d.vm_ttl_minutes,
    _sudo=True,
)
systemd.service(
    name="enable + start nyc-node",
    service="nyc-node.service",
    running=True, enabled=True, daemon_reload=True, restarted=node_unit.changed,
    _sudo=True,
)

files.template(
    name="Caddyfile",
    src=str(TEMPLATES / "Caddyfile.j2"), dest=f"{node_folder}/Caddyfile",
    domain=d.domain, node_host=d.node_host, http_port=d.http_port,
)
caddy_unit = files.template(
    name="nyc-caddy.service",
    src=str(TEMPLATES / "nyc-caddy.service.j2"),
    dest="/etc/systemd/system/nyc-caddy.service", mode="644",
    caddyfile=f"{node_folder}/Caddyfile",
    _sudo=True,
)
systemd.service(
    name="enable + start nyc-caddy",
    service="nyc-caddy.service",
    running=True, enabled=True, daemon_reload=True, restarted=caddy_unit.changed,
    _sudo=True,
)
