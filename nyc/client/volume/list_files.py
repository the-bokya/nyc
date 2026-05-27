from pathlib import Path

from nyc.client import privops


def run(volumes_dir: Path) -> list[Path]:
    """Real volumes are files; fake volumes are entries in STATE['files']."""
    if privops.backend() == "fake":
        from nyc.client.privops_fake import STATE
        return [Path(p) for p in STATE["files"] if Path(p).parent == volumes_dir]
    if not volumes_dir.exists():
        return []
    return [p for p in volumes_dir.iterdir() if p.is_file()]
