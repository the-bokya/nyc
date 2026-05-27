from pathlib import Path


def cmdline(ns: str, ip: str, key: Path, user: str = "root") -> list[str]:
    """Return an argv that ssh's into the given VM IP from inside its netns.

    Caller wraps with `sudo` if needed.
    """
    return [
        "ip", "netns", "exec", ns,
        "ssh", "-i", str(key),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        f"{user}@{ip}",
    ]
