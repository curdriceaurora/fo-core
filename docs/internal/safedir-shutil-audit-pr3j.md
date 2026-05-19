# SafeDir read-side closeout — `shutil` audit + streaming-vs-stat decision

**PR3j of #267**. Final deliverable in the read-side hardening epic.
Documents the audit findings for `shutil.copystat` / `shutil.copyfile`
plus the streaming-vs-stat decision adopted across PR3a–PR3i.

## Audit: `shutil.copystat` and `shutil.copyfile` in `src/`

### Findings

A repository scan for `shutil.copystat` and `shutil.copyfile` produces
exactly two callsites, both in the same function in `src/undo/durable_move.py`:

| Site | Call | Context |
|---|---|---|
| `src/undo/durable_move.py:417` | `shutil.copyfile(src, tmp_path)` | Inside `_durable_cross_device_move`, in the `else:` branch of `if src.is_symlink():`. Regular files only — symlinks take the dedicated `os.replace(tmp_path, dst)` branch. |
| `src/undo/durable_move.py:422` | `shutil.copystat(src, tmp_path)` | Same `else:` branch. Wrapped in `try / except OSError` (non-fatal — see comment at line 423). |

### Risk assessment

`shutil.copyfile(src, dst)` does NOT follow symlinks on the source side
when `follow_symlinks=True` is the default and Python sees a symlink at
`src` — it raises `IsADirectoryError` or copies the dereferenced target
depending on the Python version. The CPython source confirms that
`shutil.copyfile` opens both endpoints with the builtin `open()`, which
follows symlinks at every path component.

That sounds dangerous, but the `durable_move.py` call is protected
upstream:

```python
if src.is_symlink():
    # symlink path: os.replace the tmp ENTRY (which the caller pre-
    # populated as a symlink). shutil.copyfile is NEVER reached.
    os.replace(tmp_path, dst)
    fsync_directory(dst)
else:
    shutil.copyfile(src, tmp_path)  # regular file only
    try:
        shutil.copystat(src, tmp_path)
    except OSError:
        ...
```

