# Infra Hardening: Design Spec

**Issue:** [#92](https://github.com/curdriceaurora/fo-core/issues/92)
**Date:** 2026-04-15
**Status:** Approved

## Overview

Four MECE workstreams that harden the repository's validation pipeline. Each ships as an
independent PR. Execution order follows the issue recommendation:

1. PR-time integration coverage hard gate
2. xdist/shared-state hardening audit
3. Type-check gate expansion
4. Optional extras validation

---

## Workstream 1 — PR-time Integration Coverage Hard Gate

### Problem

`pr-integration.yml` runs `pytest -m "integration"` with `-n=auto` but without `--cov`.
The 71.9% global floor and 287 per-module floors from `scripts/coverage/integration_module_floor_baseline.json`
are only enforced on main-push. A PR can introduce integration coverage regressions that are
invisible until after merge.

### Design

Modify `pr-integration.yml` to add coverage measurement and floor enforcement identical to
the `test-integration` job in `ci.yml`:

**Step changes:**

1. Add to the pytest command:

   ```
   --cov=file_organizer --cov-branch --cov-report=xml --cov-report=term-missing
   ```

2. Pipe pytest output to a report file and run the per-module floor script (exact shape
   mirrors `ci.yml` lines 449–458):

   ```bash
   pytest tests/ \
     -m "integration and not benchmark" \
     --strict-markers --cov=file_organizer --cov-branch \
     --cov-report=term-missing --cov-report=xml \
     --timeout=60 -n=auto --override-ini="addopts=" \
     | tee "$RUNNER_TEMP/integration-coverage-report.txt"

   python scripts/check_module_coverage_floor.py \
     --report-path "$RUNNER_TEMP/integration-coverage-report.txt" \
     --baseline-path scripts/coverage/integration_module_floor_baseline.json
   ```

   Uses `continue-on-error: true` on the floor step (id: `per_module_gate`) so the global
   gate can also run and both outcomes are inspected by an enforcer step — same pattern as
   main CI.

3. Add global floor step (`id: global_gate`, `continue-on-error: true`):

   ```bash
   coverage report --fail-under=71.9
   ```

4. Add enforcer step that exits non-zero if either gate failed:

   ```bash
   if [ "${{ steps.per_module_gate.outcome }}" != "success" ]; then exit 1; fi
   if [ "${{ steps.global_gate.outcome }}"   != "success" ]; then exit 1; fi
   ```

**Failure semantics:** Hard gate — PR fails if either the per-module floor or global floor
is breached. Same enforcement as main, just earlier.

**Cost:** `--cov` adds ~15–20% overhead to the 3–5 min integration run. Acceptable given the
signal gained. Bounded by `cancel-in-progress: true` in the concurrency group.

**No new baseline:** The PR gate uses the same `integration_module_floor_baseline.json` as
main. No separate PR-specific baseline.

### Out of scope

- Changing the 71.9% global floor or any per-module floor
- Adding diff-cover to integration tests

---

## Workstream 2 — Type-Check Gate Expansion

### Problem

Only `src/file_organizer/models/` (28 files) is in the strict mypy CI gate. 17 other packages
(~270 files) are unchecked despite `strict = true` being set globally in `pyproject.toml`.

### Design

**Mypy audit results (run 2026-04-15):**

| Tier | Packages | Files | Errors | Action |
|------|----------|-------|--------|--------|
| **Tier 1** | optimization, parallel, events, daemon, undo, history, interfaces, updater, pipeline | 83 | 0 | Gate immediately |
| **Tier 2** | methodologies, integrations, utils, config | 53 | 6 | Remove stale `# type: ignore` comments, then gate |
| **Tier 3** | core, cli, watcher | 44 | 11 | Deferred → issue #93 |
| **Defer** | services | 76 | 28 | Deferred → issue #93 |

**Tier 2 errors are all `[unused-ignore]`** — the codebase improved and suppression comments
became stale. All 6 are one-line removals. No logic changes.

**CI changes:**

1. Expand `mypy src/file_organizer/models/` in the `type-check` job in `ci.yml` to include
   all Tier 1+2 packages.
2. Update the `mypy-changed` pre-commit hook in `.pre-commit-config.yaml` to cover the same
   expanded set.
3. Add a `[[tool.mypy.overrides]]` entry if any Tier 2 package needs a narrow suppression
   (none expected).

**Staged ratchet plan:** Issue #93 tracks Tier 3 (core, cli, watcher) and services, with the
goal of eventually replacing the per-package listing with `mypy src/file_organizer/`.

### Out of scope

- Fixing the 11 Tier 3 errors or 28 services errors
- Demanding repo-wide mypy cleanliness in this PR

---

## Workstream 3 — Optional Extras Validation

### Problem

Only `[search]` is validated in CI. 12 other extras have no install/import contract. A
broken version constraint or missing native lib in any extra is invisible until a user
installs it.

### Design

New `ci-extras.yml` workflow. Keeps `ci.yml` clean. Triggered on PR push and main push.

**Extras classification:**

| Class | Extras | Contract |
|-------|--------|----------|
| **File capability** | audio, video, dedup, archive, scientific, cad | Install + import + smoke canary test |
| **Platform/API** | cloud, llama, mlx, claude | Install + import only (no external calls in CI) |
| **Tooling** | build, docs | Deferred — not runtime |

**Matrix job:** One job per extra using a GitHub Actions matrix. Each job:

1. `pip install -e ".[dev,extra]"` — installs both the extra and the repo's test toolchain
   (pytest, pytest-asyncio, pytest-cov, faker, etc.). This ensures `pytest` is present
   and the canary tests can run without a separate tooling install step.
2. `python -c "import key_module; print('OK')"` — top-level import succeeds
3. (File capability only) `pytest tests/extras/test_extras_<extra>.py -m "smoke" -x` — canary passes

**mlx:** macOS runner in the matrix (`runs-on: macos-latest`); all other extras use ubuntu.

**New `tests/extras/` directory:** One canary test file per file-capability extra. Each
contains a single `@pytest.mark.smoke` test that:
- Creates a minimal fixture file of the relevant type in `tmp_path` (e.g. a 1-second WAV,
  a minimal 7z archive — **not ZIP**, which is core and would not exercise the optional dep)
- Instantiates the core reader/processor class that the extra provides
- Asserts a non-None result — no external calls, no network

**Key imports per extra:**

| Extra | Key import | Canary class |
|-------|-----------|--------------|
| audio | `faster_whisper`, `mutagen` | Audio metadata reader |
| video | `cv2`, `scenedetect` | Video frame extractor |
| dedup | `imagededup`, `sklearn` | Image deduplicator |
| archive | `py7zr`, `rarfile` | Archive reader (7z/RAR fixture file, not ZIP — ZIP is core) |
| scientific | `h5py`, `scipy` | HDF5 reader |
| cad | `ezdxf` | DXF reader |
| cloud | `openai` | (import only) |
| llama | `llama_cpp` | (import only) |
| mlx | `mlx_lm` | (import only) |
| claude | `anthropic` | (import only) |

### Out of scope

- Runtime behavior testing for API extras (cloud, llama, mlx, claude)
- Exhaustive file format matrices
- extras combinations or cross-extras interaction testing

---

## Workstream 4 — xdist Hardening Audit

### Problem

The `test-full` job uses a 6-directory-shard matrix (not xdist) introduced as an "emergency"
workaround after xdist flakes. The comment in `ci.yml` reads:

> *"all shards can use xdist again instead of the emergency micro-shard matrix."*

`pr-integration.yml` still runs with `-n=auto` (xdist live on PRs). Two known recent races
have been fixed:

- PR #91 (`821d5bf`): config dir shared state in `test_config_edit_persists_text_model`
- `4f47168`: sklearn `sys.modules` leak across xdist workers in `test_dedup_embedder`

The question is: are there more?

### Design (audit-first)

**Phase 1 — Understand the emergency**

Read git history around the transition to the shard matrix to identify the original root
cause. Key commits: `8261fad` (split into 4 directory shards), `53cab36` (single-worker
shard 3, GC-finalizer hang), `37f4dd9` (MockStat xdist teardown crash).

**Phase 2 — Audit current xdist surface**

Add `scripts/ci/run-xdist-audit.sh`:

```bash
#!/usr/bin/env bash
# Run the non-integration, non-benchmark suite 3 times under xdist.
# Captures FAILED/ERROR output to docs/internal/xdist-audit-YYYY-MM-DD.md.
for i in 1 2 3; do
  pytest tests/ \
    -m "not integration and not benchmark and not e2e" \
    -n auto --timeout=30 -q 2>&1 | tee -a /tmp/xdist-audit-run-$i.txt
done
grep -h "FAILED\|ERROR\|xfail" /tmp/xdist-audit-run-*.txt | sort | uniq -c | sort -rn
```

Run the audit and commit findings to `docs/internal/xdist-audit-2026-04-15.md`.

**Phase 3 — Fix confirmed failures only**

For each confirmed flake:
- Categorize: shared state / env race / module-level singleton / fixture scope
- Apply the minimal fix (monkeypatch, xdist_group marker, patch.dict, or fixture conversion)
- Do not preemptively convert 77 module-level `CliRunner` singletons unless confirmed failing

**Phase 4 — Re-enable xdist for test-full (if justified)**

If Phase 3 fixes resolve the root cause of the emergency, replace the 6-shard directory
split in `test-full` with `pytest -n auto`. If remaining issues are complex, document them
in a follow-up issue and leave the shard matrix in place.

**Phase 5 — Guidance doc**

Add `.claude/rules/xdist-safe-patterns.md` documenting:
- Use `monkeypatch.setenv` not `os.environ` mutation
- Use `tmp_path` not shared temp dirs
- Use `patch.dict(sys.modules, ...)` for optional-dep mocking
- Use `@pytest.mark.xdist_group(name="...")` for tests sharing a singleton — **requires
  `--dist=loadgroup` in the pytest invocation**; without it the marker is parsed but does
  not provide serialization. Any pytest command or CI step that relies on xdist_group
  isolation must pass `--dist=loadgroup` (not `-n auto` alone).
- Module-level `CliRunner` instances are safe (stateless per `.invoke()`) but prefer fixtures

### Out of scope

- Refactoring deterministic tests that don't fail under parallelism
- Broad test rewrites outside race-prone areas

---

## Implementation Order

| PR | Workstream | Key files changed |
|----|-----------|------------------|
| PR-1 | Integration coverage hard gate | `pr-integration.yml` |
| PR-2 | xdist hardening audit | `scripts/ci/run-xdist-audit.sh`, `ci.yml` (optional), new `.claude/rules/` doc |
| PR-3 | Type-check gate expansion | `ci.yml`, `.pre-commit-config.yaml`, 6 one-line source fixes |
| PR-4 | Optional extras validation | `ci-extras.yml` (new), `tests/extras/` (new) |

## Definition of Done

- PRs fail if integration coverage floors regress (hard gate)
- 13 packages (136 files) are in the strict mypy CI gate, up from 1 (28 files)
- Staged mypy ratchet plan documented and tracked in issue #93
- 10 optional extras have an explicit CI validation contract
- xdist audit findings documented; confirmed races fixed; re-enablement decision made
- `.claude/rules/xdist-safe-patterns.md` committed
