# client/volume

Per-VM read-write data volumes. Today a volume is a plain ext4 file on the
host (LVM later — see the roadmap in `nyc/README.md`). The root filesystem is
shared and read-only; this is the only writable disk a VM gets.

## Actions

| File | Public fn | Does |
|---|---|---|
| `create.py` | `run(path, size_mb)` | `truncate -s <N>M` then `mkfs.ext4 -F` to make a fresh ext4 file. Creates the parent dir. |
| `attach.py` | `run(vm_dir, volume_path)` | Symlink `volume_path` into `vm_dir/data.ext4`. Returns the link. Also `detach(vm_dir)`. |
| `delete.py` | `run(path)` | Remove the volume file (idempotent). |
| `list_files.py` | `run(volumes_dir)` | List volume files in a directory. |

## Why the symlink (`attach`)

Firecracker's config always references `vm_dir/data.ext4` (see
`vm/config.py` `_data_drive`). Symlinking the real volume to that fixed name
decouples *where volumes are stored* from *where firecracker looks*, so volume
storage can be organized independently of VM lifecycles. `attach` is
idempotent — it unlinks any existing target first.

## Backend notes

`create`/`delete`/`list_files` go through `privops`. On `fake`, volumes live as
entries in `privops_fake.STATE["files"]` rather than real files, so `delete`
and `list_files` consult `STATE` when `backend() == "fake"`. `attach` is pure
filesystem (symlinks work the same on both backends).
