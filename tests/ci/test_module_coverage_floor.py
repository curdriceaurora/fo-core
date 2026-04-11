"""Tests for scripts/check_module_coverage_floor.py.

These tests protect the CI policy parser/evaluator behavior so a formatting
change in pytest-cov or GH log prefixes does not silently change gate results.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_module_coverage_floor.py"

pytestmark = pytest.mark.ci


def _load_module():
    """
    Load and return the module defined by SCRIPT_PATH.
    
    Dynamically imports the Python file referenced by SCRIPT_PATH and returns the loaded module object.
    
    Returns:
        module: The imported module object.
    
    Raises:
        AssertionError: If the module spec or its loader could not be created.
    """
    spec = importlib.util.spec_from_file_location("check_module_coverage_floor", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_report_handles_actions_prefix_and_ansi(tmp_path: Path) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    report.write_text(
        (
            "plain text line that should be ignored\n"
            "Integration coverage gate\tstep\t2026-01-01T00:00:00Z "
            "\x1b[36msrc/file_organizer/cli/update.py  194  35  62  8  77%  30-44\x1b[0m\n"
            "Integration coverage gate\tstep\t2026-01-01T00:00:01Z "
            "src/file_organizer/services/intelligence/profile_merger.py  208  64  122  18  64%  53,114-123\n"
        ),
        encoding="utf-8",
    )

    parsed = module.parse_report(report)
    assert parsed == {
        "src/file_organizer/cli/update.py": 77.0,
        "src/file_organizer/services/intelligence/profile_merger.py": 64.0,
    }


def test_parse_report_ignores_non_module_rows(tmp_path: Path) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    report.write_text(
        (
            "TOTAL 27498 6999 7774 915 72%\n"
            "Name Stmts Miss Branch BrPart Cover Missing\n"
            "some random line\n"
        ),
        encoding="utf-8",
    )

    assert module.parse_report(report) == {}


def test_evaluate_flags_regressions_new_modules_and_missing_module_files(
    tmp_path: Path,
) -> None:
    module = _load_module()

    # Existing baseline module present on disk, but missing from report -> should flag.
    missing_module = tmp_path / "src" / "file_organizer" / "watcher" / "monitor.py"
    missing_module.parent.mkdir(parents=True, exist_ok=True)
    missing_module.write_text("# placeholder", encoding="utf-8")

    report_modules = {
        "src/file_organizer/cli/update.py": 70.0,  # regression vs floor 71.9
        "src/file_organizer/new_module.py": 60.0,  # new module below min
    }
    baseline_modules = {
        "src/file_organizer/cli/update.py": 71.9,
        "src/file_organizer/watcher/monitor.py": 24.0,  # missing from report
    }

    regressions, low_new_modules, missing_from_report = module.evaluate(
        report_modules,
        baseline_modules,
        tolerance=0.5,
        new_module_min=71.9,
        repo_root=tmp_path,
    )

    assert regressions == [("src/file_organizer/cli/update.py", 70.0, 71.9)]
    assert low_new_modules == [("src/file_organizer/new_module.py", 60.0)]
    assert missing_from_report == ["src/file_organizer/watcher/monitor.py"]


def test_evaluate_allows_small_tolerance_without_regression(tmp_path: Path) -> None:
    module = _load_module()

    report_modules = {"src/file_organizer/cli/update.py": 71.5}
    baseline_modules = {"src/file_organizer/cli/update.py": 71.9}

    regressions, low_new_modules, missing_from_report = module.evaluate(
        report_modules,
        baseline_modules,
        tolerance=0.5,
        new_module_min=71.9,
        repo_root=tmp_path,
    )

    assert regressions == []
    assert low_new_modules == []
    assert missing_from_report == []
