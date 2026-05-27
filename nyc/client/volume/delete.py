from pathlib import Path

from nyc.client import privops


def run(path: Path) -> None:
    if not path.exists() and privops.backend() == "fake":
        from nyc.client.privops_fake import STATE
        STATE["files"].pop(str(path), None)
        return
    if path.exists():
        path.unlink()
