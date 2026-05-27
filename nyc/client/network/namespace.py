from nyc.client import privops


def create(name: str) -> None:
    privops.run(["ip", "netns", "add", name])


def delete(name: str) -> None:
    privops.run(["ip", "netns", "del", name])


def exists(name: str) -> bool:
    out = privops.run(["ip", "netns", "list"])
    names = [line.split()[0] for line in out.splitlines() if line.strip()]
    return name in names


def list_all() -> list[str]:
    out = privops.run(["ip", "netns", "list"])
    return [line.split()[0] for line in out.splitlines() if line.strip()]
