"""pyinfra inventory — turns a cluster.toml into hosts + per-host data.

The TOML path comes from $NYC_CLUSTER (deploy.py sets it before shelling out to
`pyinfra`). Each host's identifier is its node name (so `--limit n1` works); it
connects over agent-forwarded ssh to public_host/domain/host. Everything the
provision/teardown deploys need is flattened into host.data here, so those files
read config without re-parsing TOML.
"""
import os
import tomllib
from pathlib import Path

_path = Path(os.environ.get("NYC_CLUSTER", "cluster.toml")).resolve()
_data = tomllib.loads(_path.read_text())
_cluster = _data.get("cluster", {})
_nodes = _data["nodes"]
_boot = next(n for n in _nodes if n.get("bootstrap"))
_keydir = _path.parent / ".nyc-deploy"


def _host(node: dict) -> tuple[str, dict]:
    raft = _cluster.get("rqlite_raft_port", 4002)
    return node["name"], {
        # --- ssh connector (matches the old `ssh -A` path) ---
        "ssh_hostname": node.get("public_host") or node.get("domain") or node["host"],
        "ssh_user": _cluster.get("ssh_user", "ubuntu"),
        "ssh_forward_agent": True,
        "ssh_strict_host_key_checking": "accept-new",
        # --- config the deploys read via host.data ---
        "repo_url": _cluster["repo_url"],
        "ref": _cluster.get("ref", "main"),
        "remote_dir": _cluster.get("remote_dir", "~/equator"),
        "node_host": node["host"],
        "public_host": node.get("public_host", ""),
        "domain": node.get("domain", ""),
        "http_port": _cluster.get("http_port", 8000),
        "rqlite_http_port": _cluster.get("rqlite_http_port", 4001),
        "rqlite_raft_port": raft,
        "dns": _cluster.get("dns", "1.1.1.1"),
        "role": "bootstrap" if node.get("bootstrap") else "join",
        "join_target": f"{_boot['host']}:{raft}",
        "vm_ttl_minutes": _cluster.get("vm_ttl_minutes", 0),
        "lvm_device": node.get("lvm_device", _cluster.get("lvm_device", "")),
        "lvm_vg": _cluster.get("lvm_vg", "nyc"),
        "lvm_thinpool": _cluster.get("lvm_thinpool", "pool"),
        # shared VM keypair (local paths, uploaded by provision.py)
        "vm_key": str(_keydir / "id_ed25519"),
        "vm_pub": str(_keydir / "id_ed25519.pub"),
        # public IP / domain config (written into each node's config.toml by provision.py)
        "cluster_domain": _cluster.get("vm_domain", ""),
        "pubip_provider": _cluster.get("pubip_provider", "scaleway"),
        "public_iface": node.get("public_iface", ""),
        "public_ips": node.get("public_ips", []),
        "pubip_gateway": node.get("pubip_gateway", ""),
    }


nyc = [_host(n) for n in _nodes]
