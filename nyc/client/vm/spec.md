# client/vm

The firecracker process: its JSON config, spawning it, checking it, killing
it, and the argv to ssh into the guest. Networking is assumed to already
exist (built by `client/network`); this module only deals with the VM process
and its config.

## Actions

| File | Public fn | Does |
|---|---|---|
| `config.py` | `build(paths, cfg)` | Write firecracker JSON to `paths.config`. Returns the path. |
| `inject.py` | `run(paths, ssh_pubkey, dns, has_data_volume)` | One batched `debugfs` session on the per-VM rootfs copy: writes `/root/.ssh/authorized_keys` (if `ssh_pubkey` — replaces it, so the VM accepts only this key), `/etc/resolv.conf` (always), and — when `has_data_volume` — `/etc/fstab` + a systemd `home.mount` unit (belt-and-suspenders for images lacking the fstab generator). No mount, no loop device, no root. |
| `create.py` | `run(paths, vm_id, ns, firecracker_bin)` | Spawn firecracker inside netns `ns`; write pid. Returns pid. |
| `boot.py` | `run(paths)` | Stable "boot" verb. With `--config-file`, real firecracker auto-starts, so this is a no-op on `real` and a state flip on `fake`. |
| `status.py` | `run(paths)` | `"running"` \| `"stopped"`. |
| `kill.py` | `run(paths)` | SIGTERM the pid, wait up to 3s, clean up pid file. |
| `ssh.py` | `cmdline(ns, ip, key, user)` | Build an argv that ssh's into the guest from inside its netns. Caller adds `sudo`. |
| `list_dirs.py` | `run(vms_dir)` | List VM ids on disk (delegates to `env.teardown.list_dirs`). |

## Config (`config.py`)

`VmConfig` is the input dataclass: `vm_id`, `tap_name`, `mac`, `guest_ip`,
`cidr`, `has_data_volume`, `vcpu_count=1`, `mem_mib=512`, `dns="1.1.1.1"`.
`build` emits the four firecracker sections: `boot-source`, `drives`,
`machine-config`, `network-interfaces`.

The **key trick** lives in `_boot_args`: the kernel cmdline carries
`ip=<guest_ip>::<gateway>:<netmask>::eth0:off:<dns>`, so the guest's `eth0` (and
its resolver) is configured at kernel init — *before* userspace. That is why
SSH works with no DHCP server. `gateway`/`netmask` come from
`client/network/allocate`; `dns` defaults to `1.1.1.1`.

Drives: `rootfs` is always present as the root device, **read-write** (the
per-VM CoW copy from `env.setup`). A `data` drive (`paths.data`, read-write) is
appended only when `has_data_volume`.

## Process model (`create.py`)

- `real`: `sudo -n ip netns exec <ns> <firecracker_bin> --api-sock … --id …
  --config-file …` via `subprocess.Popen`; stdout/stderr → `firecracker.log`;
  pid written to `paths.pid_file`.
- `fake`: routes through `privops.run` and writes pid `0`.

`status` and `kill` read `paths.pid_file`; on `real` they probe with
`os.kill(pid, 0)`; on `fake` they read/mutate `privops_fake.STATE["fc_socks"]`.
The fake socket entry is created by `boot.run` and removed by `kill`.
