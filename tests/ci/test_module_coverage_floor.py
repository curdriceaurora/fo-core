"""Tests for scripts/check_module_coverage_floor.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_module_coverage_floor.py"

pytestmark = pytest.mark.ci


def _load_module():
    spec = importlib.util.spec_from_file_location("check_module_coverage_floor", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_main(module, monkeypatch: pytest.MonkeyPatch, args: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["check_module_coverage_floor.py", *args])
    return module.main()


def _write_report(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_parse_report_handles_actions_prefix_and_ansi(tmp_path: Path) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    report.write_text(
        (
            "plain text line that should be ignored\n"
            "Integration coverage gate\tstep\t2026-01-01T00:00:00Z "
            "\x1b[36msrc/cli/update.py  194  35  62  8  77%  30-44\x1b[0m\n"
            "Integration coverage gate\tstep\t2026-01-01T00:00:01Z "
            "src/services/intelligence/profile_merger.py  208  64  122  18  64%  53,114-123\n"
        ),
        encoding="utf-8",
    )

    parsed = module.parse_report(report)
    assert parsed == {
        "src/cli/update.py": 77.0,
        "src/services/intelligence/profile_merger.py": 64.0,
    }


def test_parse_report_parses_integer_coverage(tmp_path: Path) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(
        report,
        ["src/cli/main.py  100  12  40  6  88%  20-31"],
    )

    assert module.parse_report(report) == {"src/cli/main.py": 88.0}


def test_parse_report_ignores_non_module_rows(tmp_path: Path) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(
        report,
        [
            "TOTAL 27498 6999 7774 915 72%",
            "Name Stmts Miss Branch BrPart Cover Missing",
            "some random line",
            "",
        ],
    )

    assert module.parse_report(report) == {}


def test_evaluate_tolerance_boundary_for_regressions(tmp_path: Path) -> None:
    module = _load_module()

    report_modules = {
        "src/ok.py": 71.4,  # equals floor - tolerance => pass
        "src/fail.py": 71.3,  # below floor - tolerance => fail
    }
    baseline_modules = {
        "src/ok.py": 71.9,
        "src/fail.py": 71.9,
    }

    regressions, _, _ = module.evaluate(
        report_modules,
        baseline_modules,
        tolerance=0.5,
        new_module_min=71.9,
        repo_root=tmp_path,
    )

    assert regressions == [("src/fail.py", 71.3, 71.9)]


def test_evaluate_tolerance_boundary_for_new_modules(tmp_path: Path) -> None:
    module = _load_module()

    report_modules = {
        "src/new_ok.py": 71.4,  # equals min - tolerance => pass
        "src/new_fail.py": 71.3,  # below min - tolerance => fail
    }

    _, low_new_modules, _ = module.evaluate(
        report_modules,
        {},
        tolerance=0.5,
        new_module_min=71.9,
        repo_root=tmp_path,
    )

    assert low_new_modules == [("src/new_fail.py", 71.3)]


def test_evaluate_flags_missing_module_only_when_file_exists(tmp_path: Path) -> None:
    module = _load_module()

    existing_module = tmp_path / "src" / "watcher" / "monitor.py"
    existing_module.parent.mkdir(parents=True, exist_ok=True)
    existing_module.write_text("# placeholder", encoding="utf-8")

    baseline_modules = {
        "src/watcher/monitor.py": 24.0,
        "src/watcher/deleted.py": 10.0,
    }

    _, _, missing_from_report = module.evaluate(
        report_modules={},
        baseline_modules=baseline_modules,
        tolerance=0.5,
        new_module_min=71.9,
        repo_root=tmp_path,
    )

    assert missing_from_report == ["src/watcher/monitor.py"]


def test_main_returns_2_when_baseline_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(report, ["src/cli/update.py  10  0  0  0  100%"])

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(tmp_path / "missing-baseline.json"),
        ],
    )

    assert rc == 2


def test_main_returns_2_when_report_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"modules": {}}), encoding="utf-8")

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(tmp_path / "missing-report.txt"),
            "--baseline-path",
            str(baseline),
        ],
    )

    assert rc == 2


def test_main_returns_2_for_malformed_baseline_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(report, ["src/cli/update.py  10  0  0  0  100%"])

    baseline = tmp_path / "baseline.json"
    baseline.write_text("{not json}", encoding="utf-8")

    rc = _run_main(
        module,
        monkeypatch,
        ["--report-path", str(report), "--baseline-path", str(baseline)],
    )

    captured = capsys.readouterr().out
    assert rc == 2
    assert "ERROR: baseline file is not valid JSON" in captured


def test_main_returns_2_for_non_object_baseline_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(report, ["src/cli/update.py  10  0  0  0  100%"])

    baseline = tmp_path / "baseline.json"
    baseline.write_text("42", encoding="utf-8")

    rc = _run_main(
        module,
        monkeypatch,
        ["--report-path", str(report), "--baseline-path", str(baseline)],
    )

    captured = capsys.readouterr().out
    assert rc == 2
    assert "ERROR: Invalid baseline file" in captured
    assert "expected top-level JSON object" in captured


def test_main_returns_2_for_unparseable_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(report, ["this is not a pytest-cov module row"])

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"modules": {}}), encoding="utf-8")

    rc = _run_main(
        module,
        monkeypatch,
        ["--report-path", str(report), "--baseline-path", str(baseline)],
    )

    captured = capsys.readouterr().out
    assert rc == 2
    assert "ERROR: no module coverage rows parsed from report" in captured


def test_main_returns_0_when_all_modules_meet_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(report, ["src/cli/update.py  100  20  40  10  80%  10-30"])

    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"modules": {"src/cli/update.py": 79.8}}),
        encoding="utf-8",
    )

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0


def test_main_returns_1_when_regressions_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(report, ["src/cli/update.py  100  30  40  10  70%  10-30"])

    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"modules": {"src/cli/update.py": 71.9}}),
        encoding="utf-8",
    )

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 1


def test_main_uses_policy_defaults_from_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(report, ["src/new_module.py  100  31  20  5  69%  1-10"])

    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "policy": {"new_module_min_percent": 70.0, "tolerance_percent": 0.0},
                "modules": {},
            }
        ),
        encoding="utf-8",
    )

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--repo-root",
            str(tmp_path),
        ],
    )

    captured = capsys.readouterr().out
    assert rc == 1
    assert "effective_min=70.0%" in captured
    assert "(nominal=70.0% tolerance=0.0%)" in captured


def test_main_returns_2_for_invalid_baseline_floor_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    report = tmp_path / "integration.log"
    _write_report(report, ["src/cli/update.py  10  0  0  0  100%"])

    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"modules": {"src/cli/update.py": "not-a-number"}}),
        encoding="utf-8",
    )

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--repo-root",
            str(tmp_path),
        ],
    )

    captured = capsys.readouterr().out
    assert rc == 2
    assert "ERROR: invalid module floor value in baseline" in captured


def test_main_anchors_repo_root_to_script_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    existing_module_file = next((module._default_repo_root() / "src").rglob("*.py"))
    module_path = existing_module_file.relative_to(module._default_repo_root()).as_posix()

    report = tmp_path / "integration.log"
    _write_report(report, ["src/cli/any.py  10  0  0  0  100%"])

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"modules": {module_path: 0.0}}), encoding="utf-8")

    isolated_cwd = tmp_path / "isolated"
    isolated_cwd.mkdir()
    monkeypatch.chdir(isolated_cwd)

    rc = _run_main(
        module,
        monkeypatch,
        ["--report-path", str(report), "--baseline-path", str(baseline)],
    )

    captured = capsys.readouterr().out
    assert rc == 1
    assert "Baseline modules missing from coverage report:" in captured
    assert module_path in captured


def _make_baseline(tmp_path: Path, modules: dict[str, float]) -> Path:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-01-01T00:00:00+00:00",
                "source": {"workflow_run_id": 1, "job_id": 2, "commit": "abc"},
                "policy": {"new_module_min_percent": 71.9, "tolerance_percent": 0.5},
                "modules": modules,
            }
        ),
        encoding="utf-8",
    )
    return baseline


def test_update_baseline_raises_floor_when_coverage_improves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    report = tmp_path / "report.txt"
    _write_report(report, ["src/cli/update.py  100  5  40  2  95%"])

    baseline = _make_baseline(tmp_path, {"src/cli/update.py": 80.0})

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--update-baseline",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["modules"]["src/cli/update.py"] == 95.0
    captured = capsys.readouterr().out
    assert "80.0% → 95.0%" in captured


def test_update_baseline_ratchet_does_not_lower_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When actual coverage drops below the existing floor, the floor must not be lowered."""
    module = _load_module()

    report = tmp_path / "report.txt"
    _write_report(report, ["src/cli/update.py  100  30  40  10  70%"])

    baseline = _make_baseline(tmp_path, {"src/cli/update.py": 80.0})

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--update-baseline",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["modules"]["src/cli/update.py"] == 80.0


