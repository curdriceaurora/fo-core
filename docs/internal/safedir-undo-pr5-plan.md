# PR5 Implementation Plan: Undo Inode-Pin + Anchored-Traversal Sweep

**Epic branch:** `epic/safedir-undo-pr5`
**Parent issue:** #269
**Depends on:** #266 (SafeDir primitive), #267 (PR3 read-side)
**Folded-in:** #286 (anchored-traversal sweep)

---

## Threat model

`fo undo` replays moves by reading the history record and calling
`durable_move(destination → source)`. The history record stores the source
path, destination path, hash, and size — but no `(st_dev, st_ino)`. A file at
`destination` at undo time may not be the file that was originally moved. A
crafted undo can silently clobber an unrelated file that happens to sit at the
same path.

---

## Key findings from codebase exploration

1. **No new SQL columns needed.** `Operation.metadata` is already a JSON TEXT
   column. `dest_dev` / `dest_ino` will be stored there, matching the existing
   `size` / `permissions` pattern. No `ALTER TABLE`, no schema version bump.

2. **History is recorded by callers of `durable_move`, not inside it.** The
   fd that has the destination's inode must be obtained by fstat-ing the
   destination _after_ the move lands and _before_ the history record is
   committed. The right place is in the caller that wraps the move and calls
   `HistoryTracker.record_operation()`.

3. **Anchored-traversal sweep (#286) is minimal.** The Explore agent found no
   raw `rglob`/`os.walk` call-sites in the originally listed files that are
   not already using `safe_walk` or SafeDir. The one real site
   (`validator.py:543`) is system-managed (trash dir, not user-supplied root)
   and is explicitly out of scope. This sub-PR will be a verification pass
   with a brief audit note.

4. **Test 6** (`test_undo_refuses_replay_on_inode_change`) is in
   `tests/security/test_symlink_safety.py` and currently
   `pytest.skip("blocked on PR5")`-ed.

---

## Sub-PR breakdown

### PR5a — `history/models.py`: add `dest_dev` / `dest_ino` to Operation metadata helpers

**Branch:** `epic/safedir-undo-pr5-5a`
**Files:** `src/history/models.py`, `src/history/tracker.py`
**Scope:**

- Add typed accessors `Operation.dest_dev`, `Operation.dest_ino`,
  `Operation.source_dev`, `Operation.source_ino` that read/write from
  `self.metadata` (no new dataclass fields — keeps the DB schema stable).
- Update `HistoryTracker.record_operation` to accept optional
  `dest_dev: int | None = None`, `dest_ino: int | None = None` kwargs and
  store them in the metadata dict before persisting.
- Update docstrings to document the fallback rule:
  > Pre-PR5 rows have `NULL` inode fields. Undo falls back to size+hash
  > check for these rows and logs a debug note.
- Tests: unit tests verifying round-trip serialization through
  `to_dict()` / `from_dict()` for both None (legacy) and int (new) values.

**Acceptance:**
- `Operation.dest_dev` returns `None` for legacy rows, `int` for new rows
- No SQL schema change, SCHEMA_VERSION unchanged
- `record_operation` stores dev/ino in metadata when provided

---

### PR5b — `undo/durable_move.py`: capture destination inode at move time

**Branch:** `epic/safedir-undo-pr5-5b`
**Files:** `src/undo/durable_move.py`, callers that write history
**Scope:**

- After the atomic `os.replace(tmp, dst)` (or same-device rename), fstat the
  destination to capture `(st_dev, st_ino)`.
- Propagate the triple back to the history-recording call site via a return
  value or a new `InodePin`-style struct (reuse `hasher.InodePin` from PR4).
- Wire the captured inode into `HistoryTracker.record_operation(dest_dev=...,
  dest_ino=...)`.
- Windows path: `sys.platform == "win32"` guard — skip inode capture,
  pass `None`.
- Tests: verify a move records non-None `dest_dev`/`dest_ino` in the history
  DB; verify a legacy row (None values) is handled gracefully at read time.

**Acceptance:**
- Post-move history rows have `dest_dev` and `dest_ino` set
- Windows path skips inode capture cleanly
- Existing `test_durable_move.py` suite passes without regression

---

### PR5c — `undo/rollback.py`: inode verification before replay

**Branch:** `epic/safedir-undo-pr5-5c`
**Files:** `src/undo/rollback.py`, `tests/security/test_symlink_safety.py`
**Scope:**

- In `RollbackExecutor.rollback_move`, before calling `self._move(destination,
  source)`:
  1. Check if `operation.dest_dev` is not None (new-style row).
  2. If yes: `os.stat(destination)` and compare `(st_dev, st_ino)` to the
     recorded triple.
  3. On mismatch: log `security_event undo_inode_mismatch`, refuse with a
     clear error string, return `False`.
  4. If None (legacy row): fall back to existing size+hash check (unchanged),
     log a debug note.
- Un-skip Test 6 in `tests/security/test_symlink_safety.py` with a concrete
  implementation.
- Add unit tests for: mismatch → refuse, legacy None → fallback path, match →
  proceeds normally.

**Acceptance:**
- Test 6 (`test_undo_refuses_replay_on_inode_change`) passes
- Pre-PR5 rows undo via size+hash fallback (regression test)
- Post-PR5 rows refuse when destination inode changed (new test)
- Security event logged with `exc_info=True`

---

### PR5d — Anchored-traversal sweep audit (#286)

**Branch:** `epic/safedir-undo-pr5-5d`
**Files:** Audit note in `docs/internal/`, minimal code if gaps found
**Scope:**

- Walk all files originally listed in #286:
  `extractor.py`, `epub_enhanced.py`, `hybrid_retriever.py`, `organizer/`,
  `undo/`.
- For each: confirm `rglob`/`os.walk` call-sites are already using
  `safe_walk` or SafeDir, or are system-managed (not user-supplied roots).
- Document findings. If gaps exist: apply `safe_walk` wrapper or
  `# safedir: ok — <reason>` opt-out with justification.
- Update `#286` issue with findings and close.

**Expected outcome:** All sites confirmed safe or opted-out with documented
rationale. No functional code change expected based on initial exploration.

---

## Merge order

```
epic/safedir-undo-pr5-5a  →  epic/safedir-undo-pr5
epic/safedir-undo-pr5-5b  →  epic/safedir-undo-pr5  (depends on 5a)
epic/safedir-undo-pr5-5c  →  epic/safedir-undo-pr5  (depends on 5b)
epic/safedir-undo-pr5-5d  →  epic/safedir-undo-pr5  (independent)
epic/safedir-undo-pr5     →  main
```

---

## References

- Issue #269 (parent tracking)
- Issue #286 (anchored-traversal, folded in)
- `src/undo/rollback.py` — `RollbackExecutor.rollback_move` (line ~149)
- `src/history/models.py` — `Operation` dataclass
- `src/history/tracker.py` — `record_operation`
- `src/undo/durable_move.py` — the atomic replace at line ~444
- `tests/security/test_symlink_safety.py` — Test 6 (line ~471)
- `docs/developer/safedir-readers.md` — SafeDir contributor guide

**Last updated:** 2026-05-20
