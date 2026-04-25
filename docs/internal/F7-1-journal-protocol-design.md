# F7.1 Journal Protocol — Design Spec

Tracks: issue #201 (follow-up to PR #197).
Supersedes: `docs/internal/F7-8-crash-recovery-model.md` §6 non-goals.

## 1. Goal

Lift the durable-move journal from "ad-hoc file the helper happens to write" to a
first-class protocol surface. Close the five gaps PR #197 review exposed:

1. Collapse-key conflation — mixed-op same-path entries mask each other.
2. Parse-time AttributeError on non-object JSON values.
3. Live-journal rewrite (sweep truncate+write) loses retained entries on crash.
4. STARTED state is ambiguous without content comparison (PR #197 retained-forever workaround).
5. Dir-move is coordination-only with no operator-visible recovery path.

Non-goals from #201:

- Trash GC deletion API (owned by #202).
- Non-undo `shutil.move` sites in roadmap inventory.
- DB / config changes except shared test helpers.

---

## 2. Journal schema — v2

Superset of v1 (op=move/dir_move, src, dst, state). Additions marked `NEW`.

| Field | Type | Required | v1 | Purpose |
|---|---|---|---|---|
| `schema` | `int` | yes (v2) | NEW | Journal-record schema version. `2` for this spec; readers accept `1` and up-convert. |
| `op` | `str` | yes | ✓ | Operation type — `move`, `dir_move`; future ops share the journal. |
| `op_id` | `str \| None` | optional | NEW | Unique per-operation UUID (v2 writers). v1 records have `None` here; see §3.1 collapse rules. |
| `src` | `str` | yes | ✓ | Absolute normalized source path. |
| `dst` | `str` | yes | ✓ | Absolute normalized destination path. |
| `state` | `str` | yes | ✓ | One of `started`, `copied`, `done`. (`copied` only used by `op=move`.) |
| `tmp_path` | `str` | yes for `op=move` EXDEV started; else no | NEW | Absolute path of the tmp file/symlink used by the EXDEV path — required to exist on disk from before the started-write until consumed by `os.replace`. Letters sweep disambiguate STARTED via `lexists(tmp_path)`. |
| `ts` | `float` | no | NEW | Epoch seconds of the write. Operator diagnostics only. |
| `host_pid` | `int` | no | NEW | Writer process PID. **Diagnostic only — never use for liveness checks** (PID reuse makes that unreliable per F2). |
| `_raw` | (internal) | — | — | Python-side only (not serialized). Unknown-op entries retain their full raw JSON line here so compaction can re-serialize them verbatim — a future binary's handler receives all fields a v1/v2 binary might drop. |

### Back-compat

Readers MUST accept v1 records (missing `schema` field implies v1). `op_id` is
**optional** — v1 records lack it, and the collapse-key reducer handles both
cases (§3.1). v1 `op=move started` records lack `tmp_path` and so cannot be
disambiguated by the new protocol; they retain PR #197 behavior: operator-
visible retain with warning (see §5.1 "v1 `started`" row).

Writers in this branch produce v2 exclusively. Compaction re-serializes v1
retained records as v1 (no synthetic `op_id` injection) so their identity stays
stable across sweeps. Unknown-op entries (any `op` not in `_KNOWN_OPS`) are
re-serialized verbatim from their captured `_raw` line so forward-compat metadata
is preserved.

---

## 3. Operation identity and collapse keying

### 3.1 Collapse key

All latest-state reducers (sweep `_reconcile_entries`, `is_path_in_flight`)
collapse by an operation identity derived from the entry. The rules, in order:

1. **v2 known-op record WITH `op_id`**: identity is `("v2", op, op_id)`.
   Uniquely identifies one invocation; different retries of the same move do
   not collapse.
2. **v1 known-op record** (`schema` absent): identity is `("v1", op, src, dst)`.
   Path-keyed fallback matching PR #197 behavior.
3. **v2 known-op record WITHOUT `op_id`**: **malformed.** Parse-time rejection
   per §4.1 — logged at WARNING and dropped. v2 writers in this branch always
   emit `op_id`; a known-op v2 record missing it is either corrupt or came from
   a misconfigured external writer, and treating it as v1 identity would
   silently collapse invocations that should stay distinct. Safer to drop.
4. **Unknown-op record** (op not in `_KNOWN_OPS`): identity is
   `("unknown", op, _hash16(_raw))` where `_hash16` is the first 16 hex chars
   of `sha256(_raw.encode()).hexdigest()` — derived from the full raw line. Rationale: a future op's correctness may
   depend on fields our parser doesn't know about. Collapsing two unknown-op
   records by `(op, src, dst)` alone could conflate invocations that a future
   binary's handler would distinguish via its own fields. Hashing the raw line
   guarantees different serialized payloads stay distinct; compaction
   re-serializes them verbatim from `_raw` (§4.2).

```python
def _identity(entry: _JournalEntry) -> tuple:
    if entry.op in _KNOWN_OPS:
        if entry.schema == 2:
            # v2 writers ALWAYS emit op_id; see parse rule §4.1 rejection #8.
            assert entry.op_id is not None, "invariant: v2 known-op has op_id"
            return ("v2", entry.op, entry.op_id)
        # v1: path-keyed fallback for PR #197 back-compat.
        return ("v1", entry.op, entry.src, entry.dst)
    # Unknown op: raw-line hash so different payloads never collapse.
    assert entry._raw is not None, "invariant: unknown-op records retain _raw"
    return ("unknown", entry.op, _hash16(entry._raw))
```

This closes codex `iy4u` (same-path different-op masking) AND adds
retry-identity for v2 records AND prevents future unknown ops from silently
collapsing with each other via a v2 parser that doesn't understand their
additional fields.

### 3.2 Progression rule

Within a single `op_id`, states progress monotonically: `started` → `copied`
(op=move only) → `done`. `_reconcile_entries` uses the last state per identity. A
later `done` supersedes an earlier `started`/`copied` of the same op_id.

### 3.3 Mixed-op example

Journal:

```jsonl
{"schema": 2, "op": "move",     "op_id": "A", "src": "/a", "dst": "/b", "state": "started"}
{"schema": 2, "op": "dir_move", "op_id": "B", "src": "/a", "dst": "/b", "state": "started"}
{"schema": 2, "op": "dir_move", "op_id": "B", "src": "/a", "dst": "/b", "state": "done"}
```

Post-reconcile: two identities — `(move, A, /a, /b)` retained for sweep, `(dir_move,
B, /a, /b)` dropped. Pre-fix (PR #197), the second and third rows would have
overwritten the first, dropping the `move` recovery metadata.

---

## 4. Parse contract

`_parse_journal_text` and `_read_journal` MUST accept one JSONL line per record and
for each line produce exactly one of: valid entry, logged-and-skipped malformed
entry. No input (inside a total journal size cap — see §6.5) may cause the parser
to raise.

### 4.1 Rejection cases (logged + skipped)

1. JSON parse error (v1 + v2).
2. `json.loads` returns non-object (`null`, `[]`, scalars, strings). **codex iy4w**
3. Missing required field (op, src, dst, state).
4. Non-string value in a required-string field.
5. `op` not in `_KNOWN_OPS` AND `schema` is v1 — v1 records are expected to be
   `op=move`; anything else on a v1 record is garbage. v2 unknown ops are NOT
   rejected; they're retained with raw-payload preservation (§4.2, §5.1).
6. `schema` present but not a positive int.
7. Line > 64 KiB (prevents pathological payload abuse).
8. **v2 known-op record WITHOUT `op_id`.** v2 writers in this branch always
   emit `op_id` per §3.1. A known-op v2 record missing it is either corrupt
   or came from a misconfigured external writer; treating it as v1 identity
   would silently collapse invocations that should stay distinct. Rejection
   keeps the identity rule honest.
9. **v2 `op=move` `state=started` record WITHOUT `tmp_path`.** The tmp-exists
   invariant (§7.1) requires `tmp_path` on every v2 `move started` record —
   without it sweep cannot safely disambiguate pre-replace vs post-replace
   crashes. Writers emit `tmp_path` unconditionally for this combination;
   rejection prevents a corrupt/external record from tricking sweep into
   unlink-src data loss.

Rejected lines are logged at WARNING with the offending line truncated to 200
chars. Sweep continues to the next line.

### 4.2 Unknown future fields

For **known ops** (`move`, `dir_move`): extra JSON fields are ignored — the
parser returns a `_JournalEntry` populated from the known-v2 attribute set only.
Those ops have stable schemas defined here; extras are genuinely unknown noise.

For **unknown ops** (anything not in `_KNOWN_OPS`): the parser preserves the full
raw JSON line on the `_JournalEntry._raw` attribute. Compaction writes these
entries back verbatim using `_raw`, so a future binary with a handler for the
op receives ALL fields the v1/v2 writer persisted — NOT just the
`(op, src, dst, state)` core that v2's parser happens to recognize. This fixes
a scope creep the original spec missed: dropping extras would silently destroy
metadata the future handler may need.

---

## 5. Sweep / reconciliation

### 5.1 Recovery state table (v2)

`lexists_*` columns are `src`, `dst`, `tmp` file-system presence. `—` means
irrelevant to the action.

**Invariant underpinning the disambiguation** (see §7): for every v2 `op=move`
`started` record, `tmp_path` is created on disk **before** the started entry is
written, and no code path removes `tmp_path` on failure — only `os.replace` or
sweep touches it afterward. Therefore `lexists(tmp_path) == False` for a v2
`move started` record is a **positive signal** that `os.replace` ran and
consumed the tmp. Without this invariant the table's "tmp absent → post-replace"
rows would be unsafe (the blocking concern raised in review).

| op | state | schema | lexists(tmp) | lexists(dst) | lexists(src) | Action | Rationale |
|---|---|---|---|---|---|---|---|
| `move` | `started` | v2 | true | — | — | Delete tmp → drop entry | tmp still present ⇒ pre-`os.replace` crash; dst untouched by our txn. |
| `move` | `started` | v2 | false | true | true | Unlink src → fsync(src.parent) → drop | tmp consumed by `os.replace` + pre-`copied`-log crash; finish as copied. |
| `move` | `started` | v2 | false | true | false | Drop | Post-unlink pre-`done` crash; already consistent. |
| `move` | `started` | v2 | false | false | true | Retain + warn | Unusual — `os.replace` ran but dst has since been removed out-of-band. Operator inspection: unlink src would destroy the only remaining copy. |
| `move` | `started` | v2 | false | false | false | Drop + warn | Catastrophic (dst consumed + out-of-band deleted + src gone). No safe action. |
| `move` | `started` | v1 | — | — | — | Retain + warn | v1 records lack `tmp_path`; cannot disambiguate. Matches PR #197 behavior. |
| `move` | `copied` | any | — | true | true | Unlink src → fsync → drop | Existing PR #197 behavior. |
| `move` | `copied` | any | — | true | false | Drop | Existing PR #197 behavior. |
| `move` | `copied` | any | — | false | — | Retain + warn | Existing codex hGWW guard. |
| `move` | `done` | any | — | — | — | Drop | Already reconciled. |
| `move` | unknown state | any | — | — | — | Retain + warn | Known op but unrecognized state value — preserve for a future binary that may understand it. |
| `dir_move` | `done` | any | — | — | — | Drop | Coordination complete. |
| `dir_move` | `started` | any | — | — | — | Drop + warn | shutil.move crash; operator must inspect on-disk state. |
| `dir_move` | unknown state | any | — | — | — | Retain + warn | Same reasoning as `move` unknown state. |
| unknown `op` | any | any | — | — | — | Retain + warn | codex hdFb — future binary's handler will process. Entry re-serialized verbatim from `_raw` on compaction (§4.2). |

**Key win**: rows 1–5 replace PR #197's unconditional "retain STARTED as ambiguous"
with deterministic recovery in 4 of the 5 concrete sub-cases, provided the §7
writer protocol holds tmp_path existence as an invariant. Only the unusual
dst-absent+src-present case remains retain-only, flagged with an operator-actionable
warning.

**Safety fallback**: if `tmp_path` is missing from a v2 `move started` record for
any reason (bug, corruption), sweep treats it as a v1 record (retain + warn)
rather than applying the "tmp absent → post-replace" inference. The disambiguation
rule is gated on *both* `schema == 2` AND `tmp_path is not None`.

### 5.2 Tmp cleanup

When row 1 applies (tmp exists), sweep unlinks tmp AFTER determining recovery
action but BEFORE dropping the entry. Unlink failures are logged + entry retained
(same pattern as existing copied-state OSError-retain).

### 5.3 Dir_move semantics (decision)

**Decision: keep `dir_move` coordination-only.** True durable directory recovery
is out of F7.1 scope — it would require a staged copy + atomic swap of arbitrary
directory trees, which is a substantially larger design. The issue body lists this
as an explicit decision point; this spec makes the call.

Consequence: `dir_move started` entries on sweep are dropped with an operator
warning (§5.1 row 11). Operator visibility (§8) surfaces the warning history so
partially-moved directories are discoverable.

### 5.4 Unknown-op retention

Preserves codex hdFb behavior. Future binaries that know the op resolve the
entry when their sweep runs. No change from PR #197.

---

## 6. Atomic journal compaction + lock-file coordination

Two blocking correctness issues flagged in the round-1 design review:

1. PR #197 sweep uses `fh.truncate()` + `fh.write(...)` on the LIVE journal while
   holding `LOCK_EX`. A crash between truncate and write-complete leaves the
   journal truncated to zero, destroying all retained entries (CodeRabbit round-10).
2. If the journal itself is the lock subject AND compaction `os.replace`s it with
   a new inode, a concurrent appender that opened the OLD inode before the
   replace blocks on the old inode's lock, then after sweep releases, appends to
   the UNLINKED old inode. **Lost append.** (Round-1 review, blocking.)

The fix for both: **separate lock file + atomic journal compaction**.

### 6.1 Lock file

A sibling `<journal>.lock` is the coordination subject for all protocol
operations. It is:

- Created (if absent) by any reader or writer on first access.
- **Never replaced or unlinked** during normal operation — stays at a stable inode
  for the lifetime of the state directory.
- Empty (zero bytes); the file itself carries no data. Only its inode exists as
  a `fcntl.flock` target.
- Located alongside the journal (same directory) so one lock covers both the
  journal file and any compaction tmp.

All `fcntl.flock` calls (append, sweep, `is_path_in_flight`) take locks on
`<journal>.lock` — not on `<journal>`. `os.replace` on the journal therefore
cannot invalidate a held lock.

### 6.2 Compaction algorithm

1. Open `<journal>.lock` (creating if absent) → acquire `LOCK_EX` on its fd.
2. Open `<journal>` for reading; parse + reconcile → `retained: list[_JournalEntry]`.
3. If `retained == []` and the on-disk journal is already empty: release lock,
   return (no write needed).
4. Write retained entries to `<journal>.<pid>.compact.tmp` in the same directory,
   via `open("x")` (fail if it already exists).
5. `write + flush + fsync(tmp_fd)`.
6. `fsync_directory(<journal>.parent)` — make the tmp's dir entry durable.
7. `os.replace(tmp, <journal>)` — atomic inode swap for the journal file.
8. `fsync_directory(<journal>.parent)` — make the replace durable.
9. Release `LOCK_EX` (close lock fd's flock, leave the lock file on disk).

Because `<journal>.lock` is never replaced, step 7's inode swap doesn't
invalidate any flock held by appenders waiting on the same lock file.

### 6.3 Crash window analysis

| Crash at step | On-disk state | Recovery |
|---|---|---|
| 1–3 | Journal unchanged | Next sweep re-reconciles; idempotent. |
| 4 mid-write | Stale compact tmp; journal intact | Next sweep's step 4 sees `FileExistsError` via `open("x")` → stale-tmp handler (§6.4) removes + retries once. |
| 5 mid-fsync | Stale compact tmp; journal intact | Same recovery as 4. |
| 6 before replace | Stale compact tmp; journal intact | Same recovery as 4. |
| 7 mid-replace | Atomic: either journal = old content, or = tmp content | Either state is consistent; next sweep re-reconciles from whichever version landed. |
| 8 before fsync_directory | Replace landed in page cache but not directory entry | On reboot, journal may revert to old; next sweep re-reconciles (idempotent). |
| After 8 | Compacted | Done. |

No state leaves the journal as `zero-bytes + retained-entries-pending`. No
window allows an appender to write to an unlinked inode.

### 6.4 Stale compact-tmp cleanup

Step 4's `open("x", ...)` raises `FileExistsError` if a prior crashed sweep
left a tmp. Handler: log warning, unlink the stale tmp, retry step 4 **once**.
Not a loop — two `FileExistsError`s in a row indicates a pathology (e.g.
permissions) that sweep shouldn't paper over.

### 6.5 Lock invariants

- All protocol operations acquire their lock on `<journal>.lock`, NOT on
  `<journal>`. The journal file may be replaced; the lock file is stable.
- `_append_journal` (POSIX) takes `LOCK_EX` on `<journal>.lock` for each append.
- Sweep / compaction takes `LOCK_EX` for steps 1–9.
- `is_path_in_flight` takes `LOCK_SH` on `<journal>.lock` — which blocks any
  `LOCK_EX` holder (writer or compaction) from progressing, AND blocks the
  reader while any `LOCK_EX` is held. This is the intended F8 semantics: readers
  never observe a journal state mid-append or mid-compaction.

POSIX flock semantics confirm this: `LOCK_SH` is granted only when no `LOCK_EX`
is held, and `LOCK_EX` requires no other lock (shared or exclusive) to be held.
So shared readers and exclusive writers cannot be simultaneously active on the
same lock file. Corrects the §6.4 wording in the v1 draft ("reads block during
compaction but not during appends" was wrong — reads also block during appends,
which is desirable).

### 6.6 Size cap

Journals >16 MiB trigger a WARNING log and compaction skips to avoid unbounded
memory + write amplification. The size cap is belt-and-suspenders: steady state,
the journal is bounded by the number of concurrently in-flight moves (currently
≤1 per CLI invocation).

---

## 7. STARTED disambiguation via `tmp_path`

PR #197's retain-as-ambiguous behavior for `move started` is the direct consequence
of sweep not knowing whether `os.replace` had completed. v2 adds `tmp_path` to
`started` records; sweep then distinguishes:

- `lexists(tmp_path) == True` — pre-replace crash (tmp is orphan).
- `lexists(tmp_path) == False` — post-replace crash (replace has run, tmp consumed).

**This disambiguation is only safe if tmp is guaranteed to exist on disk between
the started journal write and the `os.replace` consumption.** Otherwise a crash
window where tmp hasn't been created yet (but the journal has been written)
would be misread as post-replace and sweep would unlink src incorrectly. Round-1
review flagged this as a blocking issue.

### 7.1 The tmp-exists invariant

For every v2 `op=move` `started` journal record, the following invariant MUST
hold:

> From the moment the `started` record is durable on disk, `tmp_path` exists on
> disk **and its directory entry is durable**, and remains that way until
> either `os.replace` atomically consumes it (swap to dst) OR sweep deletes it
> during recovery.

The durability qualifier is critical. If `tmp_path` exists only in the page
cache but its directory entry hasn't been flushed, a power-loss crash on the
write path ("create tmp → journal append+fsync → crash before dir fsync")
leaves the journal record on disk while the tmp file vanishes on reboot. Sweep
would then observe `lexists(tmp_path) == False` and misread as "post-replace
completed" → unlink src → data loss.

Three rules enforce the invariant:

1. **tmp is created BEFORE the `started` journal write.** For both regular
   files (`NamedTemporaryFile(delete=False)` creates + closes an empty file)
   and symlinks (`os.symlink(target, tmp)` is a single atomic syscall), the
   writer calls the tmp-creating operation first, then journals `started`.
   A crash between the two leaves an orphan tmp with no journal entry — that's
   operator debris, not a sweep concern.
2. **`fsync_directory(<dst.parent>)` runs between tmp creation and the
   `started` journal write.** Makes the tmp's directory entry durable so a
   later reboot cannot lose tmp while retaining the started record. Without
   this fsync the §5.1 "tmp absent → post-replace inference" is unsafe under
   power loss — the blocking concern raised in round-2 review.
3. **No code path removes tmp on exception.** Specifically, the current
   `_durable_cross_device_move` `except BaseException: tmp_path.unlink()`
   handler is **removed**. Exceptions propagate unchanged; tmp persists for
   sweep to handle. Callers that retry get a new `tmp_path` (uniquely
   suffixed); the orphan tmp from the failed attempt is cleaned by the next
   sweep.

### 7.2 Updated writer sequence (EXDEV, regular file)

1. Allocate + **create** empty tmp via `NamedTemporaryFile(delete=False)`.
2. **`fsync_directory(<dst.parent>)`** — make the tmp file's directory entry
   durable before the journal can claim tmp exists. (Round-2 blocking fix.)
3. `_append_journal(started, op_id=<uuid>, tmp_path=<abs>)`.
4. `shutil.copyfile(src, tmp_path)` + `shutil.copystat(src, tmp_path)`.
5. `fsync(tmp_fd)` + `fsync_directory(<dst.parent>)`.
6. `os.replace(tmp_path, dst)` — tmp is consumed atomically into dst.
7. `fsync_directory(<dst.parent>)`.
8. `_append_journal(copied, op_id=<same>)`.
9. `os.unlink(src)`.
10. `fsync_directory(<src.parent>)`.
11. `_append_journal(done, op_id=<same>)`.

### 7.3 Updated writer sequence (EXDEV, symlink)

1. Compute `target = os.readlink(src)`.
2. Generate `tmp_path` (PID + random suffix).
3. `os.symlink(target, tmp_path)` — atomic, creates the symlink as tmp.
4. **`fsync_directory(<dst.parent>)`** — make the tmp symlink's directory
   entry durable before the journal can claim tmp exists. (Round-2 blocking
   fix.)
5. `_append_journal(started, op_id=<uuid>, tmp_path=<abs>)`.
6. `os.replace(tmp_path, dst)`.
7. `fsync_directory(<dst.parent>)`.
8. `_append_journal(copied, op_id=<same>)`.
9. `os.unlink(src)`.
10. `fsync_directory(<src.parent>)`.
11. `_append_journal(done, op_id=<same>)`.

A crash between the tmp-creating syscall and the pre-started `fsync_directory`
(steps 1–3 regular, 1–4 symlink) leaves an orphan tmp with no journal entry —
operator debris, not sweep-visible. A crash anywhere from the `started` append
onward leaves a v2 record where `tmp_path` exists **durably** on disk and
sweep can disambiguate correctly.

### 7.4 Exception-cleanup removal

The current `except BaseException: tmp_path.unlink()` block in
`_durable_cross_device_move` is **deleted**. Rationale: cleaning tmp on
exception breaks the §7.1 invariant (tmp would be absent between the journal
started write and a later sweep, indistinguishable from "post-replace
completed"). Retries get fresh tmp paths (random suffix in regular-file case;
PID+random in symlink case) so accumulated orphans are bounded, and sweep
always deletes matching orphan tmps on the next run.

### 7.5 Crash-point catalog

Mechanically derivable from §7.2 and §7.3. The §5.1 recovery state table covers
all boundaries.

---

## 8. Operator visibility

Scoped down per round-1 review: this PR adds only `fo recover`. `fo doctor`
integration (the command exists at `src/cli/doctor.py`) is a reasonable
follow-up but kept out of this PR to minimize scope.

### 8.1 `plan_recovery_actions` — shared pure planner

Round-2 review flagged: a "report-only mode" on the sweep function risks
accidental sharing of mutating code paths. Fix: extract a pure planner that
both sweep and `fo recover` call.

Signature:

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class _PlannedAction:
    identity: tuple              # §3.1 collapse key
    entry: _JournalEntry         # the reconciled entry
    verb: Literal[               # sweep decision from §5.1
        "drop",
        "retain",
        "drop_tmp_then_drop",    # row 1: unlink tmp, drop entry
        "unlink_src_then_drop",  # rows 2, 7: unlink src + fsync, drop entry
    ]
    reason: str                  # human-readable for logs / CLI rendering
    needs_warning: bool          # log a WARNING on retain/drop-warn rows

def plan_recovery_actions(
    entries: list[_JournalEntry],
    fs_observer: Callable[[str], bool] = os.path.lexists,
) -> list[_PlannedAction]: ...
```

**Pure:** no file-system mutation. `fs_observer` defaults to `os.path.lexists`
(reads on-disk state to drive §5.1 disambiguation) but can be stubbed in tests
to exercise every row deterministically.

**Both call sites use the same function:**

- `sweep()` calls `plan_recovery_actions(entries)`, then a separate
  `_apply_planned_actions(plan, journal)` executes each action's verb under
  `LOCK_EX`.
- `fo recover` calls `plan_recovery_actions(entries)`, then renders the
  plan as a table (no execution).

Regression test runs both call sites on the same input and asserts the CLI's
rendered plan matches the executor's observed actions row-for-row.

### 8.2 `fo recover`

CLI command (top-level, not nested under `fo undo`, to avoid restructuring the
existing flat `fo undo` command) that:

1. Reads the journal under `LOCK_SH` (on `<journal>.lock` per §6.1).
2. Calls `plan_recovery_actions(entries)` (§8.1) — pure, no mutation.
3. Prints a table of retained / actionable entries with `op`, `state`, `src`,
   `dst`, `tmp_path` (when present), and the planned verb + reason.
4. For v2 `move started` entries the rendered reason includes the
   disambiguation tier (`[pre-replace]` / `[post-replace]` / `[v1-ambiguous]`).
5. Exits `0` if the plan has no actionable entries (empty OR all-`drop`),
   `1` if any non-`drop` action would be taken (so scripts can detect
   "needs cleanup").

Use case: after a crash, an operator runs `fo recover` to see what the next
sweep would do — no mystery WARNINGS in logs.

### 8.3 Structured log fields

All journal warnings/errors log with consistent keyword fields: `op`, `op_id`,
`state`, `src`, `dst`, `tmp_path` (when applicable). Enables grep-based
post-mortem across a crash / retry cycle.

---

## 9. Test plan

### 9.1 Schema + parse

- v1 record without `schema` field accepted.
- v2 record round-trips through parse → entry → serialize.
- Malformed JSON logged + skipped.
- Non-object JSON (`null`, `[]`, `"str"`, `42`) logged + skipped — **codex iy4w**.
- Missing required field logged + skipped.
- Oversized line (>64 KiB) logged + skipped.
- Unknown future field ignored.

### 9.2 Collapse key

- Same-path different-op records don't mask each other — **codex iy4u**.
- Same-path same-op different op_id records treated as distinct identities.
- Same op_id progression: started → copied → done collapses to done.

### 9.3 STARTED disambiguation

Tests the §5.1 table rows 1–6:

- `started` with `lexists(tmp_path) = true` → tmp unlinked, entry dropped, src
  + dst preserved (row 1).
- `started` with `lexists(tmp_path) = false` + `lexists(dst) = true` +
  `lexists(src) = true` → src unlinked, fsync(src.parent), entry dropped (row 2).
- `started` with `lexists(tmp_path) = false` + `lexists(dst) = true` +
  `lexists(src) = false` → drop (row 3).
- `started` with `lexists(tmp_path) = false` + `lexists(dst) = false` +
  `lexists(src) = true` → retain + operator warning (row 4).
- `started` with all paths absent → drop + warn (row 5).
- v1 `started` (no `tmp_path` field) → retain (PR #197 compat; row 6).

**Invariant tests** (critical — these protect the round-1 + round-2 review
fixes):

- Writer creates tmp BEFORE journal started: inspect journal between start of
  `durable_move` and first fsync — at any observation where journal contains a
  v2 `started` record, `tmp_path` MUST exist on disk.
- **Parent-dir fsync before started** (round-2 blocking fix): instrument the
  writer's syscall sequence (monkeypatch `fsync_directory` + `_append_journal`
  to append to a trace list). Assert that for the EXDEV regular-file and
  symlink paths, the trace contains `fsync_directory(dst.parent)` at least
  once BEFORE the `_append_journal(started, ...)` call. Without this
  ordering the tmp-exists invariant is not crash-durable.
- Exception-cleanup removal: monkeypatch `shutil.copyfile` to raise after
  journal started. Assert tmp still on disk after the exception propagates.
  Sweep then disambiguates correctly (row 1).
- Symlink variant: same invariant test using `os.symlink`-backed tmp.
- Safety fallback: if a v2 `started` record is written WITHOUT `tmp_path`
  (bug/corruption), the parser rejects it per §4.1 rule 8. Direct unit test on
  `_parse_journal_text` with a hand-crafted v2 record missing `op_id` OR
  `tmp_path` (where required).

### 9.4 Atomic compaction + lock file

- Compaction replaces via compact-tmp + `os.replace`, NOT live truncate.
- Stale compact-tmp from prior crashed sweep handled (log + retry once).
- Crash simulation (monkeypatch `os.replace` to raise after compact-tmp write)
  → journal retains original content, next sweep completes successfully.
- All locks are on `<journal>.lock` (stable inode), never on `<journal>`
  (which gets replaced). Regression test: hold `LOCK_EX` on lock file in main
  thread, start compaction in worker, `os.replace` the journal underneath →
  worker's lock is still valid, subsequent append lands in the new journal,
  NOT an unlinked inode. This directly covers the round-1 review blocking case.
- `<journal>.lock` is never unlinked during normal operation.
- `LOCK_EX` held throughout compaction; concurrent `_append_journal` AND
  `is_path_in_flight` both block.
- `is_path_in_flight` with `LOCK_SH` blocks on any `LOCK_EX` (appender OR
  compaction) — verifies the corrected §6.5 wording.
- Journal >16 MiB triggers size-cap warning + skips compaction.

### 9.5 Dir_move

- `dir_move started` dropped with warning on sweep.
- `dir_move done` dropped silently.
- Existing directory-restore path still works end-to-end.

### 9.6 Operator visibility

- `fo recover` with empty journal → exits 0, prints "no retained entries".
- `fo recover` with retained entries → exits 1, prints formatted table.
- `fo recover` per-row hint matches the §5.1 disambiguation tier for the
  on-disk observation (tmp present / tmp absent + src present / etc.).
- `plan_recovery_actions(entries) -> list[_PlannedAction]` is pure: given
  identical inputs it returns identical output with no file-system mutation.
  Sweep's mutation path calls `plan_recovery_actions` then executes each
  returned action; `fo recover` calls `plan_recovery_actions` and renders
  without executing. Regression test runs both call sites on the same input
  and asserts the CLI's rendered plan matches the planner's return value
  verbatim — guarantees there's no "report-only mode" branch that could drift
  from sweep's behavior.

---

## 10. Non-goals reiterated

- Trash GC deletion API → #202.
- True durable directory recovery → deferred; documented §5.3.
- Non-undo shutil.move sites → separate issues per roadmap.

---

## 11. Implementation order (TDD)

1. **Schema v2 parser + `_raw` preservation for unknown ops** + round-trip tests
   (§4, §9.1). Closes codex `iy4w` (dict-type validation).
2. **`plan_recovery_actions` pure planner** (§8.1). Introduced BEFORE the
   collapse-key and writer-protocol changes so each subsequent step adds rows
   to its coverage rather than touching sweep's mutation path. Sweep refactors
   to call the planner + a new `_apply_planned_actions` executor; behavior
   identical to PR #197 at this step.
3. **Collapse-key refactor** inside the planner + tests (§3, §9.2). Closes
   codex `iy4u`.
4. **Lock-file extraction** (§6.1): all flock operations move from `<journal>`
   to `<journal>.lock`. Regression test covers the round-1 review blocking
   case (replace-under-held-lock).
5. **Atomic compaction** via compact-tmp + `os.replace` (§6.2–6.4) + crash-window
   tests (§9.4).
6. **`tmp_path` field + writer-protocol change** (§7): tmp created before
   started, `fsync_directory(<dst.parent>)` before the started journal write,
   no exception cleanup. STARTED disambiguation tests (§9.3) including the
   tmp-durability and invariant tests.
7. **Dir_move decision locked in** (§5.3) + existing tests adjusted (§9.5).
8. **`fo recover` CLI** + structured log fields (§8) + tests (§9.6).

Each step is a self-contained commit + CI green before the next. The
`tmp_path` / writer-protocol change (step 6) is the highest-risk step and
goes last among protocol changes so the earlier refactors (planner, collapse-
key, lock file, atomic compaction) land against PR #197's writer and don't
couple refactor risk with behavior-shift risk.

---

## 12. Self-review (per writing-plans skill)

**Spec coverage:** every item in #201's acceptance criteria is bound to a §:
  - Protocol spec/table → §2, §5.1
  - Op-separated recovery → §3, §9.2
  - Malformed-line tolerance → §4
  - Crash-safe compaction → §6
  - Automated STARTED recovery → §7
  - Test matrix → §9
  - Operator visibility → §8

**Placeholder scan:** no TBD/TODO; all references cite concrete helpers or commit
SHAs.

**Type consistency:** `_JournalEntry` gains `schema: int`, `op_id: str`,
`tmp_path: str | None`, `ts: float | None`, `host_pid: int | None`. Every reducer
and reader referenced uses these types consistently.

**Ambiguity check:** §5.1 row 4 (move started with dst-absent + src-present) is
the only residual retain case. Documented as operator-inspection with a specific
warning message — not a hand-wave.

---

**Tracks:** issue #201.
**Supersedes:** PR #197 `docs/internal/F7-F8-crash-recovery-model.md` §6 non-goals
items (tmp_path metadata, atomic compaction, directory semantics decision).
**Last Updated:** 2026-04-24.

---

## Revision history

### Round 1 (initial draft) → Round 2 (current)

Round-1 review flagged two blocking correctness issues and four tightening
items. All addressed in the current revision:

**Blocking fixes:**

1. **Lock file** (§6.1, §6.5): all flock operations now acquire on
   `<journal>.lock` (stable inode, never replaced). Prior draft locked
   `<journal>` directly, which became unsafe when compaction `os.replace`'d the
   journal — a concurrent appender that opened the old inode before replace
   would block on the old inode's lock, then append to an unlinked file after
   sweep released. Lost journal record.
2. **tmp-always-exists invariant** (§7.1, §7.4): tmp is created BEFORE the
   started journal write, and no code path removes tmp on exception. Prior
   draft allowed "bare name" tmp allocation for symlinks and a pre-replace
   exception cleanup, either of which could produce "tmp absent + journal says
   started" in a pre-replace crash — sweep would then misread as post-replace
   and unlink src (data loss).

**Tightening fixes:**

3. **Unknown-op raw payload preservation** (§2 schema, §4.2): unknown-op
   entries retain the full raw JSON line via `_raw`, re-serialized verbatim on
   compaction. Prior draft dropped extras on parse, silently destroying metadata
   a future handler might need.
4. **v1 compat / `op_id` optionality** (§2 schema, §3.1): `op_id` is `str | None`;
   v1 records carry `None` and collapse-key falls back to `("v1", op, src, dst)`.
   v1 retained records re-serialized as v1 (no synthetic op_id injection).
   Prior draft required `op_id` which contradicted v1 back-compat.
5. **Known-op unknown-state row** (§5.1): explicit row in the recovery state
   table for `op ∈ _KNOWN_OPS` with an unrecognized `state` value (retain + warn).
6. **LOCK_SH-vs-appender wording** (§6.5): corrected the prior draft's wrong
   claim that shared-lock reads don't block during appends. POSIX flock
   semantics block shared readers during any exclusive holder — which is the
   intended F8 behavior anyway.

**Scope adjustment:**

7. **`fo doctor` integration deferred** (§8): scoped down to just `fo recover`.
   The `fo doctor` command exists but integrating a journal section there is
   kept out of this PR to minimize scope. Follow-up-ready (low risk, read-only).

### Round 2 → Round 3 (current)

Round-2 review flagged one new blocking correctness issue and three tightening
items. All addressed:

**Blocking fix:**

8. **Parent-dir fsync before `started` journal write** (§7.1, §7.2 step 2,
   §7.3 step 4): the tmp-exists invariant required the tmp *file* to exist
   before journal started, but the tmp's *directory entry* was not fsynced. A
   crash window "create tmp → journal append+fsync → crash before dir fsync"
   could leave the durable journal while tmp vanishes on reboot, making sweep
   misinfer post-replace and unlink src (data loss). Fix: `fsync_directory(<dst.parent>)`
   between tmp creation and the started append, for both regular-file and
   symlink writers. Regression test instruments the writer's syscall sequence
   and asserts the dir-fsync-before-started ordering (§9.3 "parent-dir fsync
   before started").

**Tightening fixes:**

9. **Stale `fo doctor` test removed from §9.6**: the round-2 scope adjustment
   dropped `fo doctor` integration but left a regression test referencing it.
   Replaced with coverage of the shared planner (§8.1) ensuring the CLI's
   rendered plan matches sweep's executed actions row-for-row.
10. **Sharper v2 `op_id` rules** (§3.1, §4.1 rule 8): v2 known-op records
    missing `op_id` are now parse-time malformed (dropped with warning), not
    silently treated as v1 identity. Unknown-op records without `op_id` use
    raw-line hash identity (first 16 hex of `sha256(_raw)`) so compaction cannot collapse
    semantically-distinct future records that a v2 parser doesn't understand.
    Parse also rejects v2 `move started` records missing `tmp_path` (§4.1 rule 9)
    so the tmp-exists invariant can't be bypassed by corrupt/external input.
11. **Pure `plan_recovery_actions` planner** (§8.1): both sweep and
    `fo recover` call the same pure planner, then either execute the plan
    (sweep) or render it (CLI). Replaces the round-2 "report-only mode" which
    risked a mutating-code-path drift between the two call sites. Regression
    test asserts CLI rendering and sweep execution agree on the action plan
    given the same input.
