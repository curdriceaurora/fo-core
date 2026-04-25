# F7/F8 Crash-Recovery Model — Supplementary Spec

Supplement to `2026-04-22-hardening-roadmap-design.md` §3 F7/F8.

Purpose: capture the full invariant table that was reconstructed across 8 review
passes. Checked in so future work on the durable-move protocol starts from the
invariants, not from the happy path.

Also documents the 3 deliberate deviations from the original spec's prescribed
recovery behavior, and the reason each prescribed behavior was wrong.

---

## 1. Recovery state table (sweep-time)

Inputs observable by the sweep: the last journal entry per `(src, dst)` pair,
`lexists(src)`, `lexists(dst)`, and the declared `op` field.

| op | journal state | lexists(src) | lexists(dst) | Sweep action | Rationale |
|---|---|---|---|---|---|
| — | _no entry_ | any | any | no-op | nothing to reconcile |
| not `move` | any | any | any | **retain** | unknown op — future binary handles; move semantics would data-loss on downgrade (codex hdFb) |
| `move` | `started` | any | any | **retain** | ambiguous — see §2 rows 1–7: dst may be pre-existing OR our replaced content; can't tell without content comparison (codex gbdD + g2Ex) |
| `move` | `copied` | present | present | unlink src → fsync(src.parent) → drop | pre-unlink crash; finish the move. Fsync per codex hT9b. |
| `move` | `copied` | absent | present | drop | post-unlink crash; already consistent |
| `move` | `copied` | any | absent | **retain** | dst removed out-of-band; unlinking src would destroy last copy (codex hGWW) |
| `move` | `done` | any | any | drop | audit-only, already reconciled |
| `move` | unknown state | any | any | drop + warn | unrecognized state string; retry won't help |

"Retain" means: return False from `_complete_or_rollback`, entry is re-written
to the journal, next sweep (or operator intervention, or successful retry
producing a superseding `done` entry) reconciles.

---

## 2. Crash-point catalog (EXDEV path)

Boundaries in `_durable_cross_device_move`, numbered by write order. The
journal state observable by sweep after a crash at each boundary:

| # | Boundary (after this syscall/step) | src on disk | dst on disk | tmp on disk | Journal state | Sweep behaviour |
|---|---|---|---|---|---|---|
| 0 | (function entry) | present | either | absent | _no entry_ | no-op |
| 1 | `_append_journal(started)` | present | either (untouched) | absent | `started` | **retain** |
| 2 | tmp path generated | present | either | absent | `started` | retain |
| 3 | `os.symlink(target, tmp)` / `shutil.copyfile(src, tmp)` | present | either | present (content may be partial for copyfile; atomic for symlink) | `started` | retain |
| 4 | `shutil.copystat(src, tmp)` | present | either | present + mode | `started` | retain |
| 5 | `os.fsync(tmp_fd)` | present | either | durable | `started` | retain |
| 6 | `fsync_directory(dst)` _pre-replace_ | present | either | durable + dir-entry-durable | `started` | retain |
| 7 | `os.replace(tmp, dst)` | present | **has new content** (atomic) | absent | `started` | **retain** — indistinguishable from rows 1–6 without content check. This is why spec-prescribed "delete orphaned dst on started" is wrong. |
| 8 | `fsync_directory(dst)` _post-replace_ | present | new content durable | absent | `started` | retain |
| 9 | `_append_journal(copied)` | present | new content durable | absent | `copied` | unlink src if `lexists(dst)` else retain |
| 10 | `os.unlink(src)` | absent | new content durable | absent | `copied` | fsync(src.parent) → drop |
| 11 | `fsync_directory(src)` | absent durably | new content durable | absent | `copied` | drop |
| 12 | `_append_journal(done)` | absent | new content | absent | `done` | drop |

**Key insight**: journal state `started` covers crash-points 1–8, which span
"dst never touched" through "dst has new content". We cannot disambiguate
without either (a) content comparison, or (b) recording `tmp_path` in the
journal entry so sweep can check if the replace already happened. **Both
would be future F7.1 work.** Current implementation retains the entry for
operator/retry reconciliation — the safe choice under ambiguity.

