# scripts

Operational scripts: artifact fetch, single-host staging, and (bare-metal)
node preflight. Deploy/teardown orchestration lands here in Part F.

| File | What it does |
|---|---|
| `install_firecracker.sh` | Idempotently download the pinned firecracker binary into `bin/firecracker`. |
| `fetch_artifacts.sh` | Idempotently fetch the kernel (`assets/vmlinux`) + rootfs (`assets/rootfs.ext4`), generate an ssh keypair, and inject the pubkey (and, later, `/etc/resolv.conf`) into the rootfs. |
| `inject_ssh_key.sh` | Offline-inject a pubkey into `assets/rootfs.ext4` via `debugfs` (no mount, no sudo). Sets `authorized_keys` + `PermitRootLogin`. |
| `stage.sh [N] [--real] [--keep] [--no-tests]` | Single-host emulation: boot N dadar nodes in `./stage/`, run the e2e suite. `host` stays `127.0.0.1`, so cross-node overlay is not exercised here. |
| `preflight.py <cluster.toml>` | **Read-only** bare-metal readiness probe (see below). |
| `cluster.toml.example` | Inventory schema for `preflight.py` / `deploy.py`. Copy to `cluster.toml`. |

## preflight.py

Run before any bare-metal deploy to shrink unknowns. Reads the inventory and,
over `ssh -A` (agent forwarding, matching the deploy's git path), runs a
bundled read-only probe on each node in parallel. Mutates nothing.

```
python3 scripts/preflight.py cluster.toml        # probe every node
python3 scripts/preflight.py --local cluster.toml # run checks on THIS machine
python3 scripts/preflight.py --print-script       # dump the remote probe
```

Per-node checks (`PASS` / `WARN` / `FAIL` / `INFO`):

| Check | FAIL means |
|---|---|
| `os` | not Ubuntu (WARN if Ubuntu < 24.04; 24.04+ passes) |
| `arch` | not `x86_64` |
| `kvm` | no `/dev/kvm` (WARN if present but not rw for the user) |
| `virt` | (WARN) no `vmx`/`svm` CPU flag |
| `sudo` | `sudo -n true` fails — passwordless sudo is required |
| `internet` | no egress (VM internet, apt, git all need it) |
| `dns` | no name resolution |
| `git` | `git ls-remote <repo_url>` fails — agent forwarding or repo access broken |
| `peer:<ip>` | a peer's private IP is unreachable on tcp/22 (private network broken) |
| `port:<n>` | (WARN) http/rqlite port already in use |
| `domain` | (WARN) domain missing / not pointing at `public_host` (Caddy ACME will fail) |
| `clock` | (WARN) NTP not synced (raft prefers low skew) |
| `tools` | (INFO) packages the deploy will install |

Plus a top-level **CIDR overlap** check: the default `vpc_cidr` must not
contain any node's underlay `host`, or NAT/routing breaks. Exit code is
non-zero if any node has a `FAIL`.