def test_update_baseline_adds_new_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    report = tmp_path / "report.txt"
    _write_report(report, ["src/new_module.py  50  5  10  1  88%"])

    baseline = _make_baseline(tmp_path, {})

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--update-baseline",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["modules"]["src/new_module.py"] == 88.0
    captured = capsys.readouterr().out
    assert "Adding 1 new module" in captured
    assert "src/new_module.py: 88.0%" in captured


def test_update_baseline_removes_deleted_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Modules in baseline but absent from report AND disk should be removed."""
    module = _load_module()

    report = tmp_path / "report.txt"
    _write_report(report, ["src/kept.py  10  0  0  0  100%"])

    baseline = _make_baseline(
        tmp_path,
        {
            "src/kept.py": 100.0,
            "src/deleted.py": 50.0,  # not on disk, not in report
        },
    )

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--update-baseline",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert "src/deleted.py" not in data["modules"]
    assert "src/kept.py" in data["modules"]
    captured = capsys.readouterr().out
    assert "Removing 1 deleted module" in captured


def test_update_baseline_keeps_floor_for_module_still_on_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Module missing from report but still on disk should keep its floor (not removed)."""
    module = _load_module()

    on_disk = tmp_path / "src" / "missing_from_report.py"
    on_disk.parent.mkdir(parents=True, exist_ok=True)
    on_disk.write_text("# placeholder", encoding="utf-8")

    report = tmp_path / "report.txt"
    _write_report(report, ["src/present.py  10  0  0  0  100%"])

    baseline = _make_baseline(
        tmp_path,
        {
            "src/present.py": 100.0,
            "src/missing_from_report.py": 75.0,
        },
    )

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--update-baseline",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["modules"]["src/missing_from_report.py"] == 75.0


