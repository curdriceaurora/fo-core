# Integration Coverage Lift (Step 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift integration coverage on the 9 modules currently below 70% to ≥70% per-module, and lift global integration coverage from 71.9% to ≥75%. This satisfies the "Integration coverage floors" row of `docs/release/beta-criteria.md` §2 and is the largest single workstream on the path to beta.

**Architecture:** Integration tests in this project run via the `test-integration` CI job (full suite, real dependencies, no mocks for the system under test). Per-module floors are tracked in `scripts/coverage/integration_module_floor_baseline.json` — the file is the source of truth and is checked in CI. To raise a floor, you write tests that cover the gap, then bump the entry in the baseline file in the same PR. The baseline file refuses to drop floors silently — that's by design.

**Approach:** Each module is its own workstream and likely its own PR. The plan groups them by domain so tests share fixtures naturally. Within each group, tasks follow the same TDD shape: read the module, identify the uncovered branches via `pytest --cov=<module> --cov-report=term-missing`, write tests for the highest-value missing lines, ratchet the floor, commit.

**Tech Stack:** pytest, pytest-cov, existing integration fixtures, real system dependencies (no faster-whisper-style optional skips except where the module itself is gated).

**Out of scope:** Refactoring untested modules to be more testable — if a function is untestable as written, surface that to the user rather than rewriting it under coverage pressure. Lifting modules already above 70% (orthogonal). Branch coverage targets — the baseline tracks line coverage; branch coverage is a follow-up.

---

## File Structure

| File / Group | Action | Per-module target |
|---|---|---|
| `scripts/coverage/integration_module_floor_baseline.json` | Modify (ratchet) | Bump 9 entries to ≥70%; bump global floor to 75% |
| Per-module test files (see groups below) | Create / extend | Add tests covering the highest-value uncovered branches |

Plan conventions: see [2A plan](2026-04-27-audio-model-wiring-2a.md) "Conventions for this plan" section.

---

## The 9 modules and their groups

| Group | Module | Current | Target |
|---|---|---|---|
| **G1 Search** | `services/search/__init__.py` | 38% | 70% |
| **G2 Daemon** | `daemon/service.py` | 57% | 70% |
| **G3 Dedup/Intelligence** | `services/deduplication/__init__.py` | 60% | 70% |
| | `services/intelligence/profile_migrator.py` | 60% | 70% |
| | `services/intelligence/profile_merger.py` | 67% | 70% |
| **G4 Methodology (JD)** | `methodologies/johnny_decimal/adapters.py` | 67% | 70% |
| | `methodologies/johnny_decimal/numbering.py` | 68% | 70% |
| **G5 Misc** | `core/hardware_profile.py` | 68% | 70% |
| | `utils/epub_enhanced.py` | 55% | 70% |

Each group is a separate PR. Within a group, modules share test fixtures.

---

## The Group Workflow Template (apply to each of G1–G5)

### Step 1: Identify the gap

```bash
pytest -m integration --cov=src/<group_path> --cov-report=term-missing tests/integration/ -v 2>&1 | tee /tmp/cov-<group>.log
grep -A 50 "<module_path>" /tmp/cov-<group>.log | head -60
```

Pay attention to the `Missing` column — it lists the uncovered line numbers. Focus on:

