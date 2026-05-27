"""Thin shim around privileged shellouts.

Two backends, selected by `NYC_BACKEND` (default `fake`):

- `real`: `run(argv)` shells out via `sudo -n <argv...>`. Used by the staging
  script and any deployment with passwordless sudo. Raises `PrivopsError` on
  non-zero exit.
- `fake`: `run(argv)` parses `ip` / `mkfs` / `mount` / `firecracker` argv into
  state mutations against an in-memory `STATE` dict. Used by unit tests so
  they pass without root or `/dev/kvm`.

Callers do NOT branch on the backend — they call `run(["ip", "link", ...])`
unconditionally. Branching belongs here.
"""
import os
import subprocess
from typing import Callable

from nyc.client.privops_fake import fake_run, reset_state, STATE  # re-export


class PrivopsError(RuntimeError):
    pass


def backend() -> str:
    return os.environ.get("NYC_BACKEND", "fake").lower()


def run(argv: list[str], input: str | None = None) -> str:
    impl = _impl()
    return impl(argv, input)


def _impl() -> Callable[[list[str], str | None], str]:
    return _real_run if backend() == "real" else fake_run


def _real_run(argv: list[str], input: str | None) -> str:
    result = subprocess.run(
        ["sudo", "-n", *argv],
        input=input,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PrivopsError(f"{' '.join(argv)} → {result.returncode}: {result.stderr.strip()}")
    return result.stdout


__all__ = ["run", "backend", "reset_state", "STATE", "PrivopsError"]
