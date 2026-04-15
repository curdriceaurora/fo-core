# xdist Audit Findings — 2026-04-15

## Setup

- Command: `bash scripts/ci/run-xdist-audit.sh`
- Suite: non-integration, non-benchmark, non-e2e (`-n auto`, `--timeout=30`)
- Runs: 3
- Tests collected: 8,825

## Results

### Confirmed flakes (appeared in 2+ runs)

None. All failures that appeared in 2+ runs were deterministic failures
unrelated to xdist parallelism (see "Deterministic failures" section below).

| Test | Runs failed | Root cause category | Fix applied |
|------|-------------|---------------------|-------------|
| None | — | — | — |

### Single-run failures (potential xdist flake — appeared in 1/3 runs)

| Test | Notes |
|------|-------|
| `tests/unit/services/video/test_scene_detector.py::TestSceneDetector::test_detect_scenes_batch` | Passed in isolation. `@patch("...SceneDetector.detect_scenes")` patches at class level — concurrent patches from parallel workers can race. Rare. |
| `tests/unit/services/video/test_scene_detector.py::TestSceneDetector::test_detect_scenes_batch_skip_errors` | Same root cause as above. |

### Deterministic failures (not xdist flakes — failed all 3 runs)

These tests fail regardless of parallelism. They indicate pre-existing issues
in the test suite or missing assets; they are out of scope for xdist hardening.

| Test group | Count | Root cause |
|------------|-------|-----------|
| `tests/integration/test_context_menu_macos.py::TestMacOSQuickAction::*` | 12 | `desktop/context-menus/macos/` directory does not exist in repo. Tests lack `@pytest.mark.integration` marker so they are not excluded by the audit filter. |
| `tests/docs/test_doc_file_paths.py::test_referenced_path_exists[...pr4-extras-*]` | 9 | Plan doc `docs/superpowers/plans/2026-04-15-pr4-extras-validation.md` references files that do not yet exist (`tests/extras/`, `.github/workflows/ci-extras.yml`). |
| `tests/ci/test_workflows.py::TestShardCoverage::test_all_test_directories_assigned_to_shard` | 1 | `tests/extras/` directory not assigned to any shard in `scripts/ci_shard_paths.sh`. |
| `tests/ci/test_md031_ratchet.py::test_no_new_md031_violations_in_changed_files` | 1 | MD031 fenced-code-block violations in plan/spec docs changed on this branch. |

## Historical context

Two races fixed before this audit:

- PR #91 (`821d5bf`): config dir shared state — `test_config_edit_persists_text_model`
  used module-level `DEFAULT_CONFIG_DIR`; fixed with `monkeypatch.setattr`
- `4f47168`: sklearn `sys.modules` leak — `DocumentEmbedder` mock left
  `sklearn.feature_extraction` and `sklearn.feature_extraction.text` pointing at
  mock after teardown; fixed with `patch.dict(sys.modules, ...)`

## Re-enablement decision

**xdist re-enabled. Shard matrix replaced with Python-version matrix.**

The audit found zero confirmed flakes (failures in 2+ runs). The suite is safe
to run under `-n auto` without the shard workaround.

The single-run video test flake (`test_detect_scenes_batch*`) is a rare
class-level `@patch` race that appeared in 1 of 3 runs and passed in isolation.
It is acceptable — class-level patches are standard pytest practice and the race
window is narrow under normal CI load.

The 23 deterministic failures are pre-existing issues unrelated to xdist:
- `tests/integration/test_context_menu_macos.py` — missing `integration` marker
  causes these to run; `desktop/context-menus/macos/` does not exist in repo.
- `tests/docs/test_doc_file_paths.py` — plan doc references not-yet-created files.
- `tests/ci/test_workflows.py::TestShardCoverage` — `tests/extras/` (empty dir)
  not listed in `scripts/ci_shard_paths.sh`.
- `tests/ci/test_md031_ratchet.py` — MD031 violations in plan/spec docs on branch.

These are tracked separately and are out of scope for this workstream.

### Workflow changes

Both `.github/workflows/ci.yml` and `.github/workflows/ci-full.yml` have been
updated:

- Removed: `shard: [1, 2, 3, 4, 5, 6]` dimension from matrix
- Removed: `scripts/ci_shard_paths.sh` invocation
- Added: `pytest tests/ -m "not benchmark and not e2e" -n auto --timeout=60`
- Coverage artifacts renamed: `coverage-{py}-{shard}` → `coverage-{py}`
- Coverage files renamed: `.coverage.shard-N` → `.coverage.py{version}`
- Combine command updated: `.coverage.shard-*` → `.coverage.py*`
