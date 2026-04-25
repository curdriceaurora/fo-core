# F8.1 Trash GC API — Operator Reference

**Tracks**: issue #202.
**Design spec**: [F8-1-trash-gc-design.md](./F8-1-trash-gc-design.md).
**Depends on**: PR #203 (#201, F7.1 journal protocol).

Operator-facing summary of the `TrashGC` API: what calls return, how it
coordinates with the rest of the undo subsystem, and what to do when an
unusual outcome shows up in logs.

## Public surface

`src/undo/trash_gc.py`:

- `TrashGC(trash_dir, *, journal_path=None)` — constructs a GC instance
  scoped to a trash directory. Eagerly cleans `.pending-delete-*`
  staging orphans on every construction (§3.4 of the design spec).
- `TrashGC.safe_delete(path) -> TrashDeleteOutcome` — the single
  blessed deletion entry point. Validates the path is inside
  `trash_dir`, acquires `LOCK_EX` on `<journal>.lock`, checks the
  in-flight predicate against the durable_move journal, and either
  unlinks (file/symlink) or atomically renames the directory into a
  staging path before releasing the lock and rmtree-ing the staging
  unlocked.
- `TrashDeleteResult` (`StrEnum`) — six-variant outcome.
- `TrashDeleteOutcome` (frozen dataclass) — `result`, `path`, `reason`,
  optional `error`.

The class is intentionally tiny — one public method. All trash-deletion
callers (current and future GC drivers) MUST go through this API. Direct
`Path.unlink` / `shutil.rmtree` on trash entries bypasses the in-flight
check and re-introduces the F8 race the API exists to prevent.

## Outcomes

| Result | Path state after the call | What the operator should do |
|--------|---------------------------|------------------------------|
| `DELETED` | Gone | Nothing. Routine success. |
| `DELETED_WITH_STAGING_FAILURE` | Gone (rename succeeded), but a `.pending-delete-*` orphan survives in `trash_dir`. | Nothing immediate — next `TrashGC` construction's eager init recovery cleans the orphan. If the same orphan keeps surviving across multiple constructions, investigate the underlying `OSError` (logged at WARNING with `exc_info`). |
| `SKIPPED_IN_FLIGHT` | Untouched | Wait. An in-flight `move` / `dir_move` is currently using this path. The next sweep or rollback completion will free it; the GC driver retries on its next pass. |
| `MISSING` | Untouched (was already absent) | Nothing. Idempotent no-op. |
| `PERMISSION_ERROR` | Untouched | Investigate the `error` field. Common causes: read-only filesystem, restrictive ACL, target file has the immutable attribute. |
| `OUTSIDE_TRASH` | Untouched | The caller passed a path outside the configured trash root. WARNING-logged with the resolved path. Indicates either a configuration bug (wrong `trash_dir`) or a malicious / buggy caller — investigate. |

## Lock protocol

`safe_delete` takes `LOCK_EX` on `<journal>.lock` (the same lock subject
introduced by F7.1 #201 §6.1) for the duration of:

- the `is_path_in_flight` check;
- `os.path.lexists`;
- `unlink` (file/symlink) OR `os.rename` to staging (directory).

The lock is released BEFORE the `shutil.rmtree` of the directory
staging path, so concurrent journal writers (rollback's
`durable_move._append_journal`) are blocked for at most one rename
syscall regardless of trash subtree size. This is the §3.3
atomic-rename pivot from the round-2 design review.

The lock-hold contract is verified by
`tests/undo/test_trash_gc.py::TestSafeDeleteRaceCoordination::test_safe_delete_directory_lock_hold_bounded_by_rename`.

## Init-time orphan recovery

Every `TrashGC` construction scans `trash_dir` for entries matching
`.pending-delete-*` and `rmtree`s them (§3.4). The recovery is
LOCKLESS — these names are GC-owned and isolated from the user's path
namespace, so no journal coordination is needed.

A clean construction emits no log line. If any orphans were processed,
an aggregated `INFO` line lands:

```text
trash GC init recovery: 3 orphans cleaned, 0 failed
```

Per-orphan `rmtree` failures are logged at `WARNING` with `exc_info=True`
and the loop continues so one stuck entry doesn't block the rest.

## Composition with adjacent surfaces

| Surface | Interaction |
|---------|-------------|
| `is_path_in_flight()` | Reused as the in-flight predicate inside `safe_delete`'s LOCK_EX context (via the `_path_in_flight_from_entries` helper to avoid the LOCK_SH-vs-LOCK_EX deadlock). |
| `is_trash_safe_to_delete()` | Existing predicate kept as a one-instant view for read-only callers. `safe_delete` is the new mutation entry point. |
| `durable_move` / `directory_move` | Both write `started` entries under `LOCK_EX`; `safe_delete` SKIPs while a move is in flight. |
| `sweep` (#201) | Also takes `LOCK_EX` on the same lock file. POSIX flock semantics serialize them — `safe_delete` and `sweep` cannot run simultaneously. |
| `fo recover` (#201) | Read-only `LOCK_SH` reader; blocks `safe_delete` for the duration of the recovery preview. Acceptable: `fo recover` is one-shot operator visibility, not a hot path. |

## Documented limitations

- **Recursive in-flight detection inside a trash directory under
  deletion**: `is_path_in_flight()` matches on the journal entry's
  `src` / `dst` exactly. If a future binary moves a file FROM inside a
  trash dir while GC is deleting that dir's parent, the GC won't detect
  the child move. This is an unsupported pattern (rollback restores
  FROM trash; it doesn't move files OUT of trash for arbitrary
  purposes).
- **Cross-filesystem trash dirs**: `trash_dir` MUST be on a single
  filesystem. The atomic rename from `target` to
  `<trash_dir>/.pending-delete-<uuid>` is single-fs by construction;
  mounting `trash_dir` as a composite of multiple filesystems would
  break the rename with `EXDEV`.
- **No batch API**: `safe_delete` is single-path. A `safe_delete_many`
  is additive future work for when a concrete caller needs it.

## Out of scope (not yet wired up)

This PR ships the API. Adoption by the actual trash GC driver (a
retention-policy scheduler that calls `safe_delete` for each expired
entry) is downstream future work, tracked separately. Until then,
`safe_delete` is the entry point that future GC implementations MUST
use.

---

**Last Updated**: 2026-04-25.