def test_update_baseline_dry_run_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    report = tmp_path / "report.txt"
    _write_report(report, ["src/cli/update.py  100  5  40  2  95%"])

    original_content = json.dumps(
        {
            "generated_at_utc": "2026-01-01T00:00:00+00:00",
            "source": {"workflow_run_id": 1, "job_id": 2, "commit": "abc"},
            "policy": {"new_module_min_percent": 71.9, "tolerance_percent": 0.5},
            "modules": {"src/cli/update.py": 80.0},
        }
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(original_content, encoding="utf-8")

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--update-baseline",
            "--dry-run",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0
    assert baseline.read_text(encoding="utf-8") == original_content
    captured = capsys.readouterr().out
    assert "dry-run" in captured.lower()


def test_update_baseline_updates_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()

    report = tmp_path / "report.txt"
    _write_report(report, ["src/cli/update.py  10  0  0  0  100%"])

    baseline = _make_baseline(tmp_path, {"src/cli/update.py": 100.0})

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--update-baseline",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert rc == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert "generated_at_utc" in data
    assert data["generated_at_utc"] != "2026-01-01T00:00:00+00:00"
    assert data["source"]["workflow_run_id"] is None


def test_dry_run_without_update_baseline_returns_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    report = tmp_path / "report.txt"
    _write_report(report, ["src/cli/update.py  10  0  0  0  100%"])

    baseline = _make_baseline(tmp_path, {"src/cli/update.py": 100.0})

    rc = _run_main(
        module,
        monkeypatch,
        [
            "--report-path",
            str(report),
            "--baseline-path",
            str(baseline),
            "--dry-run",
        ],
    )

    assert rc == 2
    captured = capsys.readouterr().out
    assert "ERROR" in captured
    assert "--update-baseline" in captured


def test_print_report_shows_effective_thresholds_and_guidance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()

    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"modules": {}}), encoding="utf-8")

    module.print_report(
        report_modules={"src/cli/update.py": 70.0},
        regressions=[("src/cli/update.py", 70.0, 71.9)],
        low_new_modules=[("src/new_module.py", 60.0)],
        missing_from_report=["src/watcher/monitor.py"],
        new_module_min=71.9,
        tolerance=0.5,
        baseline_path=baseline_path,
    )

    captured = capsys.readouterr().out
    assert "effective_floor=71.4%" in captured
    assert "effective_min=71.4%" in captured
    assert "This usually means they have lost all integration test coverage." in captured
    assert str(baseline_path) in captured