1. **Error paths** that exception-tested code skips (caught exceptions whose handlers aren't exercised).
2. **Boundary conditions** in `if/elif/else` chains where one arm is unreached.
3. **Public API entry points** that don't have a happy-path integration test.

Skip:

- Defensive branches genuinely unreachable (mark with `# pragma: no cover` only if you can't test them — see T11 in `.claude/rules/test-generation-patterns.md`).
- Logging-only branches.
- Platform-conditional code already in `known_local_drift` in the baseline file.

### Step 2: Write tests targeting the missing lines

Follow `.claude/rules/test-generation-patterns.md` strictly:

- T1: Concrete value assertions, no sole `isinstance` checks.
- T2: After a mutating call, verify state.
- T3: Mock assertions with payloads, not just `call_count`.
- T9: No `assert len(x) >= 0` tautologies.
- T10: For predicates, add a negative case that has the same surface shape but different context.

Tests go in `tests/integration/test_<module_name>_*.py`. Follow naming conventions of existing integration tests in the same directory.

### Step 3: Re-measure

```bash
bash .claude/scripts/measure-integration-coverage.sh
```

Per `.claude/rules/ci-generation-patterns.md` C7, **always** use this script — never measure off a dirty `.coverage` file.

Confirm the module's coverage is ≥ the new target. Confirm global coverage hasn't regressed elsewhere.

### Step 4: Ratchet the baseline

Edit `scripts/coverage/integration_module_floor_baseline.json`. Find the entry for the module and update its value to the new measured floor (rounded down to the nearest whole percent — give CI 0.5 pp tolerance per the `policy.tolerance_percent` field).

If the global integration floor in `.github/workflows/ci.yml` (currently `71.9%`) needs bumping for this PR, do it in the same PR.

### Step 5: Commit + PR

```bash
git add tests/integration/<new_files> scripts/coverage/integration_module_floor_baseline.json
git commit -m "test(<module>): lift integration coverage to <new>%"
```

PR title: `test(coverage): lift <group> integration coverage to ≥70%`

---

## Group G1: Search (single module, biggest lift)

**Module:** `src/services/search/__init__.py`
**Current:** 38%
**Target:** 70%

### G1 — Task 1: Map the uncovered surface

- [ ] **Step 1: Run coverage on the search package**

```bash
pytest -m integration --cov=src/services/search --cov-report=term-missing tests/integration/test_*search* -v 2>&1 | head -100
```

- [ ] **Step 2: Read `src/services/search/__init__.py`** and note the public functions/classes. Map each missing line range to a function. Capture in scratch notes.

- [ ] **Step 3: Cross-reference with `.claude/rules/search-generation-patterns.md` S1–S6** — every `rglob`, every cache write, every result-formatting path is a candidate for testing.

### G1 — Task 2: Write tests for symlink filtering (S1)

The S1 anti-pattern says symlinks must be filtered. Verify the search corpus collector does this and add a test if missing.

- [ ] **Step 1: Test**

```python
@pytest.mark.integration
def test_search_skips_symlinked_paths(tmp_path: Path) -> None:
    """S1: symlinks pointing outside the search root must not be indexed."""
    from services.search import SearchService  # adjust import as needed

    target = tmp_path / "outside_secret.txt"
    target.write_text("CLASSIFIED")
    inside = tmp_path / "corpus"
    inside.mkdir()
    (inside / "link.txt").symlink_to(target)

    svc = SearchService(root=inside)
    results = svc.search("CLASSIFIED")
    assert all("link.txt" not in str(r) for r in results)
```

- [ ] **Step 2: If the test fails because the search service does follow symlinks**, that's a real S1 finding. Fix it in `src/services/search/__init__.py` per the S1 pattern (skip `is_symlink()` candidates) before shipping the coverage lift.

- [ ] **Step 3: Repeat the same shape for hidden files (S2), absolute-path exposure (S4), and PII-in-debug-output (S5)**. Each finding either:
  - Already-implemented → test passes → coverage rises
  - Real bug → fix it before shipping the coverage lift

### G1 — Task 3: Cover error paths

- [ ] **Step 1**: Identify each `except` clause in the module and verify there's a test that triggers it. If not, write one.

- [ ] **Step 2**: Identify each `if/elif/else` whose branches aren't all covered; add tests until each branch is reached.

### G1 — Task 4: Re-measure, ratchet, commit, PR

Follow the Group Workflow Template Steps 3–5.

---

## Group G2: Daemon

**Module:** `src/daemon/service.py`
**Current:** 57%
**Target:** 70%

### G2 — Task 1: Map uncovered surface

- [ ] **Step 1**: `pytest -m integration --cov=src/daemon/service --cov-report=term-missing tests/integration/test_*daemon* -v`

- [ ] **Step 2**: Read `src/daemon/service.py`. Daemon failure modes that integration tests usually skip:
  - Signal handling (SIGTERM, SIGHUP)
  - PID file lifecycle (creation, stale-PID cleanup, lock acquisition failure)
  - Watcher restart on crash
  - Graceful shutdown with in-flight events

### G2 — Task 2: Daemon smoke test (also satisfies a separate beta-criteria bullet)

- [ ] **Step 1**: Write `tests/integration/test_daemon_smoke.py` covering start → watch → stop → status → recovery-after-SIGTERM. This test ALSO satisfies the "Daemon smoke test in CI" row of beta-criteria.md §2.

```python
@pytest.mark.integration
class TestDaemonSmoke:
    def test_start_watch_stop_status_cycle(self, tmp_path: Path) -> None:
        # Use subprocess.Popen to launch `fo daemon start --watch <tmp_path>`
        # then `fo daemon status` should show running, then `fo daemon stop`
        # cleanly terminates. Drop a file in tmp_path mid-cycle to verify
        # the watcher is live.
        ...
```

(Pin the exact subprocess shape to whatever the existing test infrastructure supports; if no precedent, use `subprocess.Popen` with a timeout and assert exit codes + output contains "running" / "stopped".)

- [ ] **Step 2**: Test signal handling — send SIGTERM to a started daemon and assert PID file cleanup.

### G2 — Task 3: Re-measure, ratchet, commit, PR

Follow the template.

---

## Group G3: Dedup + Intelligence

**Modules:**

- `src/services/deduplication/__init__.py` (60% → 70%)
- `src/services/intelligence/profile_migrator.py` (60% → 70%)
- `src/services/intelligence/profile_merger.py` (67% → 70%)

These three share the "user profile / preference data" domain and naturally test together.

### G3 — Task 1: Profile migrator coverage

- [ ] Coverage map: `pytest -m integration --cov=src/services/intelligence/profile_migrator --cov-report=term-missing -v`

- [ ] Likely gaps: corrupted-profile recovery, version-mismatch fallback, partial-migration failure.

- [ ] Write tests with `tmp_path` profile files in known-bad shapes (truncated JSON, missing fields, version too new). Assert the migrator either fixes them or fails loudly with a useful error.

### G3 — Task 2: Profile merger coverage

- [ ] Coverage map.

- [ ] Likely gaps: conflict resolution between two profiles disagreeing on a key. Write tests with explicit conflict pairs.

### G3 — Task 3: Deduplication package coverage

- [ ] Coverage map for `src/services/deduplication/__init__.py`.

- [ ] Likely gaps: ImportError handling for optional `imagededup` dep (`known_local_drift` notes this), threshold edge cases (exact 1.0 similarity, 0.0 similarity), empty-corpus path.

### G3 — Task 4: Re-measure, ratchet, commit, PR

Single PR for all three modules — they share fixtures.

---

## Group G4: Johnny Decimal Methodology

**Modules:**

- `src/methodologies/johnny_decimal/adapters.py` (67% → 70%)
- `src/methodologies/johnny_decimal/numbering.py` (68% → 70%)

Smallest lift; can be one PR.

### G4 — Task 1: Numbering edge cases

- [ ] Coverage map.

- [ ] Gaps likely in: invalid number parsing, area/category boundary (e.g. `00.00` vs `99.99`), overflow when generating next number in a full category.

- [ ] Write tests for each parse-and-roundtrip path with adversarial inputs (empty string, extra dots, non-ASCII digits).

### G4 — Task 2: Adapter coverage

- [ ] Coverage map.

- [ ] Gaps likely in: format conversion errors (PARA → JD with no matching area), empty methodology config, fallback paths.

### G4 — Task 3: Re-measure, ratchet, commit, PR

---

## Group G5: Hardware profile + EPUB

**Modules:**

- `src/core/hardware_profile.py` (68% → 70%)
- `src/utils/epub_enhanced.py` (55% → 70%)

Different domains; can be one PR (small) or split.

### G5 — Task 1: Hardware profile fallback paths

- [ ] Coverage map.

- [ ] Gaps likely in: GPU detection on machines without CUDA/MPS (CI Linux runners), the `fo hardware-info` JSON path, invalid `device` strings.

- [ ] Tests should mock `torch` import outcomes via `monkeypatch.setitem(sys.modules, ...)` (use `patch.dict` per `xdist-safe-patterns.md` Pattern 2 — atomic restore).

### G5 — Task 2: EPUB enhanced extraction

- [ ] Coverage map.

- [ ] Gaps likely in: malformed EPUB recovery, missing TOC, encrypted EPUB rejection.

- [ ] Use small synthetic EPUB fixtures (or `pytest.importorskip("ebooklib")` + build one in-memory).

### G5 — Task 3: Re-measure, ratchet, commit, PR

---

## Final task: bump global integration floor

After all five groups have merged and the per-module floors are at ≥70%:

- [ ] **Step 1: Re-measure global coverage**

```bash
bash .claude/scripts/measure-integration-coverage.sh
```

- [ ] **Step 2: If global TOTAL is ≥75%**, bump the gate in `.github/workflows/ci.yml` (the `--cov-fail-under` value in the `test-integration` job, currently 71.9%) to the new measured floor minus 0.5 pp tolerance.

- [ ] **Step 3: Update `scripts/coverage/integration_module_floor_baseline.json`** `policy.new_module_min_percent` if you want new-module floor raised correspondingly.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml scripts/coverage/integration_module_floor_baseline.json
git commit -m "ci: lift integration coverage gate to 75% (closes Step 4 of beta path)"
```

PR title: `ci: lift integration coverage gate to 75% (Step 4 closeout)`

---

## Verification checklist (rolls up the §2 entry-checklist row)

After this plan executes:

- All 9 modules previously below 70% are at ≥70% per the baseline file.
- Global integration coverage gate is at ≥75%.
- Daemon smoke test exists in CI exercising start → watch → stop → status → SIGTERM (also satisfies the separate daemon-smoke-test row of beta-criteria.md §2).
- No silent floor regressions — every floor change is visible in the baseline file diff.

This is the largest single Step in the alpha→beta path. Expect 5+ PRs over multiple weeks.
