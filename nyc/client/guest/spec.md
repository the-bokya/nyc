# client/guest

Run shell scripts inside a guest VM over root-namespace SSH.

## `run.py`

`run(ip, key, script) -> str`

Builds `ssh -i <key> -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
-o LogLevel=ERROR root@<ip> bash -s` and pipes `script` to stdin via
`privops.run(argv, input=script)`.

**Why root namespace:** the owner node's root namespace has the VPC bridge
(`br-<node>-<vpc>`) with a connected route to the VPC CIDR, so `ssh root@<vm_ip>`
works directly — no netns wrapper needed. This is the same pattern
`scripts/deploy.py:cmd_ssh` uses (proven path).

**Shared key:** `assets/id_ed25519` is baked into every rootfs by
`scripts/provision.py:_bake_cmd`, so every plain VM accepts it as root without
per-VM key injection.

**Fake backend:** `privops.run` with `ssh` argv is a no-op in the fake backend
(`_HANDLERS.get("ssh")` is None → returns `""`). Unit tests pass without a real
VM or network.
