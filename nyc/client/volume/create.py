from pathlib import Path

from nyc.client import privops


def run(path: Path, size_mb: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    privops.run(["truncate", "-s", f"{size_mb}M", str(path)])
    privops.run(["mkfs.ext4", "-F", str(path)])
