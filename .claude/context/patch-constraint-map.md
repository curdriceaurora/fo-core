# Test Patch Constraint Map

**Purpose**: Before refactoring any module, check this map. Private method patches in the test suite will break if targets are renamed, moved, or removed.

**Generated**: 2026-03-11 from Phase A lessons. Update after each refactoring PR.

---

## High-Risk Targets (>10 patches)

| Target | Patches | Risk | Phase B Impact |
|--------|---------|------|----------------|
| `cli.marketplace._service()` | 31 | CRITICAL | None (not in scope) |
| `cli.suggest._get_analyzer()` | 22 | CRITICAL | None |
| `cli.suggest._get_engine()` | 12 | HIGH | None |
| `cli.dedupe_v2._get_detector()` | 20 | CRITICAL | None |
| `core.organizer` imports (FileOrganizer, TextProcessor, VisionProcessor) | 28 | CRITICAL | **Task 6 (Pipeline)** |
| `web.organize_routes._build_job_view()` | 14 | HIGH | None |
| `services.video.scene_detector._check_dependencies()` | 18 | HIGH | None |
| `cli.daemon._DEFAULT_PID_FILE` | 14 | HIGH | None |

## Phase B Constraint Analysis

### Task 4 (Benchmarking Suite) — LOW RISK
New module (`cli/benchmark.py`). No existing patches to break.

### Task 5 (Model Lifecycle) — MEDIUM RISK
Touches model initialization. Patches to watch:
- `models._openai_client.OPENAI_AVAILABLE` (6 patches)
- `models.text_model.TextModel._enter_generate()` (1 patch)
- `models.text_model.TextModel._exit_generate()` (1 patch)
- All `core.organizer` patches that mock `TextProcessor`/`VisionProcessor` (16 patches)

### Task 6 (Composable Pipeline) — HIGH RISK
Refactors the orchestrator. **28 patches** target `file_organizer.core.organizer`:
- 12 patch `FileOrganizer` directly
- 9 patch `TextProcessor` imported through organizer
- 7 patch `VisionProcessor` imported through organizer

**Mitigation**: Keep delegation methods on `FileOrganizer` facade (learned in Phase A). If extracting pipeline stages, ensure `_process_text_files`, `_process_image_files`, etc. still exist as patch targets OR update all 28 patches simultaneously.

## Refactoring Checklist

Before renaming/removing any private method:

1. `grep -rn "patch.*<method_name>" tests/` — count dependent patches
2. If >5 patches: consider making public or keeping as thin delegation wrapper
3. If renaming: update ALL patches in the same commit
4. If extracting to new module: update patch target paths in same commit
5. Run full `pytest -m ci` after changes — broken patches fail immediately

## Full Patch Index by Module

### file_organizer.core (39 patches)
- `core.organizer.FileOrganizer` — 12 (cli/test_cli_*.py, test_main.py)
- `core.organizer.TextProcessor` — 9 (core/test_*, integration/test_*, e2e/conftest)
- `core.organizer.VisionProcessor` — 7 (core/test_*, integration/test_*, e2e/conftest)
- `core.hardware_profile._get_cpu_cores()` — 4
- `core.hardware_profile._detect_amd()` — 3
- `core.file_ops` / `core.initializer` — 4 (from decomposition)

### file_organizer.cli (106 patches)
- `cli.marketplace._service()` — 31
- `cli.suggest._get_analyzer()` — 22
- `cli.suggest._get_engine()` — 12
- `cli.suggest._collect_files()` — 5
- `cli.dedupe_v2._get_detector()` — 20
- `cli.daemon._DEFAULT_PID_FILE` — 14
- `cli.api._build_client()` — 7

### file_organizer.web (31 patches)
- `web.organize_routes._build_job_view()` — 14
- `web.organize_routes._list_organize_jobs()` — 3
- `web.organize_routes._cancel_scheduled_job()` — 2
- `web.organize_routes` (other private methods) — 6
- `web.settings_routes._SETTINGS_DIR` — 3
- `web.profile_routes` (_get_db, _AVATAR_DIR) — 2
- `web._helpers.base_context` — 2

### file_organizer.services (38 patches)
- `services.video.scene_detector._check_dependencies()` — 18
- `services.video.scene_detector._detect_with_*()` — 2
- `builtins.__import__` mocks (audio/dedup) — 14
- Other service patches — 4

### file_organizer.models (8 patches)
- `models._openai_client.OPENAI_AVAILABLE` — 6
- `models.text_model.TextModel._enter/_exit_generate()` — 2
