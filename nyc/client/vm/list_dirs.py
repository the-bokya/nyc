from pathlib import Path

from nyc.client.env.teardown import list_dirs as _list


def run(vms_dir: Path) -> list[str]:
    return [p.name for p in _list(vms_dir)]
