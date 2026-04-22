"""Integration tests for the five under-covered CLI modules.

Target modules and coverage goals:
- cli/benchmark.py      53% → ≥80%  (suite runners, run command, output helpers)
- cli/analytics.py      57% → ≥80%  (analytics_command, display helpers)
- cli/undo_redo.py      67% → ≥80%  (dry-run paths, transaction, verbose)
- cli/dedupe_display.py 71% → ≥80%  (format_size, display functions)
- cli/utilities.py      71% → ≥80%  (search edge paths, analyze verbose/json)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from models.analytics import (
        AnalyticsDashboard,
        DuplicateStats,
        FileDistribution,
        QualityMetrics,
        StorageStats,
        TimeSavings,
    )

pytestmark = pytest.mark.integration


# ===========================================================================
# benchmark.py — suite runners, run command, output helpers
# ===========================================================================


class TestBenchmarkRunCommand:
    """Exercise the ``benchmark run`` CLI command via typer invocation."""

    def test_run_nonexistent_path_exits_1(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        result = cli_runner.invoke(
            app, ["benchmark", "run", str(tmp_path / "gone"), "--iterations", "1", "--warmup", "0"]
        )
        assert result.exit_code == 1
        assert "does not exist" in result.output.lower() or "error" in result.output.lower()

    def test_run_empty_dir_text_output(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        empty = tmp_path / "empty"
        empty.mkdir()
        result = cli_runner.invoke(
            app, ["benchmark", "run", str(empty), "--iterations", "1", "--warmup", "0"]
        )
        assert result.exit_code == 0
        assert "no files" in result.output.lower() or "yellow" in result.output.lower()

    def test_run_empty_dir_json_output(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        empty = tmp_path / "empty"
        empty.mkdir()
        result = cli_runner.invoke(
            app,
            ["benchmark", "run", str(empty), "--iterations", "1", "--warmup", "0", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output.strip())
        assert data["files_count"] == 0
        assert "results" in data
        assert data["results"]["iterations"] == 0

    def test_run_unknown_suite_exits_1(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "file.txt").write_text("hello")
        result = cli_runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(src),
                "--suite",
                "nonexistent",
                "--iterations",
                "1",
                "--warmup",
                "0",
            ],
        )
        assert result.exit_code == 1
        assert "unknown suite" in result.output.lower()

    def test_run_io_suite_text_output(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("content a")
        (src / "b.txt").write_text("content b")
        result = cli_runner.invoke(
            app,
            ["benchmark", "run", str(src), "--suite", "io", "--iterations", "2", "--warmup", "1"],
        )
        assert result.exit_code == 0
        assert "benchmark" in result.output.lower() or "completed" in result.output.lower()

    def test_run_io_suite_json_output(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("x")
        result = cli_runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(src),
                "--suite",
                "io",
                "--iterations",
                "2",
                "--warmup",
                "0",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output.strip())
        assert data["suite"] == "io"
        assert "results" in data
        assert data["results"]["iterations"] == 2

    def test_run_with_compare_file(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("x")

        baseline = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 10.0,
                "p95_ms": 20.0,
                "p99_ms": 25.0,
                "stddev_ms": 2.0,
                "throughput_fps": 100.0,
                "iterations": 5,
            },
        }
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline))

        result = cli_runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(src),
                "--suite",
                "io",
                "--iterations",
                "2",
                "--warmup",
                "0",
                "--compare",
                str(baseline_path),
            ],
        )
        assert result.exit_code == 0
        assert (
            "comparison" in result.output.lower()
            or "regression" in result.output.lower()
            or "no regression" in result.output.lower()
        )

    def test_run_with_compare_file_json(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("x")

        baseline = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 10.0,
                "p95_ms": 20.0,
                "p99_ms": 25.0,
                "stddev_ms": 2.0,
                "throughput_fps": 100.0,
                "iterations": 5,
            },
        }
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline))

        result = cli_runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(src),
                "--suite",
                "io",
                "--iterations",
                "2",
                "--warmup",
                "0",
                "--compare",
                str(baseline_path),
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output.strip())
        assert "comparison" in data

    def test_run_invalid_compare_file_exits_1(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("x")

        bad_baseline = tmp_path / "bad.json"
        bad_baseline.write_text("not valid json {{{{")

        result = cli_runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(src),
                "--suite",
                "io",
                "--iterations",
                "1",
                "--warmup",
                "0",
                "--compare",
                str(bad_baseline),
            ],
        )
        assert result.exit_code == 1

    def test_run_warmup_zero_iterations_1(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "file.md").write_text("# Heading\n\ncontent")
        result = cli_runner.invoke(
            app,
            ["benchmark", "run", str(src), "--suite", "io", "--iterations", "1", "--warmup", "0"],
        )
        assert result.exit_code == 0


class TestRunTextSuite:
    """Exercise _run_text_suite directly via benchmark run --suite text."""

    def test_text_suite_with_text_files(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "doc.txt").write_text("This is a document for benchmarking text processing.")

        result = cli_runner.invoke(
            app,
            ["benchmark", "run", str(src), "--suite", "text", "--iterations", "1", "--warmup", "0"],
        )
        assert result.exit_code == 0

    def test_text_suite_no_text_files_degrades(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "photo.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)

        result = cli_runner.invoke(
            app,
            ["benchmark", "run", str(src), "--suite", "text", "--iterations", "1", "--warmup", "0"],
        )
        assert result.exit_code == 0
        # Degraded but completes

    def test_text_suite_json_degraded(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "photo.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)

        result = cli_runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(src),
                "--suite",
                "text",
                "--iterations",
                "1",
                "--warmup",
                "0",
                "--json",
            ],
        )
        assert result.exit_code == 0
        # The output contains a JSON object — find the braces and parse it
        raw = result.output.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        assert data["degraded"] is True
        assert len(data["degradation_reasons"]) >= 1


class TestBenchmarkSuiteHelpers:
    """Direct unit-style tests for suite classification and output helpers."""

    def test_run_text_suite_directly_with_text_file(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_text_suite

        txt = tmp_path / "doc.txt"
        txt.write_text("Hello benchmark world")
        outcome = _run_text_suite([txt])
        assert outcome.processed_count == 1

    def test_run_text_suite_no_candidates(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_text_suite

        jpg = tmp_path / "photo.jpg"
        jpg.write_bytes(b"\xff\xd8\xff")
        outcome = _run_text_suite([jpg])
        assert outcome.processed_count == 0

    def test_run_vision_suite_no_candidates(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_vision_suite

        txt = tmp_path / "doc.txt"
        txt.write_text("text only")
        outcome = _run_vision_suite([txt])
        assert outcome.processed_count == 0

    def test_run_audio_suite_no_candidates_falls_back_to_io(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_audio_suite

        txt = tmp_path / "doc.txt"
        txt.write_text("text only")
        outcome = _run_audio_suite([txt])
        # Falls back to io suite, so processes the text file
        assert outcome.processed_count == 1

    def test_run_io_suite_with_multiple_files(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_io_suite

        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.md").write_text("b")
        files = [tmp_path / "a.txt", tmp_path / "b.md"]
        outcome = _run_io_suite(files)
        assert outcome.processed_count == 2

    def test_classify_text_suite_no_candidates(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_text_suite, _SuiteIterationOutcome

        jpg = tmp_path / "photo.jpg"
        jpg.write_bytes(b"\xff\xd8\xff")
        outcome = _SuiteIterationOutcome(processed_count=0)
        result = _classify_text_suite([jpg], outcome)
        assert result.degraded is True
        assert "text-no-candidates-skip" in result.degradation_reasons

    def test_classify_vision_suite_no_candidates(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_vision_suite, _SuiteIterationOutcome

        txt = tmp_path / "doc.txt"
        txt.write_text("text")
        outcome = _SuiteIterationOutcome(processed_count=0)
        result = _classify_vision_suite([txt], outcome)
        assert result.degraded is True

    def test_classify_audio_suite_no_candidates(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_audio_suite, _SuiteIterationOutcome

        txt = tmp_path / "doc.txt"
        txt.write_text("text")
        outcome = _SuiteIterationOutcome(processed_count=1)
        result = _classify_audio_suite([txt], outcome)
        assert result.degraded is True
        assert "audio-no-candidates-fallback-to-io" in result.degradation_reasons

    def test_classify_audio_suite_synthetic_metadata(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_audio_suite, _SuiteIterationOutcome

        mp3 = tmp_path / "track.mp3"
        mp3.write_bytes(b"\xff\xfb" + b"\x00" * 100)
        outcome = _SuiteIterationOutcome(processed_count=1, used_synthetic_audio_metadata=True)
        result = _classify_audio_suite([mp3], outcome)
        assert result.degraded is True
        assert "audio-synthesized-metadata-fallback" in result.degradation_reasons

    def test_classify_e2e_suite_no_processed(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_e2e_suite, _SuiteIterationOutcome

        txt = tmp_path / "file.txt"
        txt.write_text("x")
        outcome = _SuiteIterationOutcome(processed_count=0)
        result = _classify_e2e_suite([txt], outcome)
        assert result.degraded is True

    def test_classify_e2e_suite_success(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_e2e_suite, _SuiteIterationOutcome

        txt = tmp_path / "file.txt"
        txt.write_text("x")
        outcome = _SuiteIterationOutcome(processed_count=1)
        result = _classify_e2e_suite([txt], outcome)
        assert result.degraded is False

    def test_detect_hardware_profile_returns_dict(self) -> None:
        from cli.benchmark import _detect_hardware_profile

        result = _detect_hardware_profile()
        assert isinstance(result, dict)
        assert "cpu_cores" in result or "platform" in result or len(result) > 0

    def test_check_baseline_profile_compatibility_same_version(self) -> None:
        from cli.benchmark import (
            _RUNNER_PROFILE_VERSION,
            _check_baseline_profile_compatibility,
        )

        mock_console = MagicMock()
        baseline = {"runner_profile_version": _RUNNER_PROFILE_VERSION}
        warning = _check_baseline_profile_compatibility(
            baseline, suite="io", console=mock_console, json_output=False
        )
        assert warning is None

    def test_check_baseline_profile_compatibility_mismatch(self) -> None:
        from cli.benchmark import _check_baseline_profile_compatibility

        mock_console = MagicMock()
        baseline = {"runner_profile_version": "old-version-v0"}
        warning = _check_baseline_profile_compatibility(
            baseline, suite="io", console=mock_console, json_output=False
        )
        assert warning is not None
        assert "mismatch" in warning.lower()
        mock_console.print.assert_called_once()

    def test_check_baseline_profile_compatibility_mismatch_json(self) -> None:
        from cli.benchmark import _check_baseline_profile_compatibility

        mock_console = MagicMock()
        baseline = {"runner_profile_version": "old-version-v0"}
        warning = _check_baseline_profile_compatibility(
            baseline, suite="io", console=mock_console, json_output=True
        )
        assert warning is not None
        mock_console.print.assert_not_called()

    def test_check_baseline_profile_compatibility_no_version(self) -> None:
        from cli.benchmark import _check_baseline_profile_compatibility

        mock_console = MagicMock()
        baseline: dict = {}
        warning = _check_baseline_profile_compatibility(
            baseline, suite="io", console=mock_console, json_output=False
        )
        assert warning is None

    def test_print_table_runs_without_error(self) -> None:
        from cli.benchmark import BenchmarkStats, _print_table

        mock_console = MagicMock()
        stats = BenchmarkStats(
            median_ms=5.0,
            p95_ms=10.0,
            p99_ms=12.0,
            stddev_ms=1.5,
            throughput_fps=200.0,
            iterations=10,
        )
        _print_table(mock_console, "io", 3, stats, 10)
        mock_console.print.assert_called_once()

    def test_print_comparison_no_regression_text(self) -> None:
        from cli.benchmark import _print_comparison

        mock_console = MagicMock()
        comp = {
            "deltas_pct": {
                "median_ms": 5.0,
                "p95_ms": -3.0,
                "p99_ms": 2.0,
                "stddev_ms": 1.0,
                "throughput_fps": 8.0,
            },
            "regression": False,
            "threshold": 1.2,
        }
        _print_comparison(mock_console, comp, json_output=False)
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("no regression" in c.lower() for c in calls)

    def test_print_comparison_regression_text(self) -> None:
        from cli.benchmark import _print_comparison

        mock_console = MagicMock()
        comp = {
            "deltas_pct": {
                "median_ms": 30.0,
                "p95_ms": 25.0,
                "p99_ms": 22.0,
                "stddev_ms": 5.0,
                "throughput_fps": -25.0,
            },
            "regression": True,
            "threshold": 1.2,
        }
        _print_comparison(mock_console, comp, json_output=False)
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("regression" in c.lower() for c in calls)

    def test_print_comparison_json_output(self) -> None:
        from cli.benchmark import _print_comparison

        mock_console = MagicMock()
        comp = {
            "deltas_pct": {"median_ms": 0.0},
            "regression": False,
            "threshold": 1.2,
        }
        _print_comparison(mock_console, comp, json_output=True)
        mock_console.print.assert_called_once()
        call_arg = mock_console.print.call_args[0][0]
        parsed = json.loads(call_arg)
        assert "comparison" in parsed

    def test_summarize_suite_classifications_single(self) -> None:
        from cli.benchmark import (
            _SuiteExecutionClassification,
            _summarize_suite_classifications,
        )

        c = _SuiteExecutionClassification(effective_suite="io", degraded=False)
        eff, degraded, reasons = _summarize_suite_classifications(
            [c, c, c], warmup=1, requested_suite="io"
        )
        assert eff == "io"
        assert degraded is False
        assert reasons == []

    def test_summarize_suite_classifications_mixed_effective_suites(self) -> None:
        from cli.benchmark import (
            _SuiteExecutionClassification,
            _summarize_suite_classifications,
        )

        c1 = _SuiteExecutionClassification(effective_suite="io", degraded=False)
        c2 = _SuiteExecutionClassification(effective_suite="text", degraded=False)
        eff, _degraded, _reasons = _summarize_suite_classifications(
            [c1, c2], warmup=0, requested_suite="text"
        )
        assert eff == "mixed"

    def test_summarize_suite_classifications_empty_after_warmup(self) -> None:
        from cli.benchmark import (
            _SuiteExecutionClassification,
            _summarize_suite_classifications,
        )

        c = _SuiteExecutionClassification(effective_suite="io", degraded=False)
        eff, _degraded, _reasons = _summarize_suite_classifications(
            [c], warmup=1, requested_suite="io"
        )
        # warmup eats the only iteration → empty measured → falls back to requested_suite
        assert eff == "io"

    def test_execute_suite_iteration_success(self, tmp_path: Path) -> None:
        from cli.benchmark import (
            _execute_suite_iteration,
            _SuiteExecutionClassification,
            _SuiteIterationOutcome,
        )

        def fake_runner(files: list[Path]) -> _SuiteIterationOutcome:
            return _SuiteIterationOutcome(processed_count=2)

        def fake_classifier(
            files: list[Path], outcome: _SuiteIterationOutcome
        ) -> _SuiteExecutionClassification:
            return _SuiteExecutionClassification(effective_suite="io", degraded=False)

        mock_console = MagicMock()
        _elapsed, count, classification = _execute_suite_iteration(
            runner=fake_runner,
            classifier=fake_classifier,
            files=[tmp_path / "a.txt", tmp_path / "b.txt"],
            suite="io",
            console=mock_console,
        )
        assert count == 2
        assert classification.effective_suite == "io"

    def test_execute_suite_iteration_runner_exception(self, tmp_path: Path) -> None:
        import typer

        from cli.benchmark import _execute_suite_iteration

        def bad_runner(files: list[Path]):  # type: ignore[return]
            raise RuntimeError("runner exploded")

        mock_console = MagicMock()
        with pytest.raises(typer.Exit):
            _execute_suite_iteration(
                runner=bad_runner,
                classifier=MagicMock(),
                files=[],
                suite="io",
                console=mock_console,
            )
        mock_console.print.assert_called_once()

    def test_maybe_attach_comparison_output_no_compare(self, tmp_path: Path) -> None:
        from cli.benchmark import _maybe_attach_comparison_output

        output = {"suite": "io", "results": {"median_ms": 5.0}}
        result = _maybe_attach_comparison_output(
            output=output,
            compare_path=None,
            suite="io",
            console=MagicMock(),
            json_output=False,
        )
        assert result is output
        assert "comparison" not in result

    def test_maybe_attach_comparison_output_with_compare(self, tmp_path: Path) -> None:
        from cli.benchmark import _maybe_attach_comparison_output

        baseline = {
            "results": {
                "median_ms": 10.0,
                "p95_ms": 20.0,
                "p99_ms": 25.0,
                "stddev_ms": 2.0,
                "throughput_fps": 50.0,
            }
        }
        compare_path = tmp_path / "baseline.json"
        compare_path.write_text(json.dumps(baseline))

        output = {
            "suite": "io",
            "results": {
                "median_ms": 5.0,
                "p95_ms": 10.0,
                "p99_ms": 12.0,
                "stddev_ms": 1.0,
                "throughput_fps": 100.0,
            },
        }
        result = _maybe_attach_comparison_output(
            output=output,
            compare_path=compare_path,
            suite="io",
            console=MagicMock(),
            json_output=False,
        )
        assert "comparison" in result

    def test_maybe_attach_comparison_unreadable_exits_1(self, tmp_path: Path) -> None:
        import typer

        from cli.benchmark import _maybe_attach_comparison_output

        bad_path = tmp_path / "nonexistent.json"
        with pytest.raises(typer.Exit):
            _maybe_attach_comparison_output(
                output={},
                compare_path=bad_path,
                suite="io",
                console=MagicMock(),
                json_output=False,
            )


# ===========================================================================
# analytics.py — analytics_command, display helpers, format utilities
# ===========================================================================


class TestAnalyticsFormatBytes:
    def test_bytes_range(self) -> None:
        from cli.analytics import _format_bytes

        assert _format_bytes(500) == "500.0 B"
        assert _format_bytes(0) == "0.0 B"

    def test_kilobytes(self) -> None:
        from cli.analytics import _format_bytes

        assert _format_bytes(2048) == "2.0 KB"

    def test_megabytes(self) -> None:
        from cli.analytics import _format_bytes

        assert _format_bytes(1024 * 1024 * 3) == "3.0 MB"

    def test_gigabytes(self) -> None:
        from cli.analytics import _format_bytes

        assert _format_bytes(1024**3 * 2) == "2.0 GB"

    def test_terabytes(self) -> None:
        from cli.analytics import _format_bytes

        assert _format_bytes(1024**4 * 2) == "2.0 TB"

    def test_petabytes(self) -> None:
        from cli.analytics import _format_bytes

        assert _format_bytes(1024**5 * 2) == "2.0 PB"


class TestAnalyticsFormatDuration:
    def test_seconds(self) -> None:
        from cli.analytics import _format_duration

        assert _format_duration(30.0) == "30.0s"

    def test_minutes(self) -> None:
        from cli.analytics import _format_duration

        result = _format_duration(120.0)
        assert "m" in result
        assert "2.0" in result

    def test_hours(self) -> None:
        from cli.analytics import _format_duration

        result = _format_duration(7200.0)
        assert "h" in result
        assert "2.0" in result

    def test_boundary_exactly_60(self) -> None:
        from cli.analytics import _format_duration

        result = _format_duration(60.0)
        assert "m" in result


class TestAnalyticsDisplayHelpers:
    def _make_storage_stats(self) -> StorageStats:
        from datetime import UTC, datetime

        from models.analytics import FileInfo, StorageStats

        return StorageStats(
            total_size=1024 * 1024 * 10,
            organized_size=1024 * 1024 * 8,
            file_count=100,
            directory_count=5,
            saved_size=1024 * 1024,
            size_by_type={".txt": 500000, ".pdf": 500000},
            largest_files=[
                FileInfo(
                    path=Path("/tmp/large.pdf"),
                    size=500000,
                    type=".pdf",
                    modified=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ],
        )

    def _make_quality_metrics(self) -> QualityMetrics:
        from models.analytics import QualityMetrics

        return QualityMetrics(
            quality_score=75.0,
            naming_compliance=0.85,
            structure_consistency=0.72,
            metadata_completeness=0.60,
            categorization_accuracy=0.90,
        )

    def _make_duplicate_stats_no_dupes(self) -> DuplicateStats:
        from models.analytics import DuplicateStats

        return DuplicateStats(
            total_duplicates=0,
            duplicate_groups=0,
            space_wasted=0,
            space_recoverable=0,
            by_type={},
        )

    def _make_duplicate_stats_with_dupes(self) -> DuplicateStats:
        from models.analytics import DuplicateStats

        return DuplicateStats(
            total_duplicates=5,
            duplicate_groups=2,
            space_wasted=1024 * 100,
            space_recoverable=1024 * 80,
            by_type={".txt": 3, ".pdf": 2},
        )

    def _make_time_savings(self) -> TimeSavings:
        from models.analytics import TimeSavings

        return TimeSavings(
            total_operations=200,
            automated_operations=180,
            manual_time_seconds=3600,
            automated_time_seconds=360,
            estimated_time_saved_seconds=3240,
        )

    def _make_file_distribution(self) -> FileDistribution:
        from models.analytics import FileDistribution

        return FileDistribution(
            total_files=150,
            by_type={".txt": 50, ".pdf": 40, ".jpg": 30, ".md": 30},
            by_size_range={"small": 80, "medium": 50, "large": 20},
        )

    def test_display_storage_stats_with_chart(self) -> None:
        from cli.analytics import display_storage_stats
        from utils.chart_generator import ChartGenerator

        chart_gen = ChartGenerator(use_unicode=True)
        stats = self._make_storage_stats()
        display_storage_stats(stats, chart_gen)

    def test_display_storage_stats_no_chart(self) -> None:
        from cli.analytics import display_storage_stats

        stats = self._make_storage_stats()
        display_storage_stats(stats, None)

    def test_display_quality_metrics_high_score(self) -> None:
        from cli.analytics import display_quality_metrics

        metrics = self._make_quality_metrics()
        display_quality_metrics(metrics)

    def test_display_quality_metrics_low_score(self) -> None:
        from cli.analytics import display_quality_metrics
        from models.analytics import QualityMetrics

        metrics = QualityMetrics(
            quality_score=30.0,
            naming_compliance=0.30,
            structure_consistency=0.20,
            metadata_completeness=0.10,
            categorization_accuracy=0.25,
        )
        display_quality_metrics(metrics)

    def test_display_quality_metrics_medium_score(self) -> None:
        from cli.analytics import display_quality_metrics
        from models.analytics import QualityMetrics

        metrics = QualityMetrics(
            quality_score=55.0,
            naming_compliance=0.55,
            structure_consistency=0.60,
            metadata_completeness=0.55,
            categorization_accuracy=0.60,
        )
        display_quality_metrics(metrics)

    def test_display_duplicate_stats_no_dupes(self) -> None:
        from cli.analytics import display_duplicate_stats

        stats = self._make_duplicate_stats_no_dupes()
        display_duplicate_stats(stats)

    def test_display_duplicate_stats_with_dupes(self) -> None:
        from cli.analytics import display_duplicate_stats

        stats = self._make_duplicate_stats_with_dupes()
        display_duplicate_stats(stats)

    def test_display_time_savings(self) -> None:
        from cli.analytics import display_time_savings

        savings = self._make_time_savings()
        display_time_savings(savings)

    def test_display_file_distribution_with_chart(self) -> None:
        from cli.analytics import display_file_distribution
        from utils.chart_generator import ChartGenerator

        chart_gen = ChartGenerator(use_unicode=True)
        distribution = self._make_file_distribution()
        display_file_distribution(distribution, chart_gen)

    def test_display_file_distribution_no_chart(self) -> None:
        from cli.analytics import display_file_distribution

        distribution = self._make_file_distribution()
        display_file_distribution(distribution, None)


def _make_full_dashboard() -> AnalyticsDashboard:
    """Build a real AnalyticsDashboard with all required fields populated."""
    from datetime import UTC, datetime

    from models.analytics import (
        AnalyticsDashboard,
        DuplicateStats,
        FileDistribution,
        FileInfo,
        QualityMetrics,
        StorageStats,
        TimeSavings,
    )

    storage = StorageStats(
        total_size=1024 * 1024 * 100,
        organized_size=1024 * 1024 * 80,
        saved_size=1024 * 1024 * 10,
        file_count=500,
        directory_count=20,
        size_by_type={".txt": 1024 * 1024 * 50, ".pdf": 1024 * 1024 * 50},
        largest_files=[
            FileInfo(
                path=Path("/tmp/doc.pdf"),
                size=1024 * 1024 * 5,
                type=".pdf",
                modified=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
    )
    dist = FileDistribution(
        total_files=500,
        by_type={".txt": 200, ".pdf": 150, ".jpg": 100, ".md": 50},
        by_size_range={"small": 300, "medium": 150, "large": 50},
    )
    dupes = DuplicateStats(
        total_duplicates=10,
        duplicate_groups=4,
        space_wasted=1024 * 1024 * 2,
        space_recoverable=1024 * 1024 * 1,
        by_type={".txt": 5, ".pdf": 5},
    )
    quality = QualityMetrics(
        quality_score=78.5,
        naming_compliance=0.82,
        structure_consistency=0.75,
        metadata_completeness=0.68,
        categorization_accuracy=0.88,
    )
    savings = TimeSavings(
        total_operations=1000,
        automated_operations=900,
        manual_time_seconds=7200,
        automated_time_seconds=720,
        estimated_time_saved_seconds=6480,
    )
    return AnalyticsDashboard(
        storage_stats=storage,
        file_distribution=dist,
        duplicate_stats=dupes,
        quality_metrics=quality,
        time_savings=savings,
        generated_at=datetime(2026, 4, 11, 12, 0, 0, tzinfo=UTC),
    )


class TestAnalyticsCommand:
    def test_nonexistent_directory_returns_1(self, tmp_path: Path) -> None:
        from cli.analytics import analytics_command

        result = analytics_command([str(tmp_path / "gone")])
        assert result == 1

    def test_file_instead_of_directory_returns_1(self, tmp_path: Path) -> None:
        from cli.analytics import analytics_command

        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = analytics_command([str(f)])
        assert result == 1

    def test_valid_directory_returns_0(self, tmp_path: Path) -> None:
        from cli.analytics import analytics_command

        src = tmp_path / "src"
        src.mkdir()
        (src / "doc.txt").write_text("Some content")
        dashboard = _make_full_dashboard()
        with patch("cli.analytics.AnalyticsService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc
            mock_svc.generate_dashboard.return_value = dashboard
            result = analytics_command([str(src)])
        assert result == 0

    def test_with_max_depth(self, tmp_path: Path) -> None:
        from cli.analytics import analytics_command

        src = tmp_path / "src"
        src.mkdir()
        dashboard = _make_full_dashboard()
        with patch("cli.analytics.AnalyticsService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc
            mock_svc.generate_dashboard.return_value = dashboard
            result = analytics_command([str(src), "--max-depth", "2"])
        assert result == 0
        mock_svc.generate_dashboard.assert_called_once()
        assert mock_svc.generate_dashboard.call_args.kwargs["max_depth"] == 2

    def test_with_export_json(self, tmp_path: Path) -> None:
        from cli.analytics import analytics_command

        src = tmp_path / "src"
        src.mkdir()
        export_path = tmp_path / "report.json"
        dashboard = _make_full_dashboard()
        with patch("cli.analytics.AnalyticsService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc
            mock_svc.generate_dashboard.return_value = dashboard
            result = analytics_command([str(src), "--export", str(export_path)])
        assert result == 0
        mock_svc.export_dashboard.assert_called_once()

    def test_with_no_charts_flag(self, tmp_path: Path) -> None:
        from cli.analytics import analytics_command

        src = tmp_path / "src"
        src.mkdir()
        dashboard = _make_full_dashboard()
        with (
            patch("cli.analytics.AnalyticsService") as mock_svc_cls,
            patch("cli.analytics.ChartGenerator") as mock_cg,
        ):
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc
            mock_svc.generate_dashboard.return_value = dashboard
            result = analytics_command([str(src), "--no-charts"])
        assert result == 0
        mock_svc.generate_dashboard.assert_called_once()
        mock_cg.assert_not_called()

    def test_exception_returns_1(self, tmp_path: Path) -> None:
        from cli.analytics import analytics_command

        src = tmp_path / "src"
        src.mkdir()
        with patch(
            "cli.analytics.AnalyticsService",
            side_effect=RuntimeError("service blew up"),
        ):
            result = analytics_command([str(src)])
        assert result == 1

    def test_verbose_flag(self, tmp_path: Path) -> None:
        from cli.analytics import analytics_command

        src = tmp_path / "src"
        src.mkdir()
        dashboard = _make_full_dashboard()
        with patch("cli.analytics.AnalyticsService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc
            mock_svc.generate_dashboard.return_value = dashboard
            result = analytics_command([str(src), "--verbose"])
        assert result == 0
        mock_svc.generate_dashboard.assert_called_once()

    def test_export_text_format(self, tmp_path: Path) -> None:
        from cli.analytics import analytics_command

        src = tmp_path / "src"
        src.mkdir()
        export_path = tmp_path / "report.txt"
        dashboard = _make_full_dashboard()
        with patch("cli.analytics.AnalyticsService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc
            mock_svc.generate_dashboard.return_value = dashboard
            result = analytics_command([str(src), "--export", str(export_path), "--format", "text"])
        assert result == 0
        mock_svc.export_dashboard.assert_called_once()
        assert mock_svc.export_dashboard.call_args.kwargs["format"] == "text"


# ===========================================================================
# undo_redo.py — dry-run, transaction, verbose, main entry points
# ===========================================================================


class TestUndoCommandDryRun:
    def test_dry_run_with_operation_id_found(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        mock_manager.can_undo.return_value = (True, "ok")
        op = MagicMock()
        op.id = 42
        op.operation_type.value = "move"
        op.source_path = Path("/src/file.txt")
        op.destination_path = Path("/dst/file.txt")
        mock_manager.get_undo_stack.return_value = [op]

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(operation_id=42, dry_run=True)
        assert result == 0

    def test_dry_run_with_operation_id_not_found_in_stack(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        mock_manager.can_undo.return_value = (True, "ok")
        mock_manager.get_undo_stack.return_value = []  # empty stack → op not found

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(operation_id=42, dry_run=True)
        assert result == 1

    def test_dry_run_with_operation_id_cannot_undo(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        mock_manager.can_undo.return_value = (False, "already undone")

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(operation_id=42, dry_run=True)
        assert result == 1

    def test_dry_run_with_transaction_id(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        mock_transaction = MagicMock()
        mock_manager.history.get_transaction.return_value = mock_transaction
        mock_manager.history.get_operations.return_value = []

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(transaction_id="tx-abc-123", dry_run=True)
        assert result == 0

    def test_dry_run_transaction_not_found(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        mock_manager.history.get_transaction.return_value = None

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(transaction_id="tx-missing", dry_run=True)
        assert result == 1

    def test_dry_run_last_with_ops_prints_info(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        op = MagicMock()
        op.id = 1
        op.operation_type.value = "rename"
        op.source_path = Path("/src/file.txt")
        op.destination_path = None
        mock_manager.get_undo_stack.return_value = [op]

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(dry_run=True)
        assert result == 0

    def test_dry_run_last_empty_stack_returns_1(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        mock_manager.get_undo_stack.return_value = []

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(dry_run=True)
        assert result == 1


class TestUndoCommandWithTransactionId:
    def test_undo_with_transaction_id_success(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        mock_manager.undo_transaction.return_value = True

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(transaction_id="tx-abc")
        assert result == 0
        mock_manager.undo_transaction.assert_called_once_with("tx-abc")

    def test_undo_with_blank_transaction_id_falls_through(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        mock_manager.undo_last_operation.return_value = True

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(transaction_id="   ")
        # blank transaction_id normalized to None → undo_last_operation
        assert result == 0
        mock_manager.undo_last_operation.assert_called_once()


class TestRedoCommandDryRun:
    def test_dry_run_with_operation_id_found(self) -> None:
        from cli.undo_redo import redo_command

        mock_manager = MagicMock()
        mock_manager.can_redo.return_value = (True, "ok")
        op = MagicMock()
        op.id = 42
        op.operation_type.value = "move"
        op.source_path = Path("/src/file.txt")
        op.destination_path = Path("/dst/file.txt")
        mock_manager.get_redo_stack.return_value = [op]

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = redo_command(operation_id=42, dry_run=True)
        assert result == 0

    def test_dry_run_with_operation_id_cannot_redo(self) -> None:
        from cli.undo_redo import redo_command

        mock_manager = MagicMock()
        mock_manager.can_redo.return_value = (False, "not available")

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = redo_command(operation_id=42, dry_run=True)
        assert result == 1

    def test_dry_run_last_with_ops(self) -> None:
        from cli.undo_redo import redo_command

        mock_manager = MagicMock()
        op = MagicMock()
        op.id = 1
        op.operation_type.value = "move"
        op.source_path = Path("/src/file.txt")
        op.destination_path = None
        mock_manager.get_redo_stack.return_value = [op]

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = redo_command(dry_run=True)
        assert result == 0

    def test_dry_run_last_empty_stack(self) -> None:
        from cli.undo_redo import redo_command

        mock_manager = MagicMock()
        mock_manager.get_redo_stack.return_value = []

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = redo_command(dry_run=True)
        assert result == 1

    def test_redo_verbose(self) -> None:
        from cli.undo_redo import redo_command

        mock_manager = MagicMock()
        mock_manager.redo_last_operation.return_value = True

        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = redo_command(verbose=True)
        assert result == 0


class TestHistoryCommandBranches:
    def test_with_transaction(self) -> None:
        from cli.undo_redo import history_command

        mock_viewer = MagicMock()
        with patch("cli.undo_redo.HistoryViewer", return_value=mock_viewer):
            result = history_command(transaction="tx-abc-123")
        assert result == 0
        mock_viewer.show_transaction_details.assert_called_once_with("tx-abc-123")

    def test_with_operation_id(self) -> None:
        from cli.undo_redo import history_command

        mock_viewer = MagicMock()
        with patch("cli.undo_redo.HistoryViewer", return_value=mock_viewer):
            result = history_command(operation_id=42)
        assert result == 0
        mock_viewer.show_operation_details.assert_called_once_with(42)

    def test_with_search_filter(self) -> None:
        from cli.undo_redo import history_command

        mock_viewer = MagicMock()
        with patch("cli.undo_redo.HistoryViewer", return_value=mock_viewer):
            result = history_command(search="*.txt")
        assert result == 0
        mock_viewer.display_filtered_operations.assert_called_once()

    def test_with_status_filter(self) -> None:
        from cli.undo_redo import history_command

        mock_viewer = MagicMock()
        with patch("cli.undo_redo.HistoryViewer", return_value=mock_viewer):
            result = history_command(status="completed")
        assert result == 0
        mock_viewer.display_filtered_operations.assert_called_once()

    def test_with_operation_type_filter(self) -> None:
        from cli.undo_redo import history_command

        mock_viewer = MagicMock()
        with patch("cli.undo_redo.HistoryViewer", return_value=mock_viewer):
            result = history_command(operation_type="move")
        assert result == 0
        mock_viewer.display_filtered_operations.assert_called_once()

    def test_verbose_mode(self) -> None:
        from cli.undo_redo import history_command

        mock_viewer = MagicMock()
        with patch("cli.undo_redo.HistoryViewer", return_value=mock_viewer):
            result = history_command(verbose=True)
        assert result == 0

    def test_since_until_triggers_filtered(self) -> None:
        from cli.undo_redo import history_command

        mock_viewer = MagicMock()
        with patch("cli.undo_redo.HistoryViewer", return_value=mock_viewer):
            result = history_command(since="2026-01-01", until="2026-12-31")
        assert result == 0
        mock_viewer.display_filtered_operations.assert_called_once()


# ===========================================================================
# dedupe_display.py — format_size, format_datetime, display functions
# ===========================================================================


class TestDedupeDisplayFormatSize:
    def test_bytes(self) -> None:
        from cli.dedupe_display import format_size

        assert format_size(0) == "0.0 B"
        assert format_size(999) == "999.0 B"

    def test_kilobytes(self) -> None:
        from cli.dedupe_display import format_size

        assert format_size(1024) == "1.0 KB"
        assert format_size(2048) == "2.0 KB"

    def test_megabytes(self) -> None:
        from cli.dedupe_display import format_size

        assert format_size(1024 * 1024) == "1.0 MB"

    def test_gigabytes(self) -> None:
        from cli.dedupe_display import format_size

        assert format_size(1024**3) == "1.0 GB"

    def test_terabytes(self) -> None:
        from cli.dedupe_display import format_size

        assert format_size(1024**4) == "1.0 TB"

    def test_petabytes(self) -> None:
        from cli.dedupe_display import format_size

        assert format_size(1024**5) == "1.0 PB"


class TestDedupeDisplayFormatDatetime:
    def test_returns_formatted_string(self) -> None:
        from cli.dedupe_display import format_datetime

        result = format_datetime(0.0)
        assert "1970" in result
        assert "-" in result

    def test_non_epoch_timestamp(self) -> None:
        from cli.dedupe_display import format_datetime

        result = format_datetime(1700000000.0)
        assert len(result) == 19  # YYYY-MM-DD HH:MM:SS


class TestDedupeDisplayBanner:
    def test_display_banner(self) -> None:
        from cli.dedupe_display import display_banner

        mock_console = MagicMock()
        display_banner(mock_console)
        assert mock_console.print.call_count >= 4


class TestDedupeDisplayConfig:
    def test_display_config_dry_run(self) -> None:
        from cli.dedupe_display import display_config

        mock_console = MagicMock()
        display_config(
            mock_console,
            directory="/tmp/test",
            algorithm="sha256",
            strategy="oldest",
            recursive=True,
            safe_mode=True,
            dry_run=True,
        )
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("dry run" in c.lower() for c in calls)

    def test_display_config_live_unsafe(self) -> None:
        from cli.dedupe_display import display_config

        mock_console = MagicMock()
        display_config(
            mock_console,
            directory="/tmp/test",
            algorithm="md5",
            strategy="newest",
            recursive=False,
            safe_mode=False,
            dry_run=False,
        )
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("warning" in c.lower() or "safe mode" in c.lower() for c in calls)

    def test_display_config_batch_mode(self) -> None:
        from cli.dedupe_display import display_config

        mock_console = MagicMock()
        display_config(
            mock_console,
            directory="/tmp/test",
            algorithm="sha256",
            strategy="oldest",
            recursive=True,
            safe_mode=True,
            dry_run=False,
            batch=True,
        )
        mock_console.print.assert_called()

    def test_display_config_batch_manual_no_batch_text(self) -> None:
        from cli.dedupe_display import display_config

        mock_console = MagicMock()
        display_config(
            mock_console,
            directory="/tmp/test",
            algorithm="sha256",
            strategy="manual",
            recursive=True,
            safe_mode=True,
            dry_run=False,
            batch=True,
        )
        # batch=True but strategy=manual → "Batch Mode: Enabled" NOT appended
        config_text = mock_console.print.call_args_list[0][0][0].renderable
        assert "Batch Mode" not in config_text


class TestDedupeDisplayDuplicateGroup:
    def test_display_group_with_keep_flag(self) -> None:
        from cli.dedupe_display import display_duplicate_group

        mock_console = MagicMock()
        files = [
            {"path": "/a/file1.txt", "size": 1024, "mtime": 1000000.0, "keep": True},
            {"path": "/b/file2.txt", "size": 1024, "mtime": 2000000.0, "keep": False},
        ]
        display_duplicate_group(mock_console, 1, "abc123def456abcd", files, 3)
        mock_console.print.assert_called()

    def test_display_group_without_keep_flag(self) -> None:
        from cli.dedupe_display import display_duplicate_group

        mock_console = MagicMock()
        files = [
            {"path": "/a/file1.txt", "size": 512, "mtime": 1000000.0},
            {"path": "/b/file2.txt", "size": 512, "mtime": 2000000.0},
        ]
        display_duplicate_group(mock_console, 2, "deadbeef12345678", files, 5)
        mock_console.print.assert_called()


class TestDedupeDisplayBackupInfo:
    def test_display_backup_info(self) -> None:
        from cli.dedupe_display import display_backup_info

        mock_console = MagicMock()
        display_backup_info(mock_console)
        assert mock_console.print.call_count == 2


class TestDedupeDisplaySummary:
    """Pin both branches of `display_summary` — dry-run and live. D#167
    removed the legacy `fo dedupe` CLI that previously exercised these
    paths; without this class the `dry_run=True/False` arms fall out of
    integration coverage.
    """

    def test_summary_dry_run_branch_renders_dry_run_panel(self) -> None:
        from cli.dedupe_display import display_summary

        mock_console = MagicMock()
        display_summary(
            mock_console,
            total_groups=3,
            total_duplicates=5,
            total_removed=2,
            space_saved=1024 * 1024,
            dry_run=True,
        )
        printed = [str(call.args) for call in mock_console.print.call_args_list]
        assert any("DRY RUN SUMMARY" in text for text in printed)
        assert not any("DEDUPLICATION COMPLETE" in text for text in printed)

    def test_summary_live_branch_renders_complete_panel(self) -> None:
        from cli.dedupe_display import display_summary

        mock_console = MagicMock()
        display_summary(
            mock_console,
            total_groups=1,
            total_duplicates=2,
            total_removed=1,
            space_saved=2048,
            dry_run=False,
        )
        printed = [str(call.args) for call in mock_console.print.call_args_list]
        assert any("DEDUPLICATION COMPLETE" in text for text in printed)
        assert not any("DRY RUN SUMMARY" in text for text in printed)


# ===========================================================================
# utilities.py — analyze verbose, _build_json_record, _format_file_size
# ===========================================================================


class TestUtilitiesPrivateHelpers:
    def test_format_file_size_bytes(self) -> None:
        from cli.utilities import _format_file_size

        assert _format_file_size(500) == "500 B"
        assert _format_file_size(0) == "0 B"
        assert _format_file_size(1023) == "1023 B"

    def test_format_file_size_kb(self) -> None:
        from cli.utilities import _format_file_size

        assert _format_file_size(2048) == "2.0 KB"
        assert _format_file_size(1024) == "1.0 KB"

    def test_format_file_size_mb(self) -> None:
        from cli.utilities import _format_file_size

        assert _format_file_size(1024 * 1024 * 3) == "3.0 MB"

    def test_build_json_record_valid_file(self, tmp_path: Path) -> None:
        from cli.utilities import _build_json_record

        f = tmp_path / "doc.txt"
        f.write_text("hello")
        record = _build_json_record(f)
        assert record is not None
        assert record["path"] == str(f)
        assert isinstance(record["size"], int)
        assert "modified" in record

    def test_build_json_record_with_score(self, tmp_path: Path) -> None:
        from cli.utilities import _build_json_record

        f = tmp_path / "doc.txt"
        f.write_text("hello")
        record = _build_json_record(f, score=0.9876)
        assert record is not None
        assert "score" in record
        assert record["score"] == pytest.approx(0.9876, abs=1e-4)

    def test_build_json_record_missing_file_returns_none(self, tmp_path: Path) -> None:
        import warnings

        from cli.utilities import _build_json_record

        nonexistent = tmp_path / "gone.txt"
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            record = _build_json_record(nonexistent)
        assert record is None

    def test_normalized_extension_compound(self, tmp_path: Path) -> None:
        from cli.utilities import _normalized_extension

        assert _normalized_extension(Path("archive.tar.gz")) == ".tar.gz"
        assert _normalized_extension(Path("backup.tar.bz2")) == ".tar.bz2"

    def test_normalized_extension_simple(self) -> None:
        from cli.utilities import _normalized_extension

        assert _normalized_extension(Path("file.txt")) == ".txt"
        assert _normalized_extension(Path("file.PDF")) == ".pdf"

    def test_normalized_extension_no_ext(self) -> None:
        from cli.utilities import _normalized_extension

        assert _normalized_extension(Path("Makefile")) == ""

    def test_normalized_extension_unknown_compound(self) -> None:
        """Two suffixes but not .tar.gz/.tar.bz2 → falls back to last suffix."""
        from cli.utilities import _normalized_extension

        assert _normalized_extension(Path("file.backup.zip")) == ".zip"
        assert _normalized_extension(Path("data.v2.json")) == ".json"


class TestAnalyzeCommandVerbose:
    def test_analyze_verbose_output(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        text_file = tmp_path / "report.txt"
        text_file.write_text("Annual report content for analysis.")

        mock_config = MagicMock()
        mock_config.name = "benchmark-model"
        mock_model_instance = MagicMock()
        mock_model_instance.config = mock_config

        mock_text_model_cls = MagicMock()
        mock_text_model_cls.get_default_config.return_value = mock_config
        mock_text_model_cls.return_value = mock_model_instance

        with (
            patch("models.text_model.TextModel", mock_text_model_cls),
            patch(
                "services.analyzer.generate_category",
                return_value="Finance",
            ),
            patch(
                "services.analyzer.generate_description",
                return_value="A financial report.",
            ),
            patch(
                "services.analyzer.calculate_confidence",
                return_value=0.90,
            ),
        ):
            result = cli_runner.invoke(app, ["analyze", str(text_file), "--verbose"])
        assert result.exit_code == 0


class TestOutputSearchResults:
    def test_no_results_json_output(self, tmp_path: Path) -> None:
        import io as _io

        import typer

        from cli.utilities import _output_search_results

        _io.StringIO()
        with patch("typer.echo", wraps=typer.echo) as mock_echo:
            _output_search_results([], json_out=True)
        mock_echo.assert_called_once_with("[]")

    def test_no_results_text_output(self, tmp_path: Path) -> None:
        from cli.utilities import _output_search_results

        # No exception should be raised; text output goes to console
        _output_search_results([], json_out=False)

    def test_results_json_output(self, tmp_path: Path) -> None:

        from cli.utilities import _output_search_results

        f = tmp_path / "file.txt"
        f.write_text("content")

        captured = []
        with patch("typer.echo", side_effect=captured.append):
            _output_search_results([(f, None)], json_out=True)
        assert len(captured) == 1
        records = json.loads(captured[0])
        assert isinstance(records, list)
        assert len(records) == 1

    def test_results_json_output_with_score(self, tmp_path: Path) -> None:
        from cli.utilities import _output_search_results

        f = tmp_path / "file.txt"
        f.write_text("content")

        captured = []
        with patch("typer.echo", side_effect=captured.append):
            _output_search_results([(f, 0.85)], json_out=True)
        records = json.loads(captured[0])
        assert records[0]["score"] == pytest.approx(0.85, abs=1e-4)

    def test_results_text_output_with_score(self, tmp_path: Path) -> None:
        from cli.utilities import _output_search_results

        f = tmp_path / "file.txt"
        f.write_text("content")

        captured = []
        with patch("typer.echo", side_effect=captured.append):
            _output_search_results([(f, 0.75)], json_out=False, search_type="semantic")
        assert any("semantic" in str(c) for c in captured)

    def test_results_text_output_no_score(self, tmp_path: Path) -> None:
        from cli.utilities import _output_search_results

        f = tmp_path / "file.txt"
        f.write_text("content")

        captured = []
        with patch("typer.echo", side_effect=captured.append):
            _output_search_results([(f, None)], json_out=False)
        combined = " ".join(str(c) for c in captured)
        assert "file.txt" in combined


class TestValidateSearchParams:
    def test_invalid_type_raises_exit(self, tmp_path: Path) -> None:
        import typer

        from cli.utilities import _validate_search_params

        with pytest.raises(typer.Exit):
            _validate_search_params(10, tmp_path, "database")

    def test_nonexistent_directory_raises_exit(self, tmp_path: Path) -> None:
        import typer

        from cli.utilities import _validate_search_params

        with pytest.raises(typer.Exit):
            _validate_search_params(10, tmp_path / "gone", None)

    def test_limit_zero_returns_should_exit_true(self, tmp_path: Path) -> None:
        from cli.utilities import _validate_search_params

        _resolved, should_exit = _validate_search_params(0, tmp_path, None)
        assert should_exit is True

    def test_valid_params_returns_resolved_dir(self, tmp_path: Path) -> None:
        from cli.utilities import _validate_search_params

        resolved, should_exit = _validate_search_params(10, tmp_path, None)
        assert resolved == tmp_path.resolve()
        assert should_exit is False


class TestDoDefaultSearch:
    def test_glob_pattern_matches(self, tmp_path: Path) -> None:
        from cli.utilities import _do_default_search

        (tmp_path / "report.txt").write_text("x")
        (tmp_path / "data.csv").write_text("y")
        results = _do_default_search("*.txt", tmp_path, None, 50, True)
        names = [r.name for r in results]
        assert "report.txt" in names
        assert "data.csv" not in names

    def test_keyword_search(self, tmp_path: Path) -> None:
        from cli.utilities import _do_default_search

        (tmp_path / "report.txt").write_text("x")
        (tmp_path / "notes.md").write_text("y")
        results = _do_default_search("report", tmp_path, None, 50, True)
        names = [r.name for r in results]
        assert "report.txt" in names
        assert "notes.md" not in names

    def test_type_filter(self, tmp_path: Path) -> None:
        from cli.utilities import _do_default_search

        (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff")
        (tmp_path / "doc.txt").write_text("text")
        results = _do_default_search("*", tmp_path, "image", 50, True)
        names = [r.name for r in results]
        assert "photo.jpg" in names
        assert "doc.txt" not in names

    def test_limit_respected(self, tmp_path: Path) -> None:
        from cli.utilities import _do_default_search

        for i in range(10):
            (tmp_path / f"file{i}.txt").write_text("x")
        results = _do_default_search("*.txt", tmp_path, None, 3, True)
        assert len(results) == 3

    def test_non_recursive(self, tmp_path: Path) -> None:
        from cli.utilities import _do_default_search

        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("x")
        (tmp_path / "top.txt").write_text("y")
        results = _do_default_search("*.txt", tmp_path, None, 50, False)
        names = [r.name for r in results]
        assert "top.txt" in names
        assert "nested.txt" not in names


# ===========================================================================
# undo_redo.py — verbose flag, exception paths, main_* entry points
# ===========================================================================


class TestUndoCommandVerboseAndExceptions:
    def test_undo_verbose_flag_succeeds(self) -> None:
        from cli.undo_redo import undo_command

        mock_manager = MagicMock()
        mock_manager.undo_last_operation.return_value = True
        with patch("cli.undo_redo.UndoManager", return_value=mock_manager):
            result = undo_command(verbose=True)
        assert result == 0

    def test_undo_exception_returns_1(self) -> None:
        from cli.undo_redo import undo_command

        with patch(
            "cli.undo_redo.UndoManager",
            side_effect=RuntimeError("db unavailable"),
        ):
            result = undo_command()
        assert result == 1

    def test_redo_exception_returns_1(self) -> None:
        from cli.undo_redo import redo_command

        with patch(
            "cli.undo_redo.UndoManager",
            side_effect=RuntimeError("db unavailable"),
        ):
            result = redo_command()
        assert result == 1


class TestHistoryCommandStats:
    def test_stats_flag(self) -> None:
        from cli.undo_redo import history_command

        mock_viewer = MagicMock()
        with patch("cli.undo_redo.HistoryViewer", return_value=mock_viewer):
            result = history_command(stats=True)
        assert result == 0
        mock_viewer.show_statistics.assert_called_once()

    def test_exception_returns_1(self) -> None:
        from cli.undo_redo import history_command

        with patch(
            "cli.undo_redo.HistoryViewer",
            side_effect=RuntimeError("viewer broken"),
        ):
            result = history_command()
        assert result == 1


class TestMainEntryPoints:
    def test_main_undo_calls_undo_command(self) -> None:
        from cli.undo_redo import main_undo

        with (
            patch("sys.argv", ["fo-undo", "--dry-run"]),
            patch("cli.undo_redo.undo_command", return_value=0) as mock_cmd,
            patch("sys.exit"),
        ):
            main_undo()
        mock_cmd.assert_called_once()

    def test_main_redo_calls_redo_command(self) -> None:
        from cli.undo_redo import main_redo

        with (
            patch("sys.argv", ["fo-redo"]),
            patch("cli.undo_redo.redo_command", return_value=0) as mock_cmd,
            patch("sys.exit"),
        ):
            main_redo()
        mock_cmd.assert_called_once()

    def test_main_history_calls_history_command(self) -> None:
        from cli.undo_redo import main_history

        with (
            patch("sys.argv", ["fo-history", "--limit", "5"]),
            patch("cli.undo_redo.history_command", return_value=0) as mock_cmd,
            patch("sys.exit"),
        ):
            main_history()
        mock_cmd.assert_called_once()


# ===========================================================================
# utilities.py — analyze command error paths, search with limit=0
# ===========================================================================


class TestSearchCommandLimitZero:
    def test_search_with_zero_limit_exits_0(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        (tmp_path / "file.txt").write_text("x")
        result = cli_runner.invoke(app, ["search", "file", str(tmp_path), "--limit", "0"])
        assert result.exit_code == 0


class TestAnalyzeCommandErrorPaths:
    def test_analyze_file_not_found(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        result = cli_runner.invoke(app, ["analyze", str(tmp_path / "missing.txt")])
        assert result.exit_code == 1

    def test_analyze_binary_file_exits_1(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        binary = tmp_path / "data.bin"
        binary.write_bytes(b"\x00\x01\x02\x03" * 100)
        result = cli_runner.invoke(app, ["analyze", str(binary)])
        assert result.exit_code == 1
        assert "binary" in result.output.lower()

    def test_analyze_json_output(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        text_file = tmp_path / "report.txt"
        text_file.write_text("Quarterly financial report summary.")

        with (
            patch("models.text_model.TextModel.initialize"),
            patch(
                "services.analyzer.generate_category",
                return_value="Finance",
            ),
            patch(
                "services.analyzer.generate_description",
                return_value="A financial report.",
            ),
            patch(
                "services.analyzer.calculate_confidence",
                return_value=0.85,
            ),
        ):
            result = cli_runner.invoke(app, ["analyze", str(text_file), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output.strip())
        assert data["category"] == "Finance"
        assert data["description"] == "A financial report."
        assert "confidence" in data

    def test_analyze_runtime_error_exits_1(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        text_file = tmp_path / "report.txt"
        text_file.write_text("Some content for analysis.")

        with (
            patch("models.text_model.TextModel.initialize"),
            patch(
                "services.analyzer.generate_category",
                side_effect=RuntimeError("AI model failed"),
            ),
        ):
            result = cli_runner.invoke(app, ["analyze", str(text_file)])
        assert result.exit_code == 1

    def test_analyze_import_error_exits_1(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        text_file = tmp_path / "report.txt"
        text_file.write_text("Some content for analysis.")

        with patch(
            "models.text_model.TextModel",
            side_effect=ImportError("Ollama not available"),
        ):
            result = cli_runner.invoke(app, ["analyze", str(text_file)])
        assert result.exit_code == 1


# ===========================================================================
# benchmark.py — validate_benchmark_payload, _require_payload_fields,
#                _validate_payload_degradation_reasons, compare_results
# ===========================================================================


class TestValidateBenchmarkPayload:
    def test_valid_payload_passes(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 10,
            "hardware_profile": {},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        # Should not raise
        validate_benchmark_payload(payload)

    def test_missing_top_level_field_raises_key_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            # missing effective_suite, degraded, degradation_reasons, etc.
        }
        with pytest.raises(KeyError, match="Missing benchmark payload fields"):
            validate_benchmark_payload(payload)

    def test_non_string_suite_raises_type_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": 42,  # must be str
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(TypeError):
            validate_benchmark_payload(payload)

    def test_empty_suite_name_raises_value_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "",  # must be non-empty
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(ValueError, match="non-empty"):
            validate_benchmark_payload(payload)

    def test_degraded_true_with_empty_reasons_raises(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "text",
            "effective_suite": "text",
            "degraded": True,
            "degradation_reasons": [],  # inconsistent
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 0,
            "hardware_profile": {},
            "results": {
                "median_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "stddev_ms": 0.0,
                "throughput_fps": 0.0,
                "iterations": 0,
            },
        }
        with pytest.raises(ValueError, match="inconsistent"):
            validate_benchmark_payload(payload)

    def test_degraded_false_with_nonempty_reasons_raises(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": ["some-reason"],  # inconsistent
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(ValueError, match="inconsistent"):
            validate_benchmark_payload(payload)

    def test_negative_files_count_raises(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": -1,  # invalid
            "hardware_profile": {},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(ValueError, match="non-negative"):
            validate_benchmark_payload(payload)

    def test_non_dict_hardware_profile_raises(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": "not a dict",  # invalid
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(TypeError):
            validate_benchmark_payload(payload)

    def test_missing_results_field_raises(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {"median_ms": 1.0},  # missing other fields
        }
        with pytest.raises(KeyError):
            validate_benchmark_payload(payload)

    def test_negative_result_field_raises(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": -1.0,  # invalid
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(ValueError, match="non-negative"):
            validate_benchmark_payload(payload)


class TestCompareResults:
    def test_regression_detected(self) -> None:
        from cli.benchmark import compare_results

        current = {
            "results": {
                "median_ms": 50.0,
                "p95_ms": 120.0,
                "p99_ms": 150.0,
                "stddev_ms": 5.0,
                "throughput_fps": 20.0,
            }
        }
        baseline = {
            "results": {
                "median_ms": 10.0,
                "p95_ms": 50.0,
                "p99_ms": 60.0,
                "stddev_ms": 2.0,
                "throughput_fps": 100.0,
            }
        }
        result = compare_results(current, baseline, threshold=1.2)
        assert result["regression"] is True
        assert result["deltas_pct"]["median_ms"] == pytest.approx(400.0, abs=1.0)

    def test_no_regression(self) -> None:
        from cli.benchmark import compare_results

        current = {
            "results": {
                "median_ms": 10.0,
                "p95_ms": 50.0,
                "p99_ms": 60.0,
                "stddev_ms": 2.0,
                "throughput_fps": 100.0,
            }
        }
        baseline = {
            "results": {
                "median_ms": 10.0,
                "p95_ms": 50.0,
                "p99_ms": 60.0,
                "stddev_ms": 2.0,
                "throughput_fps": 100.0,
            }
        }
        result = compare_results(current, baseline, threshold=1.2)
        assert result["regression"] is False

    def test_zero_baseline_value_returns_zero_delta(self) -> None:
        from cli.benchmark import compare_results

        current = {
            "results": {
                "median_ms": 10.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "stddev_ms": 0.0,
                "throughput_fps": 0.0,
            }
        }
        baseline = {
            "results": {
                "median_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "stddev_ms": 0.0,
                "throughput_fps": 0.0,
            }
        }
        result = compare_results(current, baseline)
        assert result["deltas_pct"]["p95_ms"] == 0.0


class TestComputeStats:
    def test_empty_times_returns_zero_stats(self) -> None:
        from cli.benchmark import compute_stats

        stats = compute_stats([], file_count=0)
        assert stats["iterations"] == 0
        assert stats["median_ms"] == 0.0

    def test_single_time_returns_same_median(self) -> None:
        from cli.benchmark import compute_stats

        stats = compute_stats([42.0], file_count=1)
        assert stats["iterations"] == 1
        assert stats["median_ms"] == pytest.approx(42.0)
        assert stats["stddev_ms"] == 0.0

    def test_multiple_times_calculates_stats(self) -> None:
        from cli.benchmark import compute_stats

        times = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = compute_stats(times, file_count=5)
        assert stats["iterations"] == 5
        assert stats["median_ms"] == pytest.approx(30.0)
        assert stats["throughput_fps"] > 0


class TestBenchmarkSuiteVisionAndPipeline:
    def test_vision_suite_with_image_file(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_vision_suite

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        outcome = _run_vision_suite([img])
        assert outcome.processed_count == 1

    def test_pipeline_suite_with_text_files(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_pipeline_suite

        (tmp_path / "doc.txt").write_text("pipeline test content")
        outcome = _run_pipeline_suite([tmp_path / "doc.txt"])
        assert outcome.processed_count == 1

    def test_run_with_pipeline_suite(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("pipeline content")
        result = cli_runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(src),
                "--suite",
                "pipeline",
                "--iterations",
                "1",
                "--warmup",
                "0",
            ],
        )
        assert result.exit_code == 0

    def test_classify_e2e_suite_files_but_zero_processed(self, tmp_path: Path) -> None:
        from cli.benchmark import (
            _classify_e2e_suite,
            _SuiteIterationOutcome,
        )

        files = [tmp_path / "a.txt"]
        outcome = _SuiteIterationOutcome(processed_count=0)
        cls = _classify_e2e_suite(files, outcome)
        assert cls.degraded is True
        assert cls.effective_suite == "e2e"

    def test_classify_audio_suite_with_synthetic_metadata(self, tmp_path: Path) -> None:
        from cli.benchmark import (
            _classify_audio_suite,
            _SuiteIterationOutcome,
        )

        audio_file = tmp_path / "track.mp3"
        audio_file.write_bytes(b"\xff\xfb" + b"\x00" * 50)
        outcome = _SuiteIterationOutcome(processed_count=1, used_synthetic_audio_metadata=True)
        cls = _classify_audio_suite([audio_file], outcome)
        assert cls.degraded is True

    def test_resolve_processed_count_inconsistent_raises(self) -> None:
        import typer

        from cli.benchmark import _resolve_processed_count

        mock_console = MagicMock()
        with pytest.raises(typer.Exit):
            _resolve_processed_count([1, 2, 3], warmup=0, suite="io", console=mock_console)

    def test_check_baseline_profile_compatibility_mismatch_returns_warning(self) -> None:
        from cli.benchmark import _check_baseline_profile_compatibility

        mock_console = MagicMock()
        baseline = {"runner_profile_version": "old-profile-v0"}
        warning = _check_baseline_profile_compatibility(
            baseline, suite="io", console=mock_console, json_output=False
        )
        assert warning is not None
        assert "mismatch" in warning.lower()
        mock_console.print.assert_called_once()

    def test_check_baseline_profile_compatibility_json_no_print(self) -> None:
        from cli.benchmark import _check_baseline_profile_compatibility

        mock_console = MagicMock()
        baseline = {"runner_profile_version": "old-profile-v0"}
        warning = _check_baseline_profile_compatibility(
            baseline, suite="io", console=mock_console, json_output=True
        )
        assert warning is not None
        mock_console.print.assert_not_called()

    def test_check_baseline_profile_compatibility_same_version_returns_none(self) -> None:
        from cli.benchmark import (
            _RUNNER_PROFILE_VERSION,
            _check_baseline_profile_compatibility,
        )

        mock_console = MagicMock()
        baseline = {"runner_profile_version": _RUNNER_PROFILE_VERSION}
        warning = _check_baseline_profile_compatibility(
            baseline, suite="io", console=mock_console, json_output=False
        )
        assert warning is None


# ===========================================================================
# utilities.py — remaining uncovered branches:
#   OSError in _output_search_results, OSError in analyze read_bytes/read_text,
#   verbose path in analyze
# ===========================================================================


class TestOutputSearchResultsOSError:
    def test_oserror_on_stat_is_skipped(self, tmp_path: Path) -> None:
        from cli.utilities import _output_search_results

        f = tmp_path / "file.txt"
        f.write_text("content")
        captured = []
        # Patch Path.stat at module level to raise OSError for our file
        original_stat = Path.stat

        def _patched_stat(self, *args, **kwargs):
            if self == f:
                raise OSError("permission denied")
            return original_stat(self, *args, **kwargs)

        with (
            patch("pathlib.Path.stat", _patched_stat),
            patch("typer.echo", side_effect=captured.append),
        ):
            _output_search_results([(f, None)], json_out=False)
        # File was skipped due to OSError, 0 files rendered
        combined = " ".join(str(c) for c in captured)
        assert "Found 0 file(s)" in combined


class TestAnalyzeReadBytesOSError:
    def test_read_bytes_oserror_exits_1(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        text_file = tmp_path / "report.txt"
        text_file.write_text("Content here.")

        with patch("pathlib.Path.read_bytes", side_effect=OSError("permission denied")):
            result = cli_runner.invoke(app, ["analyze", str(text_file)])
        assert result.exit_code == 1
        assert "could not read" in result.output.lower()


class TestAnalyzeReadTextOSError:
    def test_read_text_oserror_exits_1(self, cli_runner, tmp_path: Path) -> None:
        from cli.main import app

        text_file = tmp_path / "report.txt"
        text_file.write_text("Content here.")

        def _fake_read_bytes(self: Path, *args, **kwargs):  # type: ignore[override]
            # First call (read_bytes for binary check) returns clean bytes
            return b"clean text content"

        def _fake_read_text(self: Path, *args, **kwargs) -> str:  # type: ignore[override]
            raise OSError("permission denied on text read")

        with (
            patch("pathlib.Path.read_bytes", _fake_read_bytes),
            patch("pathlib.Path.read_text", _fake_read_text),
        ):
            result = cli_runner.invoke(app, ["analyze", str(text_file)])
        assert result.exit_code == 1
        assert "could not read" in result.output.lower()


# ===========================================================================
# benchmark.py — remaining: _run_e2e_suite, _run_audio_suite with audio files,
#                validate bool fields, _percentile edge cases
# ===========================================================================


class TestBenchmarkAdditionalValidation:
    def test_bool_files_count_raises_type_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": True,  # bool is not int for this check
            "hardware_profile": {},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(TypeError, match="files_count"):
            validate_benchmark_payload(payload)

    def test_bool_iterations_raises_type_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": True,  # bool is not int
            },
        }
        with pytest.raises(TypeError, match="iterations"):
            validate_benchmark_payload(payload)

    def test_non_bool_degraded_raises_type_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": "no",  # must be bool
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(TypeError, match="degraded"):
            validate_benchmark_payload(payload)

    def test_non_list_degradation_reasons_raises(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": "not-a-list",  # must be list
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(TypeError, match="degradation_reasons"):
            validate_benchmark_payload(payload)

    def test_empty_degradation_reason_string_raises(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": True,
            "degradation_reasons": [""],  # empty string is invalid
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 0,
            "hardware_profile": {},
            "results": {
                "median_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "stddev_ms": 0.0,
                "throughput_fps": 0.0,
                "iterations": 0,
            },
        }
        with pytest.raises(ValueError, match="non-empty string"):
            validate_benchmark_payload(payload)

    def test_non_dict_results_raises_type_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": "not a dict",  # invalid
        }
        with pytest.raises(TypeError, match="results"):
            validate_benchmark_payload(payload)

    def test_percentile_empty_list_returns_zero(self) -> None:
        from cli.benchmark import _percentile

        assert _percentile([], 95) == 0.0

    def test_percentile_single_item(self) -> None:
        from cli.benchmark import _percentile

        assert _percentile([42.0], 95) == 42.0

    def test_bool_result_field_raises_type_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-03-14-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": True,  # bool is rejected by _require_non_negative_numeric_field
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        with pytest.raises(TypeError):
            validate_benchmark_payload(payload)
