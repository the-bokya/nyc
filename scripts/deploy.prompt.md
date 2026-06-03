# deploy.prompt.md

Prompt to regenerate the per-node bare-metal scripts `provision.sh` /
`teardown.sh` if they're lost (driven by `deploy.py` over `ssh -A`; orchestrator
in `spec.md`). Implement exactly this.

**Contract.** Bash, `set -euo pipefail`, configured *only* via env vars (passed
on the ssh line). Every step is **check-then-act** (re-runnable); privileged ops
use `sudo -n`. Target Ubuntu 24.04+ / x86_64 / `/dev/kvm`; one node folder at
`$REMOTE_DIR/nyc/node`. Pre-`up` snapshots live in `$HOME/.nyc` (outside the
checkout, which `--purge` deletes) so teardown can find them.

**Env in:** `REPO_URL REF REMOTE_DIR SSH_USER` · `NODE_NAME NODE_HOST
PUBLIC_HOST DOMAIN` · `HTTP_PORT RQLITE_HTTP_PORT RQLITE_RAFT_PORT` · `VPC_CIDR
DNS` · `ROLE`(bootstrap|join) `JOIN_TARGET`(host:raft) · `VM_KEY_B64 VM_PUB_B64`
(shared VM keypair) · `VM_TTL_MINUTES`. Let `uv = command -v uv || $HOME/.local/bin/uv`.

## provision.sh (ordered)
1. preflight: `[ -e /dev/kvm ]`, `uname -m`=x86_64, `sudo -n true` — else fatal.
2. snapshot `dpkg -l` ii-names → `$HOME/.nyc/pre_pkgs` (once); `apt-get install -y
   git curl e2fsprogs iproute2 iptables ca-certificates`; install uv if absent;
   install Caddy **static binary** → `/usr/bin/caddy`; `usermod -aG kvm $SSH_USER`.
3. sync repo (idempotent, **init-in-place not clone**): `git init`; remote
   origin=`$REPO_URL`; `fetch origin $REF`; `checkout -f FETCH_HEAD`;
   `submodule update --init --recursive`.
4. `uv sync` in `dadar/` then `nyc/`.
5. `nyc/scripts/install_firecracker.sh`; `dadar/scripts/install_rqlite.sh`.
6. `nyc/scripts/fetch_artifacts.sh`; if `VM_KEY_B64` set, base64-decode the
   shared keypair into `nyc/assets/id_ed25519{,.pub}` (key mode 600); bake the
   shared pubkey + resolv.conf into the base rootfs (debugfs template below).
7. snapshot `ip_forward` → `$HOME/.nyc/pre_ip_forward` (once); write
   `/etc/sysctl.d/99-nyc.conf` = `net.ipv4.ip_forward=1`; `sysctl --system`.
8. write the sudoers template; `visudo -cf` it; install mode 0440 at
   `/etc/sudoers.d/nyc`.
9. in the node folder: `uv run --project nyc dadar init --host $NODE_HOST
   --public-host $PUBLIC_HOST --domain $DOMAIN --http-port … --rqlite-http-port …
   --rqlite-raft-port …`.
10. install + `systemctl enable --now nyc-node.service` (template; ExecStart gets
    `--bootstrap`, or `--join $JOIN_TARGET` when `ROLE=join`).
11. write the Caddyfile + install + `enable --now nyc-caddy.service`.

## teardown.sh (reverse; `PURGE=1` = full)
1. stop/disable + rm both unit files; `systemctl daemon-reload`.
2. purge kernel state by **anchored** regexes: `ip netns del` each
   `^vm-[0-9a-f]{8}$`; `ip link del` each
   `^(br-[0-9a-f]{4}-[0-9a-f]{4}|vx-[0-9a-f]{4}-[0-9a-f]{4}|vm[hn]-[0-9a-f]{8})$`.
3. iptables: `-t nat -D POSTROUTING -j NYC-POSTROUTING`; `-D FORWARD -j
   NYC-FORWARD`; then `-F` and `-X` both `NYC-*` chains (nat + filter).
4. restore `ip_forward` from `$HOME/.nyc/pre_ip_forward`; rm the sysctl drop-in;
   `sysctl --system`.
5. rm the node folder; rm `/etc/sudoers.d/nyc`.
6. `PURGE=1`: `apt-get remove` packages absent from `$HOME/.nyc/pre_pkgs`; rm
   `$REMOTE_DIR`.

## Literal artifacts (templated where `$VAR` appears)
`/etc/sudoers.d/nyc` (one line):
```
$SSH_USER ALL=(root) NOPASSWD: /usr/sbin/ip,/usr/bin/ip,/usr/sbin/iptables,/usr/bin/iptables,/usr/sbin/sysctl,/usr/sbin/bridge,/usr/bin/bridge,/sbin/mkfs.ext4,/usr/sbin/mkfs.ext4,/usr/bin/mount,/usr/bin/umount,/usr/bin/truncate,/usr/bin/kill,$REMOTE_DIR/nyc/bin/firecracker
```
`/etc/systemd/system/nyc-node.service`:
```
[Unit]
Description=nyc dadar node
After=network-online.target
Wants=network-online.target
[Service]
User=$SSH_USER
WorkingDirectory=$REMOTE_DIR/nyc/node
ExecStart=/usr/bin/env <uv> run --project $REMOTE_DIR/nyc dadar run <--bootstrap | --join $JOIN_TARGET>
Restart=on-failure
Environment=NYC_BACKEND=real
Environment=NYC_VM_TTL_MINUTES=${VM_TTL_MINUTES:-0}
[Install]
WantedBy=multi-user.target
```
`$REMOTE_DIR/nyc/node/Caddyfile` + `/etc/systemd/system/nyc-caddy.service`:
```
# Caddyfile:  $DOMAIN { reverse_proxy $NODE_HOST:$HTTP_PORT }
[Unit]
Description=nyc caddy TLS front-end
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/bin/caddy run --config $REMOTE_DIR/nyc/node/Caddyfile --adapter caddyfile
Restart=on-failure
[Install]
WantedBy=multi-user.target
```
rootfs bake — `debugfs -w -f <cmds> nyc/assets/rootfs.ext4`, cmds:
```
mkdir /root/.ssh
rm /root/.ssh/authorized_keys
write <pubfile> /root/.ssh/authorized_keys
set_inode_field /root/.ssh/authorized_keys mode 0100600
set_inode_field /root/.ssh mode 040700
rm /etc/resolv.conf
write <resolvfile> /etc/resolv.conf      # contents: "nameserver $DNS"
set_inode_field /etc/resolv.conf mode 0100644
```
