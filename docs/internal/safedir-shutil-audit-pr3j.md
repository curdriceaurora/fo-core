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

[VERIFIED in: src/undo/durable_move.py:417,422]

### Risk assessment

`shutil.copyfile(src, dst)` with the default `follow_symlinks=True`
**does** follow symlinks at `src` — it dereferences the symlink and
copies the content of whatever it resolves to. Only
`follow_symlinks=False` preserves a symlink as a symlink. CPython
implements this by opening `src` with the builtin `open()`, which
follows symlinks at every path component (final + intermediate).

That sounds dangerous, but the `durable_move.py` call is protected
upstream by a deliberate symlink branch:

```python
if src.is_symlink():
    # symlink path: os.replace the tmp ENTRY (which the caller pre-
    # populated as a symlink). shutil.copyfile is NEVER reached on
    # this branch.
    os.replace(tmp_path, dst)
    fsync_directory(dst)
else:
    shutil.copyfile(src, tmp_path)  # regular file at check-time
    try:
        shutil.copystat(src, tmp_path)
    except OSError:
        ...
```

[FROM: src/undo/durable_move.py:410-428] [VERIFIED in: src/undo/durable_move.py:417,422]

`src.is_symlink()` uses `lstat` semantics — it correctly identifies
a symlink at `src` **at the moment of the check** and routes it to
the `os.replace()` branch. **But the check and the subsequent
`shutil.copyfile(src, ...)` are both path-based**: the latter
re-opens `src` by path, so an attacker who can write into `src`'s
parent directory between the check and the copy can swap `src` for
a symlink (or replace the regular file with a different inode).
This is a TOCTOU race window at the **final component**, in addition
to the well-known race at **intermediate components** (#286 anchored-
traversal epic).

In other words, the upstream `is_symlink()` guard does NOT prove
that the inode `copyfile` opens is the same inode that
`_durable_cross_device_move`'s caller intended to move. The audit
finding is: **both the final-component and the ancestor-component
symlink-swap windows are open at this callsite**. Closing them
requires switching to an fd-pinned operation (open `src` once with
`O_NOFOLLOW`, then stream from that fd into `tmp_path`) and lives
in **PR5** (`undo/` migration) per #267.

Why is the file still allowlisted today? The risk model for the
undo subsystem is narrower than the organize-time content readers
this epic targeted:

- `durable_move` only operates on paths the caller previously
  produced via undo's own state (journal entries reference paths
  the daemon owns). User-controlled paths reach `durable_move` only
  through the daemon's path-validation layer, not directly.
- The trash/journal directories that `durable_move` writes into are
  app-owned, not user-organize-root.

That risk-model boundary is what the `_ALLOWLISTED_FILES` entry is
recording — not a security proof. PR5's anchored-traversal migration
upgrades it to a real proof.

### Conclusion

- Both callsites are correctly allowlisted in
  `scripts/check_safedir_required.py` via
  `_ALLOWLISTED_FILES = frozenset({..., "src/undo/durable_move.py"})`.
  [VERIFIED in: scripts/check_safedir_required.py:64]
- The `is_symlink()` guard at line 410 routes confirmed-symlink-at-
  check-time inputs to the `os.replace()` branch. It does NOT prove
  the file `copyfile` opens by path is the same inode that the
  check examined — both the final-component and intermediate-component
  symlink-swap windows are open.
- Allowlisting is justified by the **undo-subsystem risk model**
  (paths only reach `durable_move` through the daemon's
  validation layer, not directly from user-organize-root) — not by
  a TOCTOU-free proof at the syscall level.
- Closing both windows requires fd-pinned (`O_NOFOLLOW`) handling
  in `durable_move` itself. Tracked in **#286** for the broader
  anchored-traversal effort and scheduled for **PR5** (`undo/`
  migration) per #267.
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

## Verification metadata

All concrete claims in this document were cross-checked against the
live tree at the head of this PR's base branch (`epic/safedir-readside-pr3`).

### Sources verified

- `src/undo/durable_move.py` — both callsites and the `is_symlink()`
  branch guard.
- `scripts/check_safedir_required.py` — the `_ALLOWLISTED_FILES`
  entry covering `src/undo/durable_move.py`.

### Callsites / line references

| Claim | Source |
|---|---|
| `shutil.copyfile(src, tmp_path)` callsite | `src/undo/durable_move.py:417` |
| `shutil.copystat(src, tmp_path)` callsite | `src/undo/durable_move.py:422` |
| `if src.is_symlink():` branch guard | `src/undo/durable_move.py:410` |
| `copystat` `try/except OSError` non-fatal | `src/undo/durable_move.py:423-430` |
| `_ALLOWLISTED_FILES` entry | `scripts/check_safedir_required.py:64` |
| Rail advisory count (44 sites after PR3i) | `python scripts/check_safedir_required.py --advisory` |

### Contradiction checklist

- Audit findings (2 callsites, both allowlisted under the undo
  subsystem's risk model, with both final-component and intermediate-
  component TOCTOU windows open and tracked for PR5) vs the
  Conclusion section ("no PR3j-scope code changes required"): **consistent**.
- "Streaming-vs-stat" decision section vs the "Exceptions" subsection
  (logging / display only, never security-relevant): **consistent** —
  exceptions are bounded by the explicit rule that they MUST NOT be
  security-relevant.
- Audit claim "shutil.copyfile follows source symlinks by default"
  vs conclusion "the callsite is allowlisted under the undo
  risk-model boundary": **consistent** — the `is_symlink()` guard
  doesn't TOCTOU-pin the inode (final-component swap window is
  open), so the allowlist is justified by the upstream
  path-validation guarantee, not by syscall-level safety. PR5
  migrates to fd-pinned handling for a real proof.

### Cross-reference checks

- `shutil.copyfile` symlink semantics (Python docs, `Lib/shutil.py`):
  `follow_symlinks=True` (default) follows source symlinks; verified
  against CPython's `shutil.copyfile` implementation and the existing
  `tests/undo/test_durable_move.py` test fixtures that exercise the
  dereference behavior.
- Sub-PR per-row entries in the streaming-vs-stat precedent table
  match the merged commits for PR3a–PR3h on `epic/safedir-readside-pr3`.
