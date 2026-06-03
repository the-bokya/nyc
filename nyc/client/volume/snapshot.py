"""Snapshot / golden-image LV actions, wrapping the `lv` primitives.

A snapshot is a read-only thin freeze of a volume. A golden is a read-only thin
snapshot derived from a snapshot — a bootable rootfs source. Both are
independent thin LVs: deleting one never affects its origin or the volumes/VMs
cloned from it (the pool refcounts shared blocks).
"""
from nyc.client.volume import lv, names


def create(vg: str, source_lv: str, snap_id: str) -> str:
    """Read-only snapshot of any source LV (a data volume's or a VM's rootfs)."""
    return lv.snapshot(vg, source_lv, names.snap(snap_id), readonly=True)


def golden(vg: str, snap_id: str, gold_id: str) -> str:
    """Derive a read-only golden image from an existing snapshot (cheap, CoW)."""
    return lv.snapshot(vg, names.snap(snap_id), names.gold(gold_id), readonly=True)


def remove(vg: str, lv_name: str) -> None:
    lv.remove(vg, lv_name)
