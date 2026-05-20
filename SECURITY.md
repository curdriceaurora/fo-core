# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| v2.0.x  | :white_check_mark: |
| < 2.0   | :x:                |

## Reporting a Vulnerability

We take the security of fo-core seriously. If you believe you have found a security vulnerability,
please **do not** report it through public GitHub issues.

Instead, use [GitHub Security Advisories](https://github.com/curdriceaurora/fo-core/security/advisories/new)
to report privately. Include:

- Type of vulnerability and affected component
- Environment details (OS, Python version, fo-core version)
- Steps to reproduce
- Any proof-of-concept code or screenshots

You should receive an acknowledgment within 48 hours. Patches are typically released within 7–14 days.

---

## Security Architecture

fo-core is a CLI file organizer that reads from and writes to user-supplied directory trees.
The primary attack surface is **symlink injection**: a malicious actor could place symlinks
inside the watched/organized directory to redirect reads or writes outside the intended root.

### SafeDir primitive (`src/utils/safedir.py`)

All POSIX filesystem access that touches user-supplied paths goes through `SafeDir`, which
opens files and directories with `O_NOFOLLOW` (or `openat()` + `O_NOFOLLOW`) so that symlinks
in the path raise `SymlinkRejected` rather than being followed silently.

Key API surface:
- `SafeDir.open_root(path)` — open a watched or output root with `O_DIRECTORY`
- `SafeDir.open_child(name)` — open a file within the root using `O_NOFOLLOW`
- `SafeDir.open_subdir(name)` — open a subdirectory with `O_DIRECTORY | O_NOFOLLOW`
- `SafeDir.mkdir(name)` — create a subdirectory via `mkdirat()`

### File collection (`src/core/path_guard.py`)

`safe_walk()` is used for all recursive directory traversal. It skips:
- Symlinks (at both file and directory level)
- Hidden files and directories (dot-prefixed names)

### Watcher (`src/watcher/handler.py`, PR6 / #270)

The filesystem watcher (`FileEventHandler`) accepts an optional `SafeDir` opened on the watch
root. Every `CREATED` or `MODIFIED` event for a non-directory entry is checked via
`_safedir_allows()` before being enqueued. Events for symlinks are dropped and a
`security_event watcher_symlink_rejected` log entry is emitted.

### Pipeline destination hardening (`src/pipeline/stages/`, PR6 / #270)

`PostprocessorStage` opens the output root as a `SafeDir` on POSIX and caches per-category
subdirectory handles opened with `O_NOFOLLOW | O_DIRECTORY`. A symlink swap of a category
directory is detected at open time and raises `SymlinkRejected` — the file is not organized
and a `security_event destination_symlink_swap` entry is logged.

`WriterStage` writes the destination file using `os.open(name, O_WRONLY | O_CREAT | O_TRUNC,
dir_fd=safedir._fd)` so that a symlink swap of the destination file between the existence
check and the open is also rejected.

### Undo / history (`src/undo/`, PR5 / #269)

`durable_move` captures the destination inode `(st_dev, st_ino, st_size)` after the move
completes and stores it in the history database. Before replaying an undo, `rollback.py`
re-reads the inode via `os.lstat()` and refuses to proceed if the inode changed, logging a
`security_event undo_inode_mismatch` entry.

### Anchored traversal (`src/undo/validator.py`, PR5d / #264)

Path components resolved from history records are validated against the configured undo root
using `Path.is_relative_to()` before any filesystem operation.

### Lint rails

The repository has AST-based lint rails enforced in CI:

| Rail | Script | What it checks |
|------|--------|----------------|
| SafeDir reader rail | `scripts/check_safedir_required.py` | Library readers (`fitz.open`, `shutil.copy2`, etc.) and bare `open()` calls in enforced directories must carry a `# safedir: ok` opt-out or go through SafeDir |
| Atomic-write rail | `scripts/check_atomic_write.py` | Persistent state writes use `tempfile` + `os.replace()` |
| Anchored traversal rail | `scripts/check_anchored_traversal.py` | Path-join results validated against allowed root |

### Windows fallback

`SafeDir` is POSIX-only. On Windows, the pipeline falls back to `shutil.copy2` / `Path.mkdir`.
Windows path traversal is mitigated at the collection layer (`safe_walk`) and by NTFS junction
restrictions. Symlink creation on Windows requires elevated privileges.

---

## Known Limitations

- **Cross-device moves in `durable_move`**: when source and destination are on different
  filesystems, the move uses copy + unlink rather than `os.rename`. The inode verification
  step still guards undo replay.
- **NFS / FUSE mounts**: `O_NOFOLLOW` semantics vary across network filesystems. fo-core
  does not support watching NFS roots.
- **macOS SIP paths**: organizing files under SIP-protected directories (`/System`, `/usr`)
  will fail with `EPERM` regardless of SafeDir protection.
