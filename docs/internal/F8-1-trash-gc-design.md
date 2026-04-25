# F8.1 Trash GC Coordination — Design Spec

Tracks: issue #202 (depends on #201, merged as PR #203 / commit d0d6808d).
Supersedes: PR #197 §6 non-goals item "trash GC deletion API".

## 1. Goal

Close the check-then-delete TOCTOU race in the F8 trash-GC surface. Today,
`OperationValidator.is_trash_safe_to_delete()` answers a one-instant predicate;
any caller that does

```python
if validator.is_trash_safe_to_delete(p):
    p.unlink()
```

can race a concurrent rollback / delete-to-trash that journals an entry between
the predicate read and the unlink. F8.1 ships a single blessed deletion entry
point that performs the check and the delete under one lock, so the check result
is durable for the full deletion operation.

Non-goals (per #202 issue body):

- No new journal schema or recovery semantics — those belong to #201 (now merged).
- No true durable directory recovery — `dir_move` stays coordination-only per
  #201 §5.3.
- No history DB cleanup — this is filesystem-level trash deletion only.
- No conversion of the seven non-undo `shutil.move` sites in the roadmap.

---

## 2. Surface

One new public API. One new outcome enum. Both live in a new module so the
existing `OperationValidator` stays focused on validation (separating mutation
into a dedicated class avoids the F8 WRONG_ABSTRACTION pattern of mixing
predicate + side-effect responsibilities).

```python
# src/undo/trash_gc.py

from enum import Enum
from dataclasses import dataclass
from pathlib import Path

class TrashDeleteResult(str, Enum):
    DELETED = "deleted"                          # path removed cleanly
    DELETED_WITH_STAGING_FAILURE = "deleted_with_staging_failure"
    # ^ directory case only: the user's path is gone (atomic rename succeeded
    # under lock), but the unlocked rmtree of the staging dir failed. Orphan
    # remains under <trash_dir>/.pending-delete-* for next-init recovery to
    # pick up. From the user's perspective the entry is no longer in trash;
    # surfaced as a distinct outcome so operators see the partial state.
    SKIPPED_IN_FLIGHT = "skipped"                # journal shows active move
    MISSING = "missing"                          # path didn't exist (no-op)
    PERMISSION_ERROR = "permission_error"        # OSError on rename/unlink
    OUTSIDE_TRASH = "outside_trash"              # escapes trash root

@dataclass(frozen=True)
class TrashDeleteOutcome:
    result: TrashDeleteResult
    path: Path
    reason: str
    error: BaseException | None = None
    # ^ populated for PERMISSION_ERROR (the rename/unlink raise) and for
    # DELETED_WITH_STAGING_FAILURE (the unlocked rmtree raise — surfaced so
    # operators can correlate with the orphan staging dir).

class TrashGC:
    def __init__(
        self,
        trash_dir: Path,
        *,
        journal_path: Path | None = None,
    ) -> None:
        # Eager: scan trash_dir for .pending-delete-* orphans from prior
        # crashes (rename succeeded, rmtree didn't run / didn't finish) and
        # rmtree them. Lockless — these names are GC-owned and isolated from
        # the user's path namespace by construction. Runs once per
        # construction; tests that instantiate many TrashGC pay the scan
        # cost repeatedly (acceptable: scan is O(top-level entries)).
        ...

    def safe_delete(self, path: Path) -> TrashDeleteOutcome: ...
```

The class is intentionally tiny — one public method. Owns:

- Eager init-time recovery of orphan staging directories.
- Lock acquisition for the check + rename / unlink syscalls.
- Unlocked `rmtree` of the staging directory for the directory case.
- Outcome mapping per §5.1.

---

## 3. Lock protocol

### 3.1 Why `LOCK_EX` (not `LOCK_SH`)

`is_path_in_flight()` already takes `LOCK_SH` for read coordination. F8.1 needs
*mutual exclusion against itself* (two concurrent GC processes must not both
decide-and-delete in parallel) AND against `_append_journal` writers. POSIX
`flock` semantics:

- `LOCK_SH` is granted only when no `LOCK_EX` is held.
- `LOCK_EX` requires no other lock (shared or exclusive) to be held.

Holding `LOCK_SH` during the unlink would let two GC processes both hold the
shared lock simultaneously, decide independently, and double-delete (one wins
the actual unlink, the other gets `FileNotFoundError` from a stale check). So
GC takes `LOCK_EX` on `<journal>.lock` (per #201 §6.1 — the stable-inode
sibling lock file, never the journal itself) for the full check-and-delete.

### 3.2 Sequence per call (file / symlink — fast path)

```text
1. Resolve + validate path is inside trash_dir (rejection rule §4.1).
2. Acquire LOCK_EX on <journal>.lock (per #201 §6.1).
3. is_path_in_flight(path) — SKIPPED if True.
4. os.path.lexists(path) — MISSING if False.
5. unlink (the path / the symlink). Map OSError → PERMISSION_ERROR.
6. Release LOCK_EX.
7. Return outcome.
```

Steps 3–5 happen under the held `LOCK_EX`. No writer can acquire its own
`LOCK_EX` between steps 3 and 5, so the in-flight predicate stays valid.

### 3.3 Sequence per call (directory — atomic rename + unlocked rmtree)

```text
1. Resolve + validate path is inside trash_dir (rejection rule §4.1).
2. Acquire LOCK_EX on <journal>.lock.
3. is_path_in_flight(path) — SKIPPED if True.
4. os.path.lexists(path) — MISSING if False.
5. staging = trash_dir / f".pending-delete-{uuid4().hex}"
6. os.rename(path, staging) — atomic, single-fs (both inside trash_dir).
   Map rename OSError → PERMISSION_ERROR (path still at original).
7. Release LOCK_EX.
8. shutil.rmtree(staging).
   - On success: outcome DELETED.
   - On OSError (mid-walk permission, FS error): outcome
     DELETED_WITH_STAGING_FAILURE. The user's path is gone (rename
     succeeded under lock); the orphan staging dir survives for the next
     TrashGC.__init__ to clean.
9. Return outcome.
```

Lock-hold scope is bounded by **one** rename syscall (microseconds),
regardless of how big the directory tree is. The expensive `rmtree` runs
unlocked. Concurrent writers (`_append_journal`, sweep, `safe_delete` of
some other path) can proceed as soon as step 7 releases the lock.

Why the rename is safe:

- `staging` lives inside `trash_dir`, so the rename is single-filesystem
  (no EXDEV failure mode).
- The `.pending-delete-*` namespace is GC-owned: no other writer (rollback,
  sweep, validator) ever touches a path with that prefix, so the unlocked
  rmtree cannot race anyone.
- The UUID4 hex suffix avoids collisions across processes / restarts — no
  PID is involved (PID reuse would be the same trap F2 / F7.1 documented).

### 3.4 Init-time orphan recovery

```text
TrashGC.__init__(trash_dir):
  # Eager — once per construction, no lock needed.
  for entry in trash_dir.iterdir():
      if entry.name.startswith(".pending-delete-"):
          shutil.rmtree(entry, ignore_errors=False)
          # Same OSError → log WARNING, leave entry, continue. The next
          # construction will retry. No fatal exception out of __init__.
```

`ignore_errors=False` so we see real failures; the catch + WARNING happens
in the calling loop so other orphans still get cleaned. The recovery does
NOT need the journal lock — these paths are isolated from the user's
namespace and from `is_path_in_flight` (the rename was already journalled
as a regular trash entry whose original path is gone).

### 3.5 Worst-case lock-hold latency

| Path type | Lock-held syscalls | Bound |
|-----------|---------------------|-------|
| File / symlink | `is_path_in_flight` read + `unlink` | microseconds |
| Directory | `is_path_in_flight` read + `os.rename` | microseconds |
| Directory `rmtree` (unlocked) | (n/a — no lock held) | O(entries) |

So writers are never blocked by GC for longer than two journal-read +
one fast-syscall worth of time, regardless of the trash entry's size.
This eliminates the original §3 concern about pinning the lock for
seconds on a large trash directory.

---

## 4. Path validation

### 4.1 `OUTSIDE_TRASH` rejection

`safe_delete` MUST refuse paths that resolve outside `self.trash_dir`. Without
this guard, an attacker (or a logic bug elsewhere) could pass `../../etc/passwd`
and have GC delete it. Implementation mirrors the F4 path-validation pattern
already used elsewhere in the codebase:

```python
allowed = self.trash_dir.resolve()
requested = path.resolve()
try:
    requested.relative_to(allowed)
except ValueError:
    return TrashDeleteOutcome(
        result=TrashDeleteResult.OUTSIDE_TRASH,
        path=path,
        reason=f"path {path} is outside the configured trash root {allowed}",
    )
```

This runs BEFORE the lock acquisition — a path that's clearly out of bounds
shouldn't even start the lock dance.

### 4.2 Symlink handling

Trash entries CAN be symlinks (rollback's restore-from-trash preserves symlink
identity per PR #197 codex gnab). `safe_delete` must:

- Use `os.path.lexists` (not `Path.exists`) for missing-path detection so
  dangling symlinks count as present.
- Dispatch via `path.is_symlink() or not path.is_dir()` → `unlink`, else
  `rmtree`. Symlinks to directories MUST go through `unlink`, not
  `rmtree(follow_symlinks=...)` — that would walk into the link target and
  destroy unrelated data.
- Preserve the path string used for `is_path_in_flight()` so the writer's
  normalization (`os.path.normcase(os.path.abspath(...))`) matches.

---

## 5. Outcome mapping

### 5.1 Decision table

| State | Path type | `is_path_in_flight` | On-disk before | Lock-held op | Unlocked op | Outcome |
|-------|-----------|---------------------|----------------|--------------|-------------|---------|
| Outside trash root | (any) | (not checked) | (not checked) | (none) | (none) | `OUTSIDE_TRASH` |
| In-flight | (any) | True | (not checked) | (none after check) | (none) | `SKIPPED_IN_FLIGHT` |
| Path missing | (any) | False | absent | (none after lexists) | (none) | `MISSING` |
| File/symlink — unlink ok | file/link | False | present | `unlink` | (none) | `DELETED` |
| File/symlink — `OSError` | file/link | False | present | `unlink` raises | (none) | `PERMISSION_ERROR` |
| Directory — full success | dir | False | present | `rename` | `rmtree` ok | `DELETED` |
| Directory — rename fails | dir | False | present | `rename` raises | (none) | `PERMISSION_ERROR` (path still at original) |
| Directory — rmtree fails | dir | False | present | `rename` ok | `rmtree` raises | `DELETED_WITH_STAGING_FAILURE` (orphan in trash_dir/.pending-delete-*) |
| Race: missing between lexists and op | (any) | False | gone | `unlink`/`rename` raises `FileNotFoundError` | (n/a) | `MISSING` (idempotent — `FileNotFoundError` mapped to MISSING, not PERMISSION_ERROR) |

### 5.2 Idempotency

`MISSING` is a successful outcome — second-call idempotency is required so
GC can safely retry on transient errors without reporting spurious failures.
`PERMISSION_ERROR` is the only failure outcome; callers retry or escalate.

### 5.3 Logging

Each outcome logs at a level matched to its severity:

| Outcome | Log level |
|---------|-----------|
| `DELETED` | `DEBUG` |
| `MISSING` | `DEBUG` |
| `SKIPPED_IN_FLIGHT` | `INFO` (operator-visible coordination event) |
| `PERMISSION_ERROR` | `WARNING` (with `exc_info=True`) |
| `OUTSIDE_TRASH` | `WARNING` (potential security issue worth surfacing) |
| `DELETED_WITH_STAGING_FAILURE` | `WARNING` (with `exc_info=True`; mentions the orphan staging path so operators can correlate with next-init recovery) |

Init-time recovery (§3.4) logs each successful orphan rmtree at `DEBUG`,
each rmtree failure at `WARNING` (with `exc_info=True`), and emits an
aggregated `INFO` line `"trash GC init recovery: %d orphans cleaned, %d failed"`
so an operator can spot a stuck-orphan pattern (e.g. permission perma-deny on
a specific subtree) without scanning every DEBUG line.

---

## 6. Test plan

All under `tests/undo/test_trash_gc.py`. Class layout per scenario.

### 6.1 Outcome unit tests

- `test_safe_delete_returns_deleted_for_quiet_path` — no journal entries,
  file path exists → `DELETED`, file gone.
- `test_safe_delete_returns_missing_for_absent_path` — path doesn't exist →
  `MISSING`, no error.
- `test_safe_delete_returns_skipped_when_path_in_flight` — write a `move
  started` journal entry whose dst is the trash path → `SKIPPED_IN_FLIGHT`,
  file still present.
- `test_safe_delete_returns_outside_trash_for_escaped_path` — pass
  `tmp_path / "outside" / "x"` → `OUTSIDE_TRASH`, no operation.
- `test_safe_delete_handles_dangling_symlink` — symlink in trash whose
  target is missing → `DELETED` (lexists detects it), symlink gone.
- `test_safe_delete_directory_via_staging_rename` — trash entry is a
  populated directory → `DELETED`, dir gone, no `.pending-delete-*`
  orphan left in trash_dir (rmtree finished).
- `test_safe_delete_file_returns_permission_error_on_unlink_oserror` —
  monkeypatch `Path.unlink` to raise `PermissionError` → result
  `PERMISSION_ERROR`, error populated, file still present.
- `test_safe_delete_directory_returns_permission_error_when_rename_fails`
  — monkeypatch `os.rename` to raise `PermissionError` → result
  `PERMISSION_ERROR`, dir still at original path, no staging dir created.
- `test_safe_delete_directory_returns_partial_failure_when_rmtree_fails`
  — monkeypatch `shutil.rmtree` to raise `OSError` → result
  `DELETED_WITH_STAGING_FAILURE`, original path gone, `.pending-delete-*`
  staging dir survives in trash_dir, error populated, log includes the
  staging path.
- `test_safe_delete_maps_filenotfound_during_op_to_missing` — pass a path
  that lexists but vanishes between lexists and unlink (use a fixture
  that monkeypatches lexists True then deletes the file before unlink) →
  `MISSING`, not `PERMISSION_ERROR`. Tests the idempotency rule §5.2.
- `test_safe_delete_does_not_follow_symlink_to_directory` — symlink in
  trash points at an unrelated directory; `safe_delete` must use `unlink`
  not `rmtree` and not `rename`. Verify the link target tree is intact
  post-call.

### 6.2 Race / coordination tests

- `test_safe_delete_skips_when_dir_move_started` — dir_move journal entry
  with the trash dir as src/dst → `SKIPPED_IN_FLIGHT`, dir still present.
- `test_safe_delete_skips_when_v2_move_started_targets_trash` — v2 schema
  `move started` with `tmp_path` set, dst = trash path → SKIPPED.
- `test_safe_delete_blocks_concurrent_writer` — main thread holds
  LOCK_SH on `<journal>.lock`; spawn a thread that calls
  `safe_delete()` and assert it blocks until LOCK_SH is released. Mirrors
  the equivalent test for `is_path_in_flight` from #201.
- `test_two_concurrent_safe_deletes_serialize` — two threads both call
  `safe_delete()` on the same trash file. Expected: one returns `DELETED`,
  the other returns `MISSING` (the racing call saw it gone after acquiring
  its own LOCK_EX). Proves LOCK_EX serialization works.
- `test_safe_delete_after_done_entry_succeeds` — journal contains a `done`
  entry for the trash path; should not block deletion.
- `test_safe_delete_directory_releases_lock_before_rmtree` — instrument
  fcntl + rmtree calls; verify rmtree starts AFTER LOCK_UN. Proves the
  §3.3 lock-hold scope contract: lock release must precede the slow
  unlocked rmtree.
- `test_safe_delete_directory_lock_hold_bounded_by_rename` — monkeypatch
  shutil.rmtree to sleep 100ms; concurrently spawn a writer thread that
  calls _append_journal. The writer's append MUST complete in the same
  ballpark as the rename (microseconds), not after rmtree finishes. This
  is the load-bearing test for the atomic-rename pivot — without it the
  whole point of the design change is unverified.

### 6.3 Lock-hygiene tests

- `test_safe_delete_releases_lock_on_exception` — force the unlink (file
  case) AND the rename (dir case) to raise; confirm a subsequent
  `is_path_in_flight()` call doesn't block (lock released cleanly via
  context manager).
- `test_safe_delete_releases_lock_when_outside_trash_check_fails` —
  passing an outside-root path returns `OUTSIDE_TRASH` without the lock
  ever being acquired (no need to coordinate to reject); a subsequent
  `LOCK_EX` acquisition succeeds immediately.

### 6.3a Init-time orphan recovery tests

- `test_init_cleans_orphan_pending_delete_entries` — pre-seed
  `<trash_dir>/.pending-delete-aaaa` and `bbbb` (each populated with
  files); construct `TrashGC`; assert both gone, normal trash entries
  unaffected.
- `test_init_skips_cleanup_for_unrelated_dotfiles` — pre-seed `.gitkeep`,
  `.DS_Store`, `.pending-delete` (no suffix), and `.pending-deleted-x`
  (different suffix) in trash_dir. Construct `TrashGC`. None of these
  must be deleted — the prefix match is exactly `.pending-delete-` plus
  at least one suffix character.
- `test_init_continues_when_one_orphan_rmtree_fails` — pre-seed two
  orphans; monkeypatch `shutil.rmtree` to fail on the first call only;
  construct `TrashGC`; assert second orphan still removed and a WARNING
  was logged for the first.
- `test_init_aggregated_log_line_emitted` — pre-seed three orphans;
  capture INFO logs; assert the aggregate line
  `"trash GC init recovery: 3 orphans cleaned, 0 failed"` (or matching
  count format) appears.
- `test_init_handles_missing_trash_dir` — `trash_dir` doesn't exist on
  construction; init must not raise (creates the directory, scan finds
  zero entries, aggregate logs zero/zero).

### 6.4 Integration with existing rollback flow

- `test_rollback_after_safe_delete_sees_missing_path` — sequence:
  `safe_delete(trash_path)` returns DELETED; then attempt
  `RollbackExecutor.rollback_delete(operation)` → fails as expected with
  the standard "trash entry missing" path. No new state to verify; just
  prove the two paths compose without surprise.

### 6.5 What we deliberately don't test (out of scope per #202 §non-goals)

- Recursive in-flight detection for files inside a trash dir under deletion.
  `is_path_in_flight()` matches on the journal entry's src/dst exactly; if
  a future binary moves a file FROM inside a trash dir while GC is deleting
  the parent, that's an unsupported pattern. Documented as a known limitation.

---

## 7. Implementation order (steps)

Each step is a self-contained commit + green CI before the next.

1. **Outcome types + module skeleton** — `TrashDeleteResult` enum (six
   variants), `TrashDeleteOutcome` dataclass, `TrashGC.__init__` with
   `trash_dir` + optional `journal_path`. NO `safe_delete` body yet, NO
   init-time recovery yet. Tests assert types and constructor behavior
   only. Establishes the API surface for review before any locking or
   filesystem mutation lands.

2. **Init-time orphan recovery** (§3.4) — `__init__` scans for
   `.pending-delete-*` orphans and rmtrees them, with the WARNING-on-
   failure-continue pattern and aggregated INFO log. Tests from §6.3a.
   Lands BEFORE the directory delete path so by the time `safe_delete`
   produces orphans, the recovery exists to clean them.

3. **`safe_delete` file/symlink path** (§3.2) — fast-path for files and
   symlinks: validate → LOCK_EX → in-flight check → lexists → unlink →
   release. Tests from §6.1 covering the file/symlink subset, plus the
   `OUTSIDE_TRASH` rejection. NO directory support yet — that needs the
   staging-rename machinery in step 4.

4. **`safe_delete` directory path** (§3.3) — atomic-rename to
   `.pending-delete-<uuid4>` under LOCK_EX, then unlocked rmtree.
   `DELETED` vs. `DELETED_WITH_STAGING_FAILURE` outcome split. Tests
   from §6.1's directory subset.

5. **Race / coordination + lock-hygiene** — §6.2 + §6.3 thread tests:
   the load-bearing `test_safe_delete_directory_lock_hold_bounded_by_rename`
   that validates the atomic-rename pivot, the LOCK_SH-blocks-GC test,
   the two-concurrent-GCs test, and the lock-released-on-exception tests.

6. **Integration with `RollbackExecutor`** — §6.4 composition test.

7. **Operator-visibility doc** — short `docs/internal/F8-1-trash-gc.md`
   summarizing the API + lock protocol + outcome semantics + the orphan
   recovery contract, plus a reference link from PR #197's design doc.

---

## 8. Composition with adjacent surfaces

| Surface | How TrashGC composes |
|---------|----------------------|
| `is_path_in_flight()` | Called inside `safe_delete()` while holding the same `<journal>.lock` LOCK_EX; check result is durable for the deletion. |
| `is_trash_safe_to_delete()` | Existing predicate stays as a one-instant view (used by callers that ONLY need to inspect, not delete). `safe_delete()` is the new mutation entry point. |
| `durable_move` / `directory_move` | Both write `started` entries under LOCK_EX; `safe_delete` SKIPs while a move is in flight. |
| `sweep` | Sweep also takes LOCK_EX. `safe_delete` and sweep cannot run simultaneously — POSIX flock semantics serialize them. |
| `fo recover` | Read-only LOCK_SH reader; blocks `safe_delete` (LOCK_EX) for the duration of the read. Acceptable. |

---

## 9. Resolved design decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | New `src/undo/trash_gc.py` + `TrashGC` class (NOT extending `OperationValidator`) | Avoids the WRONG_ABSTRACTION (F8) of mixing predicate + mutation responsibilities on the same class. |
| 2 | Codified `TrashDeleteResult` enum + `TrashDeleteOutcome` dataclass (NOT `(bool, str)` tuple) | Type-safe; six outcomes (`DELETED`, `DELETED_WITH_STAGING_FAILURE`, `SKIPPED_IN_FLIGHT`, `MISSING`, `PERMISSION_ERROR`, `OUTSIDE_TRASH`) get real types so callers can match exhaustively. |
| 3 | Single-path `safe_delete` (NOT a batch API) | Simpler outcome reporting; lock-hold time stays per-path; batch wrapper is additive future work if a concrete caller appears. |
| 4 | Atomic-rename pivot for directories (NOT rmtree-under-lock + entry cap) | Lock-hold bounded by one syscall regardless of trash subtree size. Eliminates the worst-case starvation concern entirely. Trade-off: a new `DELETED_WITH_STAGING_FAILURE` outcome and init-time orphan recovery (§3.4). |
| 5 | `DELETED_WITH_STAGING_FAILURE` is a distinct outcome (NOT bundled into `DELETED`) | Surfaces partial state to operators; the user's path is gone but disk space hasn't been reclaimed yet. Next-init recovery owns the cleanup. |
| 6 | Eager init-time orphan recovery (NOT explicit `recover_pending()`) | Simpler caller contract: trash dir is always clean of GC-owned orphans on first `TrashGC` use. Tests pay the scan cost per construction (acceptable: scan is O(top-level entries)). |

---

## 10. Out of scope (documented limitations)

- **Recursive in-flight detection inside a trash directory under
  deletion**. `is_path_in_flight()` matches on the journal entry's src/dst
  exactly. If a future binary moves a file FROM inside a trash dir while
  GC is deleting that dir's parent, the GC won't detect the child move.
  This is an unsupported pattern (rollback restores FROM trash; it does
  not move files OUT of trash for arbitrary purposes). Document in the
  operator-visibility doc (step 7).

- **Lock contention vs other long readers**: `fo recover` (step 8 of
  #201) holds `LOCK_SH` for its journal read. While `fo recover` runs,
  GC's `LOCK_EX` waits. Acceptable: `fo recover` is a one-shot operator
  command, not a hot path.

- **Cross-filesystem trash dirs**: We assume `trash_dir` is on a single
  filesystem (the rename in §3.3 step 6 is single-fs by construction
  because `staging` is inside `trash_dir`). Mounting `trash_dir` as a
  composite of multiple filesystems would break the rename. Document
  as a configuration constraint.

---

**Tracks:** issue #202.
**Depends on:** #201 (PR #203 merged d0d6808d) — uses the lock-file model
(`<journal>.lock`), the §3.1 collapse identity inside `is_path_in_flight`,
and the v2 journal envelope.
**Round 1 review:** atomic-rename pivot for directory deletes (§3.3, §3.4,
§5.1, §6.1, §6.3a, §7 step 2 split out, §9 decisions 4–6).
**Last Updated:** 2026-04-25.