`src.is_symlink()` resolves the LAST path component without following
it (`lstat`-style). For ancestor-component symlink swaps that an
attacker could perform between the `is_symlink()` check and the
`copyfile()` call, the existing `_durable_cross_device_move` workflow
DOES have a TOCTOU window — but this is the same window the rest of
the read-side has (see #286 anchored-traversal epic). Closing it for
`durable_move.py` belongs in **PR5** (`undo/` migration) per #267.

### Conclusion

- Both callsites are correctly allowlisted in
  `scripts/check_safedir_required.py` via
  `_ALLOWLISTED_FILES = frozenset({..., "src/undo/durable_move.py"})`.
- The `is_symlink()` guard at line 410 makes the `copyfile` path
  unreachable for symlinks at the final component.
- The intermediate-component TOCTOU is tracked separately (**#286**)
  and the migration to `SafeDir.open_path_under` lives in **PR5**.
- No PR3j-scope code changes required for these callsites.

## Decision: streaming from fd vs stat-then-open from path

### Context

Every PR3a–PR3h migration replaced a path-based reader with a
SafeDir-aware reader that:

1. Opens the file's parent with `SafeDir.open_root(parent)`.
2. Gets a raw fd via `safe_dir.open_for_reader(name)` (uses `O_NOFOLLOW`).
3. Wraps the fd with `os.fdopen(fd, "rb")`.
4. Hands the resulting fileobj to the content-extraction library.

A natural question: when the same code path also needs file metadata
(size, mtime, mode), where should it come from?

- **Stream from the SafeDir-opened fd**: `os.fstat(fd)`.
- **Stat-then-open from path**: `path.stat()` then SafeDir-open for content.

### Decision

**Stream from the SafeDir-opened fd whenever possible.** Use `os.fstat(fd)`
to obtain `st_size`, `st_mtime`, `st_mode`, etc. The path-based
`path.stat()` MUST NOT precede a SafeDir open in production code.

### Rationale

1. **TOCTOU**. `path.stat()` resolves the path through every
   intermediate symlink, then a subsequent `SafeDir.open_root + open_for_reader`
   re-resolves through the (possibly attacker-swapped) tree. The
   metadata seen by `stat()` and the bytes seen by the open may
   belong to **different files**. Reading `st_size` from one and
   the content from another is a classic confusion vector — an
   attacker swaps the file at a known size between the two calls
   and bypasses size-based defenses.

2. **`os.fstat(fd)` is TOCTOU-free for the file already open**.
   Once `open_for_reader(name)` returns an fd, that fd identifies a
   single open file description in the kernel. `os.fstat(fd)` reads
   metadata from the same inode that subsequent `read(fd)` calls
   will read content from. No racing window.

3. **`O_NOFOLLOW` only protects the final component**. The point of
   SafeDir is that the open refuses a symlink **at that final
   component**. A subsequent `path.stat()` on the same path
   re-traverses every intermediate, defeating the protection.

4. **Operational simplicity**. Streaming from the fd is the same
   number of syscalls (or fewer — no second open), and the code is
   linearly readable: "open via SafeDir, read everything I need
   from that fd, close".

### Precedent in PR3a–PR3h

| Sub-PR | File | Pattern |
|---|---|---|
| PR3a | `services/text_processor.py` | Streams text content from SafeDir fd. |
| PR3b–PR3d | `utils/readers/{archives,documents,cad,ebook,scientific}.py` | Each reader takes a `fileobj=` parameter; size check (`_check_file_size`) is path-based at the *legacy* entry only, not the SafeDir-aware caller. |
| PR3e | `services/deduplication/extractor.py` | Streams content; size limits enforced from `_extract_from_fileobj` via `fileobj.read(n)` caps, not `path.stat()`. |
| PR3f | `services/deduplication/{image_dedup,image_utils,viewer,quality}.py` | `os.fstat(fd)` used in `viewer.py` for `file_size` / `mtime` display — the explicit precedent for this decision. |
| PR3g | `utils/epub_enhanced.py` | EPUB is read as a stream from the SafeDir fd; no metadata-then-content split. |
| PR3h | `services/search/hybrid_retriever.py`, `core/organizer.py`, `methodologies/para/detection/heuristics.py` | All three new helpers read a bounded byte range from the SafeDir fd directly (`fileobj.read(limit)`). No `path.stat()` precedes the open. |

### Exceptions (with rationale)

A few sites legitimately use `path.stat()` before a SafeDir open. These
are documented and acceptable because:

- The stat is used purely for **logging / display** — not for any
  security-relevant decision (e.g., a "skip files larger than N MB"
  guard that's user-visible UX, not a security boundary).
- The stat happens **after** SafeDir-aware enumeration of the
  containing directory, so the path is already trust-bounded.

Each such site carries (or will carry) an inline comment explaining
why the dual-stat pattern is safe in that context.

### What this rules out

- Path-based size validation before opening content. Wrong: see
  CVE-2021-3580 class issues where the size check and the content
  read disagree.
- Path-based mtime caching before reading. Wrong for the same reason.
- Any "check this file is OK to read by stat-ing it first" pattern.

### What this rules in

- `os.fstat(safe_dir.open_for_reader(name))` for size / mtime / mode
  when the file is already SafeDir-opened.
- `safe_dir.lstat(name)` (no-follow) when only the metadata is needed
  and no content read follows. The SafeDir primitive exposes `lstat`
  precisely for this case (#266).

## Closeout

This document concludes the read-side hardening epic (#267) for
**Phase 3** (read-side). PR3a–PR3h migrated 18 content-reading sites
to SafeDir; PR3i flipped the bare-open detection from advisory to
enforcing for every migrated file; PR3j (this PR) confirms the
remaining `shutil.copystat` / `shutil.copyfile` callsites are
already correctly handled and codifies the streaming-vs-stat
decision that applied across the epic.

Subsequent phases:

- **PR4** (#268) — `services/deduplication/{hasher, backup, embedder}` migrations.
- **PR5** (#269) — `undo/{durable_move, rollback}` migrations.
  Addresses the anchored-traversal gap (#286) for the `shutil.copyfile`
  callsite identified in this audit.
- **PR6** (#270) — `watcher/`, `daemon/`, `pipeline/stages/{writer, postprocessor}`,
  `core/file_ops` migrations.

Parallel tracking issues that remain after PR3 closes:

- **#283** — alias-aware module-style `.open` detection.
- **#286** — anchored-traversal hardening across all read-side migrations.

## References

- #264 — original LLM-exfiltration vector documented by Codex.
- #267 — read-side SafeDir hardening epic.
- #271 — original bare-open detection requirement.
- #266 — SafeDir primitive design.
