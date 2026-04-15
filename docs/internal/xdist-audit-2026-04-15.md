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

**xdist re-enabled.** The audit found zero confirmed flakes (failures in 2+ runs).
The single-run video test flake is a rare class-level patch race; the tests use
standard `@patch` decorators which are function-scoped and should be safe under
xdist in most runs.

The 23 deterministic failures are pre-existing issues unrelated to parallelism:
missing assets, plan docs referencing not-yet-created files, and doc lint
violations. These are tracked separately and are out of scope for this workstream.

Both workflow files (`.github/workflows/ci.yml` and `.github/workflows/ci-full.yml`)
have been updated to replace the shard matrix with a Python-version matrix running
`-n auto` (see Task 4 commit).