Same-device moves (os.replace without EXDEV) are truly atomic — no journal
entry is written at all, no crash-point catalog needed.

---

## 3. Invariant → test mapping

Each invariant is enforced by at least one named test. Tests live under
`tests/undo/`, `tests/integration/`, `tests/history/`, and
`tests/test_config_manager.py`.

### F7 durable_move — same-device + file types

| # | Invariant | Test |
|---|---|---|
| 1 | same-device move uses `os.replace` (atomic) | `test_basic_move` |
| 2 | same-device creates nested dst.parent | `test_move_to_nested_destination` |
| 3 | same-device writes no journal entry | `test_same_device_does_not_append_journal` |
| 4 | regular file contents + mode bits preserved | `test_preserves_file_contents_and_permissions` |
| 5 | directory source rejected with `IsADirectoryError` | `test_directory_source_raises_is_a_directory` |
| 6 | missing source raises `FileNotFoundError` | `test_missing_source_raises_file_not_found` |
| 7 | non-EXDEV `os.replace` errors propagate | `test_non_exdev_os_replace_error_propagates` |

### F7 durable_move — EXDEV path

| # | Invariant | Test |
|---|---|---|
| 8 | EXDEV copy completes + unlinks src on success | `test_cross_device_copy_completes` |
| 9 | EXDEV journal reaches `done` | `test_cross_device_journal_reaches_done_state` |
| 10 | EXDEV preserves symlink identity (readlink + os.symlink) | `test_cross_device_preserves_symlink_identity` |
| 11 | EXDEV preserves dangling symlinks (target doesn't exist) | `test_cross_device_preserves_dangling_symlink` |
| 12 | EXDEV fsyncs `src.parent` after `os.unlink(src)` | `test_cross_device_fsyncs_src_parent_after_unlink` |
| 13 | EXDEV cleans up a stale symlink tmp from a prior crash | `test_cross_device_symlink_clears_stale_tmp` |
| 14 | EXDEV swallows `FileNotFoundError` on final src unlink | `test_exdev_source_already_gone` |
| 15 | copystat failure is non-fatal | `test_exdev_copystat_failure_nonfatal` |
| 16 | crash during copy leaves recoverable `started` entry | `test_crash_between_started_and_copied_leaves_recoverable_state` |

### F7 sweep behaviour

| # | Invariant | Test |
|---|---|---|
| 17 | empty/missing journal is a no-op | `test_sweep_noop_on_empty_journal` |
| 18 | STARTED preserves both paths + retains entry | `test_sweep_started_state_retains_entry_and_preserves_paths` |
| 19 | STARTED preserves dst byte-for-byte | `test_started_sweep_preserves_dst_contents_byte_for_byte` |
| 20 | STARTED tolerates absent dst | `test_started_sweep_tolerates_absent_dst` |
| 21 | STARTED persists across sweeps; superseded by `done` | `test_started_entry_persists_across_multiple_sweeps` |
| 22 | STARTED unlocked-body same contract | `test_sweep_unlocked_body_started_retains_entry` |
| 23 | COPIED unlinks src when dst present | `test_sweep_completes_copied_state` |
| 24 | COPIED retains when dst missing | `test_sweep_copied_state_retains_when_dst_missing` |
| 25 | COPIED accepts dangling-symlink dst via `lexists` | `test_sweep_copied_state_accepts_symlink_dst` |
| 26 | COPIED fsyncs src.parent after unlink | `test_sweep_copied_state_fsyncs_src_parent_after_unlink` |
| 27 | COPIED tolerates fsync failure on unusual FS | `test_sweep_copied_state_tolerates_fsync_failure` |
| 28 | COPIED retains on OSError from src.unlink | `test_sweep_retains_failed_entries` + `test_sweep_unlocked_body_retains_failed_entry` |
| 29 | DONE drops entry | `test_sweep_ignores_done_entries` |
| 30 | Missing files tolerated (no raise) | `test_sweep_tolerates_missing_files` |
| 31 | Malformed journal lines logged + dropped | `test_read_journal_drops_malformed_lines` |
| 32 | Idempotent sweep (retained entry survives) | `test_sweep_is_idempotent` |
| 33 | Unknown op retained for future handler | `test_sweep_retains_entries_with_unknown_op` |

### F7 journal concurrency

| # | Invariant | Test |
|---|---|---|
| 34 | `_append_journal` blocks on `LOCK_EX` held by sweep | `test_append_journal_blocks_while_sweep_holds_flock` |
| 35 | `_append_journal` writes when no holder | `test_append_journal_writes_when_no_holder` |
| 36 | Normalized path does NOT follow symlinks | `test_normalized_path_str_does_not_follow_symlinks` |
| 37 | Normalized path resolves relative + `..` | `test_normalized_path_still_resolves_relative_paths` |

### F7 rollback integration

| # | Invariant | Test |
|---|---|---|
| 38 | `rollback_move` directory fallback via `_move` | `test_move_dispatch_on_directory` |
| 39 | `rollback_delete` restores directory trash entries | `test_restore_directory_from_trash` |
| 40 | `rollback_delete` restores regular-file trash entries | `test_successful_restore_from_trash` |
| 41 | `CREATE` undo on directory | `test_create_dispatch_on_directory` |
| 42 | Executor inherits `validator.journal_path` | `test_executor_inherits_validator_journal_path_when_omitted` |
| 43 | Explicit executor journal_path overrides | `test_explicit_executor_journal_path_overrides_validator` |
| 44 | Legacy validator (no journal_path attr) falls back | `test_executor_falls_back_to_default_when_validator_has_no_journal` |

### F8 trash GC

| # | Invariant | Test |
|---|---|---|
| 45 | Empty/missing journal → never in-flight | `test_missing_journal_is_never_in_flight`, `test_empty_journal_is_never_in_flight` |
| 46 | STARTED marks both src AND dst in-flight | `test_started_src_is_in_flight` |
| 47 | COPIED marks both paths in-flight | `test_copied_both_paths_still_in_flight` |
| 48 | DONE not in-flight | `test_done_is_not_in_flight` |
| 49 | Latest state per (src,dst) wins | `test_latest_state_wins` |
| 50 | Unrelated entries don't block | `test_unrelated_entry_does_not_block` |
| 51 | Relative path query matches absolute entry | `test_is_path_in_flight_matches_relative_path` |
| 52 | Symlink and target are distinct paths | `test_is_path_in_flight_does_not_follow_symlinks` |
| 53 | No journal entry → trash safe to delete | `test_no_journal_entry_is_safe_to_delete` |
| 54 | In-flight src → trash NOT safe | `test_in_flight_trash_is_not_safe_to_delete` |
| 55 | In-flight dst → trash NOT safe | `test_in_flight_protects_dst_too` |
| 56 | Completed (done) → trash safe | `test_completed_move_is_safe_to_delete` |
| 57 | Default journal path resolves via path_manager + XDG | `test_default_journal_path_used_when_unspecified` |

### F5 database integrity

| # | Invariant | Test |
|---|---|---|
| 58 | integrity_check passes on fresh db | `test_integrity_check_passes_on_fresh_db` |
| 59 | Truncated file → `DatabaseCorruptionError` | `test_integrity_check_raises_on_truncated_file` |
| 60 | Bit-flip → `DatabaseCorruptionError` | `test_integrity_check_raises_on_bit_flip` |
| 61 | Error message mentions recovery action | `test_corruption_error_is_actionable` |
| 62 | `OperationalError` propagates unchanged (transient, not corruption) | `test_operational_error_is_not_classified_as_corruption` |
| 63 | Non-operational `DatabaseError` wrapped as corruption | `test_non_operational_database_error_is_classified_as_corruption` |

### F6 config migrations

| # | Invariant | Test |
|---|---|---|
| 64 | Identical `from_version`/`to_version` short-circuits | `test_migrate_to_current_is_noop_when_versions_match` |
| 65 | Earlier migrations skipped | `test_migrate_to_current_skips_earlier_migrations` |
| 66 | Registry gap → walker stops, warns | `test_migration_walker_stops_on_registry_gap` + `test_migrate_to_current_gap_in_registry_warns` |
| 67 | Non-increasing `to_version` → walker stops with error, no infinite loop | `test_migration_walker_stops_on_non_increasing_target` |
| 68 | Exhausted chain → warn + best-effort return | `test_migrate_to_current_exhausted_registry_warns` |
| 69 | Migration exception re-raises | `test_migrate_to_current_reraises_migration_exception` |
| 70 | Unversioned config classified as `LEGACY_CONFIG_VERSION` | `test_unversioned_config_classified_as_legacy` |
| 71 | Compare versions numerically (10.0 > 2.0) | `test_compare_versions_numeric_ordering` |
| 72 | Non-numeric version falls back to `(0,)` | `test_compare_versions_non_numeric_fallback` |

### Known test gaps (closed in round-9)

| # | Invariant | Test (round-9 addition) |
|---|---|---|
| INV-2b | Same-device move preserves symlink identity (no deref) | `test_same_device_preserves_symlink_identity` |
| MIG-2 | Numerically-equivalent versions short-circuit (`"1.0"` vs `"1.0.0"`) | `test_migrate_equivalent_versions_short_circuit` |
| MIG-7 | `_version_key` trims trailing-zero components so equivalent versions compare equal (codex hp2C) | `test_version_key_trims_trailing_zeros` |
| INV-37b | `_normalized_path_str` case-folds on Windows via `os.path.normcase` so journal lookups don't miss `C:\` vs `c:\` (codex hp2G) | `test_normalized_path_case_folds_on_windows` |

### Round-10 additions (directory coordination + shared-lock reads)

| # | Invariant | Test |
|---|---|---|
| INV-DM1 | `directory_move` writes started→done journal pair around `shutil.move` | `test_directory_move_writes_started_done_pair` |
| INV-DM2 | `is_path_in_flight` sees the path as in-flight during the move (F8 coordination) | `test_directory_move_marks_path_in_flight_during_move` |
| INV-DM3 | `done` is written even if `shutil.move` raises (releases in-flight marker) | `test_directory_move_writes_done_even_if_move_fails` |
| INV-DM4 | Sweep drops `dir_move` done entries | `test_sweep_drops_dir_move_done` |
| INV-DM5 | Sweep drops `dir_move` started entries with operator-action warning | `test_sweep_drops_dir_move_started_with_warning` |
| INV-DM6 | Sweep does NOT apply move semantics (`src.unlink`) to dir_move ops | `test_sweep_does_not_apply_move_semantics_to_dir_move` |
| INV-LS1 | `is_path_in_flight` blocks on `LOCK_SH` while a writer holds `LOCK_EX` (codex ir1P) | `test_is_path_in_flight_blocks_while_writer_holds_lock_ex` |

---

## 4. Exact CI-matching local commands

All commands must run **from the repo / worktree root** (the directory
containing `pyproject.toml`), NOT a parent or sibling directory. Running
from the wrong root changes ruff's config resolution and the git-diff-based
guardrails' base ref. If you're using `git worktree`, that means the
worktree's own root, not the main checkout.

```bash
# Run from $REPO_ROOT (or $WORKTREE_ROOT if using `git worktree`).
# All paths below are relative to that directory.

# === Job: lint (GitHub Actions) ===
# Exact invocation CI uses:
pre-commit run --all-files

# === Job: Test PR suite (py3.11) ===
pytest -m "ci" --dist=loadgroup -n auto

# === Quality guardrails (NOT covered by -m ci because meta-tests aren't marked ci) ===
pytest tests/ci/ -v
# Specifically: T1 SOLE_ISINSTANCE, T9 VACUOUS_TAUTOLOGY, optional-dep guards,
# pragma-on-tested-branch, predicate-negative-case. Missing these locally is
# what caused the round-8 post-push lint failure.

# === Integration coverage floors (main-branch gate; run before PR) ===
python3 -m coverage erase
python3 -m pytest tests/ -m "integration" --cov=src --cov-report=
python3 scripts/coverage/check_integration_module_floors.py

# === Ruff check matching CI (must run from worktree root, not parent) ===
ruff check .
ruff format . --check

# === Project's own wrapper (convenience, but the raw commands above are
# what CI actually runs) ===
bash .claude/scripts/pre-commit-validation.sh
```

### Prior local-gate gap

The round-8 push landed a T1 guardrail violation locally-clean because:

1. `bash .claude/scripts/pre-commit-validation.sh` runs `pytest -m "ci"` which
   filters by marker. The T1 guardrail test (`test_test_quality_guardrails.py`)
   is not marked `ci` — it's a meta-test scanned across all changed tests.
2. Running `pytest tests/ci/` directly catches it every time. That step wasn't
   in my pre-push gate. **Adding it as a mandatory step going forward.**

---

## 5. Deliberate deviations from the original spec

Three places where this implementation deviates from `2026-04-22-hardening-roadmap-design.md`
§3 F7's prescribed behaviour. Each deviation corrects a data-loss bug in the
spec's prescribed logic.

### 5.1 STARTED sweep does NOT delete orphaned dst

- **Spec §3 F7**: "delete the orphaned destination if state is `started`".
- **Implementation**: retain the entry; never touch `dst`.
- **Reason**: `_durable_cross_device_move` logs `started` **before** the copy
  begins, and logs `copied` **after** `os.replace`. Crash-points 1–8 all look
  identical to sweep as `started`, but in crash-point 7+ `dst` already has the
  new content. The spec's prescribed "delete dst" would destroy the successfully-
  replaced content OR a pre-existing legitimate file. See codex
  PRRT_kwDOR_Rkws59gbdD + PRRT_kwDOR_Rkws59g2Ex.
- **Cost**: entries accumulate in the journal until a successful retry
  supersedes them with `done` (at which point sweep drops them). Operators see
  the WARNING log.
- **Future fix (F7.1)**: record `tmp_path` in the `started` journal entry so
  sweep can check if `os.replace` already ran (tmp absent → replaced) and
  reconcile unambiguously.

### 5.2 COPIED sweep guards dst existence before unlinking src

- **Spec §3 F7**: "unlink the orphaned source if state is `copied`".
- **Implementation**: check `os.path.lexists(dst)` first; retain entry if dst
  is missing.
- **Reason**: between the `copied` journal write and the next sweep, an
  out-of-band actor (operator cleanup, another process, backup restore) can
  remove `dst`. Blindly unlinking `src` would destroy the last remaining copy.
  See codex PRRT_kwDOR_Rkws59hGWW.
- `os.path.lexists` (not `dst.exists()`) so a dangling-symlink dst — a
  legitimate landing for an EXDEV symlink move — still satisfies the check.

### 5.3 F6 typed `Migration` dataclass

- **Spec §3 F6**: "config-schema migration path with explicit version bump
  handling". No mention of a new module or typed registration.
- **Implementation**: dedicated `src/config/migrations.py` with
  `@dataclass(frozen=True) class Migration(to_version, transform)`.
- **Reason**: codex PRRT_kwDOR_Rkws59hdFY identified a data-corruption case
  where the walker inferred the post-migration version from the next registry
  key, which jumped over gaps. The typed dataclass makes `to_version`
  authoritative so gaps become detectable.
- **Arguable over-engineering**: a simpler inline contiguity check on the
  bare-callable registry could have satisfied the fix. Chose the dataclass
  for clarity and to make future migrations' target version explicit at
  declaration time. **This is the one deviation that is a judgement call
  rather than a spec correction.**

---

## 6. Non-goals (deferred to follow-ups)

Explicitly out of scope for this PR:

- Directory durable-move (non-atomic for both same-device and EXDEV directory
  moves). Spec §3 F7 out-of-scope list + `_move` helper's `shutil.move`
  fallback for directories.
- Content-comparison-based disambiguation of `started` crash points (F7.1).
- Journal-record `tmp_path` field for unambiguous `started` recovery (F7.1).
- Cross-process advisory locking on Windows (spec treats as single-invocation).
- Audio organizer, copilot executor, para migration manager, updater installer
  (spec §3 F7 out-of-scope list).

---

**Last Updated**: 2026-04-24
**Tracks**: PR #197 `hardening/epic-f-integrity`
**Related**: `.claude/rules/feature-generation-patterns.md` F1/F3/F7
