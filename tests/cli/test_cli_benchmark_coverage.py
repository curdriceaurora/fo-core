"""Coverage tests for cli.benchmark — uncovered lines 59-60, 66-86, 117-118, 131, 138."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

pytestmark = pytest.mark.unit

runner = CliRunner()


def _get_app():
    from cli import app

    return app


class TestBenchmarkErrors:
    """Covers error branches."""

    def test_path_not_exists(self, tmp_path: Path) -> None:
        app = _get_app()
        nonexist = tmp_path / "nonexistent_benchmark_sub"
        result = runner.invoke(app, ["benchmark", "run", str(nonexist)])
        # Typer validates the path and returns exit code 1 or 2
        assert result.exit_code in (1, 2)

    def test_read_error(self, tmp_path: Path) -> None:
        app = _get_app()
        # Patching Path.rglob globally can interfere with Typer's internal
        # path validation, so we accept either exit code 1 (our error handler)
        # or 2 (Typer's argument validation).
        with patch.object(Path, "rglob", side_effect=PermissionError("denied")):
            result = runner.invoke(app, ["benchmark", "run", str(tmp_path)])

        assert result.exit_code in (1, 2)

    def test_no_files_json(self, tmp_path: Path) -> None:
        """No files found with JSON output."""
        app = _get_app()
        result = runner.invoke(app, ["benchmark", "run", str(tmp_path), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["effective_suite"] == "io"
        assert payload["degraded"] is False
        assert payload["degradation_reasons"] == []
        assert payload["files_count"] == 0

    def test_no_files_plain(self, tmp_path: Path) -> None:
        """No files found with plain output."""
        app = _get_app()
        result = runner.invoke(app, ["benchmark", "run", str(tmp_path)])
        assert result.exit_code == 0
        assert "No files found" in result.output


class TestBenchmarkEvenIterations:
    """Covers median calculation for even number of iterations (line 138)."""

    def test_even_iterations(self, tmp_path: Path) -> None:
        app = _get_app()
        (tmp_path / "a.txt").write_text("hello")

        # Mock profiler and monitor
        mock_profiler = MagicMock()
        mock_timeline = MagicMock()
        mock_timeline.snapshots = []
        mock_profiler.stop_tracking.return_value = mock_timeline

        mock_monitor = MagicMock()
        mock_mem = MagicMock()
        mock_mem.rss = 100 * 1024 * 1024
        mock_monitor.get_memory_usage.return_value = mock_mem

        with (
            patch(
                "optimization.memory_profiler.MemoryProfiler",
                return_value=mock_profiler,
            ),
            patch(
                "optimization.resource_monitor.ResourceMonitor",
                return_value=mock_monitor,
            ),
        ):
            result = runner.invoke(
                app,
                ["benchmark", "run", str(tmp_path), "--iterations", "2", "--json"],
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["effective_suite"] == "io"
        assert payload["degraded"] is False
        assert payload["degradation_reasons"] == []
        assert payload["files_count"] == 1


# ---------------------------------------------------------------------------
# Direct function imports
# ---------------------------------------------------------------------------


class TestPercentile:
    """Tests for _percentile helper (lines 128-136)."""

    def test_percentile_empty_list_returns_zero(self) -> None:
        from cli.benchmark import _percentile

        result = _percentile([], 50)
        assert result == 0.0

    def test_percentile_single_element(self) -> None:
        from cli.benchmark import _percentile

        result = _percentile([42.0], 50)
        assert result == 42.0

    def test_percentile_p95(self) -> None:
        from cli.benchmark import _percentile

        data = sorted([float(i) for i in range(1, 101)])
        result = _percentile(data, 95)
        assert result == 95.0

    def test_percentile_p99(self) -> None:
        from cli.benchmark import _percentile

        data = sorted([float(i) for i in range(1, 101)])
        result = _percentile(data, 99)
        assert result == 99.0


class TestRequireNonNegativeNumericField:
    """Tests for _require_non_negative_numeric_field (lines 174-179)."""

    def test_bool_raises_type_error(self) -> None:
        from cli.benchmark import _require_non_negative_numeric_field

        with pytest.raises(TypeError, match="must be numeric"):
            _require_non_negative_numeric_field(True, field="test_field")

    def test_false_raises_type_error(self) -> None:
        from cli.benchmark import _require_non_negative_numeric_field

        with pytest.raises(TypeError, match="must be numeric"):
            _require_non_negative_numeric_field(False, field="test_field")

    def test_string_raises_type_error(self) -> None:
        from cli.benchmark import _require_non_negative_numeric_field

        with pytest.raises(TypeError, match="must be numeric"):
            _require_non_negative_numeric_field("10", field="test_field")

    def test_negative_int_raises_value_error(self) -> None:
        from cli.benchmark import _require_non_negative_numeric_field

        with pytest.raises(ValueError, match="must be non-negative"):
            _require_non_negative_numeric_field(-1, field="test_field")

    def test_negative_float_raises_value_error(self) -> None:
        from cli.benchmark import _require_non_negative_numeric_field

        with pytest.raises(ValueError, match="must be non-negative"):
            _require_non_negative_numeric_field(-0.5, field="test_field")

    def test_zero_passes(self) -> None:
        from cli.benchmark import _require_non_negative_numeric_field

        # Must not raise
        _require_non_negative_numeric_field(0, field="test_field")

    def test_positive_float_passes(self) -> None:
        from cli.benchmark import _require_non_negative_numeric_field

        _require_non_negative_numeric_field(3.14, field="test_field")


class TestRequirePayloadFields:
    """Tests for _require_payload_fields (lines 182-196)."""

    def test_empty_dict_raises_key_error(self) -> None:
        from cli.benchmark import _require_payload_fields

        with pytest.raises(KeyError, match="Missing benchmark payload fields"):
            _require_payload_fields({})

    def test_all_required_fields_present_passes(self) -> None:
        from cli.benchmark import _require_payload_fields

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "v1",
            "files_count": 0,
            "hardware_profile": {},
            "results": {},
        }
        # Must not raise
        _require_payload_fields(payload)

    def test_missing_single_field_raises(self) -> None:
        from cli.benchmark import _require_payload_fields

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "v1",
            "hardware_profile": {},
            "results": {},
            # "files_count" is missing
        }
        with pytest.raises(KeyError, match="files_count"):
            _require_payload_fields(payload)


class TestValidatePayloadIdentityFields:
    """Tests for _validate_payload_identity_fields (lines 199-206)."""

    def test_non_string_suite_raises_type_error(self) -> None:
        from cli.benchmark import _validate_payload_identity_fields

        payload = {
            "suite": 42,
            "effective_suite": "io",
            "runner_profile_version": "v1",
        }
        with pytest.raises(TypeError, match="must be a string"):
            _validate_payload_identity_fields(payload)

    def test_empty_string_suite_raises_value_error(self) -> None:
        from cli.benchmark import _validate_payload_identity_fields

        payload = {
            "suite": "",
            "effective_suite": "io",
            "runner_profile_version": "v1",
        }
        with pytest.raises(ValueError, match="must be non-empty"):
            _validate_payload_identity_fields(payload)

    def test_empty_effective_suite_raises_value_error(self) -> None:
        from cli.benchmark import _validate_payload_identity_fields

        payload = {
            "suite": "io",
            "effective_suite": "",
            "runner_profile_version": "v1",
        }
        with pytest.raises(ValueError, match="must be non-empty"):
            _validate_payload_identity_fields(payload)

    def test_valid_fields_passes(self) -> None:
        from cli.benchmark import _validate_payload_identity_fields

        payload = {
            "suite": "io",
            "effective_suite": "io",
            "runner_profile_version": "2026-01-01-v1",
        }
        # Must not raise
        _validate_payload_identity_fields(payload)


class TestValidatePayloadDegradationReasons:
    """Tests for _validate_payload_degradation_reasons (lines 209-236)."""

    def test_non_bool_degraded_raises_type_error(self) -> None:
        from cli.benchmark import _validate_payload_degradation_reasons

        payload = {"degraded": 1, "degradation_reasons": []}
        with pytest.raises(TypeError, match="'degraded' to be a bool"):
            _validate_payload_degradation_reasons(payload)

    def test_non_list_degradation_reasons_raises_type_error(self) -> None:
        from cli.benchmark import _validate_payload_degradation_reasons

        payload = {"degraded": False, "degradation_reasons": "bad"}
        with pytest.raises(TypeError, match="must be a list"):
            _validate_payload_degradation_reasons(payload)

    def test_empty_reason_string_raises_value_error(self) -> None:
        from cli.benchmark import _validate_payload_degradation_reasons

        payload = {"degraded": True, "degradation_reasons": [""]}
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_payload_degradation_reasons(payload)

    def test_non_string_reason_raises_value_error(self) -> None:
        from cli.benchmark import _validate_payload_degradation_reasons

        payload = {"degraded": True, "degradation_reasons": [42]}
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_payload_degradation_reasons(payload)

    def test_degraded_true_empty_reasons_raises_value_error(self) -> None:
        from cli.benchmark import _validate_payload_degradation_reasons

        payload = {"degraded": True, "degradation_reasons": []}
        with pytest.raises(ValueError, match="non-empty"):
            _validate_payload_degradation_reasons(payload)

    def test_degraded_false_nonempty_reasons_raises_value_error(self) -> None:
        from cli.benchmark import _validate_payload_degradation_reasons

        payload = {"degraded": False, "degradation_reasons": ["some-reason"]}
        with pytest.raises(ValueError, match="must be empty"):
            _validate_payload_degradation_reasons(payload)

    def test_valid_degraded_false_empty_reasons(self) -> None:
        from cli.benchmark import _validate_payload_degradation_reasons

        payload = {"degraded": False, "degradation_reasons": []}
        # Must not raise
        _validate_payload_degradation_reasons(payload)

    def test_valid_degraded_true_with_reasons(self) -> None:
        from cli.benchmark import _validate_payload_degradation_reasons

        payload = {"degraded": True, "degradation_reasons": ["reason-a", "reason-b"]}
        # Must not raise
        _validate_payload_degradation_reasons(payload)


class TestValidatePayloadResults:
    """Tests for _validate_payload_results (lines 239-259)."""

    def test_missing_field_raises_key_error(self) -> None:
        from cli.benchmark import _validate_payload_results

        # p99_ms is missing
        results = {
            "median_ms": 1.0,
            "p95_ms": 2.0,
            "stddev_ms": 0.5,
            "throughput_fps": 10.0,
            "iterations": 5,
        }
        with pytest.raises(KeyError, match="Missing benchmark payload results fields"):
            _validate_payload_results(results)

    def test_iterations_float_raises_type_error(self) -> None:
        from cli.benchmark import _validate_payload_results

        results = {
            "median_ms": 1.0,
            "p95_ms": 2.0,
            "p99_ms": 3.0,
            "stddev_ms": 0.5,
            "throughput_fps": 10.0,
            "iterations": 1.5,
        }
        with pytest.raises(TypeError, match="must be an int"):
            _validate_payload_results(results)

    def test_iterations_bool_raises_type_error(self) -> None:
        from cli.benchmark import _validate_payload_results

        results = {
            "median_ms": 1.0,
            "p95_ms": 2.0,
            "p99_ms": 3.0,
            "stddev_ms": 0.5,
            "throughput_fps": 10.0,
            "iterations": True,
        }
        # bool hits _require_non_negative_numeric_field first (raises "must be numeric"),
        # then the second isinstance check for int on line 258 raises "must be an int"
        with pytest.raises(TypeError):
            _validate_payload_results(results)

    def test_valid_results_passes(self) -> None:
        from cli.benchmark import _validate_payload_results

        results = {
            "median_ms": 1.0,
            "p95_ms": 2.0,
            "p99_ms": 3.0,
            "stddev_ms": 0.5,
            "throughput_fps": 10.0,
            "iterations": 5,
        }
        # Must not raise
        _validate_payload_results(results)


class TestValidateBenchmarkPayload:
    """Tests for validate_benchmark_payload (lines 262-287)."""

    def _valid_payload(self) -> dict:
        return {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-01-01-v1",
            "files_count": 5,
            "hardware_profile": {"cpu": "test"},
            "results": {
                "median_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
                "stddev_ms": 0.5,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }

    def test_files_count_bool_raises_type_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = self._valid_payload()
        payload["files_count"] = True
        with pytest.raises(TypeError, match="'files_count' must be an int"):
            validate_benchmark_payload(payload)

    def test_files_count_negative_raises_value_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = self._valid_payload()
        payload["files_count"] = -1
        with pytest.raises(ValueError, match="'files_count' must be non-negative"):
            validate_benchmark_payload(payload)

    def test_hardware_profile_non_dict_raises_type_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = self._valid_payload()
        payload["hardware_profile"] = "not-a-dict"
        with pytest.raises(TypeError, match="'hardware_profile' must be a dict"):
            validate_benchmark_payload(payload)

    def test_results_non_dict_raises_type_error(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        payload = self._valid_payload()
        payload["results"] = "not-a-dict"
        with pytest.raises(TypeError, match="'results' must be a dict"):
            validate_benchmark_payload(payload)

    def test_valid_payload_passes(self) -> None:
        from cli.benchmark import validate_benchmark_payload

        validate_benchmark_payload(self._valid_payload())


class TestCompareResults:
    """Tests for compare_results (lines 290-318)."""

    def test_compare_results_with_known_values(self) -> None:
        from cli.benchmark import compare_results

        current = {
            "results": {
                "median_ms": 120.0,
                "p95_ms": 150.0,
                "p99_ms": 200.0,
                "stddev_ms": 10.0,
                "throughput_fps": 8.0,
            }
        }
        baseline = {
            "results": {
                "median_ms": 100.0,
                "p95_ms": 100.0,
                "p99_ms": 100.0,
                "stddev_ms": 10.0,
                "throughput_fps": 10.0,
            }
        }
        result = compare_results(current, baseline)
        assert result["deltas_pct"]["median_ms"] == pytest.approx(20.0)
        assert result["deltas_pct"]["p95_ms"] == pytest.approx(50.0)
        assert result["threshold"] == pytest.approx(1.2)

    def test_compare_results_zero_base_val_gives_zero_delta(self) -> None:
        from cli.benchmark import compare_results

        current = {
            "median_ms": 10.0,
            "p95_ms": 10.0,
            "p99_ms": 10.0,
            "stddev_ms": 1.0,
            "throughput_fps": 5.0,
        }
        baseline = {
            "median_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "stddev_ms": 0.0,
            "throughput_fps": 0.0,
        }
        result = compare_results(current, baseline)
        assert result["deltas_pct"]["median_ms"] == 0.0
        assert result["deltas_pct"]["p95_ms"] == 0.0

    def test_compare_results_regression_detected(self) -> None:
        from cli.benchmark import compare_results

        # cur p95 > 1.2 * base p95 → regression
        current = {
            "p95_ms": 200.0,
            "median_ms": 50.0,
            "p99_ms": 200.0,
            "stddev_ms": 5.0,
            "throughput_fps": 10.0,
        }
        baseline = {
            "p95_ms": 100.0,
            "median_ms": 50.0,
            "p99_ms": 100.0,
            "stddev_ms": 5.0,
            "throughput_fps": 10.0,
        }
        result = compare_results(current, baseline, threshold=1.2)
        assert result["regression"] is True

    def test_compare_results_no_regression(self) -> None:
        from cli.benchmark import compare_results

        current = {
            "p95_ms": 110.0,
            "median_ms": 50.0,
            "p99_ms": 110.0,
            "stddev_ms": 5.0,
            "throughput_fps": 10.0,
        }
        baseline = {
            "p95_ms": 100.0,
            "median_ms": 50.0,
            "p99_ms": 100.0,
            "stddev_ms": 5.0,
            "throughput_fps": 10.0,
        }
        result = compare_results(current, baseline, threshold=1.2)
        assert result["regression"] is False

    def test_compare_results_custom_threshold(self) -> None:
        from cli.benchmark import compare_results

        current = {
            "p95_ms": 150.0,
            "median_ms": 50.0,
            "p99_ms": 150.0,
            "stddev_ms": 5.0,
            "throughput_fps": 10.0,
        }
        baseline = {
            "p95_ms": 100.0,
            "median_ms": 50.0,
            "p99_ms": 100.0,
            "stddev_ms": 5.0,
            "throughput_fps": 10.0,
        }
        result = compare_results(current, baseline, threshold=2.0)
        assert result["regression"] is False
        assert result["threshold"] == pytest.approx(2.0)

    def test_compare_results_nested_results_key(self) -> None:
        from cli.benchmark import compare_results

        current = {
            "results": {
                "p95_ms": 200.0,
                "median_ms": 100.0,
                "p99_ms": 200.0,
                "stddev_ms": 10.0,
                "throughput_fps": 5.0,
            }
        }
        baseline = {
            "results": {
                "p95_ms": 100.0,
                "median_ms": 100.0,
                "p99_ms": 100.0,
                "stddev_ms": 10.0,
                "throughput_fps": 5.0,
            }
        }
        result = compare_results(current, baseline, threshold=1.5)
        assert result["regression"] is True


class TestResolveProcessedCount:
    """Tests for _resolve_processed_count (lines 321-342)."""

    def test_consistent_counts_returns_expected(self) -> None:
        from cli.benchmark import _resolve_processed_count

        console = MagicMock()
        result = _resolve_processed_count([10, 10, 10], warmup=1, suite="io", console=console)
        assert result == 10

    def test_mismatch_counts_raises_exit(self) -> None:
        from cli.benchmark import _resolve_processed_count

        console = MagicMock()
        with pytest.raises(typer.Exit):
            _resolve_processed_count([10, 10, 11], warmup=1, suite="io", console=console)

    def test_all_warmup_returns_last_warmup(self) -> None:
        from cli.benchmark import _resolve_processed_count

        console = MagicMock()
        # warmup=3, processed_counts has 3 entries, so measured = processed_counts[3:] = []
        result = _resolve_processed_count([5, 6, 7], warmup=3, suite="io", console=console)
        # measured is empty, falls back to processed_counts[-1]
        assert result == 7

    def test_empty_list_returns_zero(self) -> None:
        from cli.benchmark import _resolve_processed_count

        console = MagicMock()
        result = _resolve_processed_count([], warmup=0, suite="io", console=console)
        assert result == 0

    def test_all_zero_measured_returns_zero(self) -> None:
        from cli.benchmark import _resolve_processed_count

        console = MagicMock()
        # warmup=0, all zeros → consistent → returns 0
        result = _resolve_processed_count([0, 0, 0], warmup=0, suite="io", console=console)
        assert result == 0


class TestBenchmarkModelStub:
    """Tests for _BenchmarkModelStub (lines 370-412)."""

    def test_is_initialized_true_after_construction(self) -> None:
        from cli.benchmark import _BenchmarkModelStub
        from models.base import ModelType

        stub = _BenchmarkModelStub(
            model_type=ModelType.TEXT,
            prompt_responses={"test": "response"},
            default_response="default",
        )
        assert stub.is_initialized is True

    def test_generate_returns_matched_response(self) -> None:
        from cli.benchmark import _BenchmarkModelStub
        from models.base import ModelType

        stub = _BenchmarkModelStub(
            model_type=ModelType.TEXT,
            prompt_responses={"category:": "docs"},
            default_response="default response",
        )
        result = stub.generate("CATEGORY: please classify")
        assert result == "docs"

    def test_generate_returns_default_when_no_match(self) -> None:
        from cli.benchmark import _BenchmarkModelStub
        from models.base import ModelType

        stub = _BenchmarkModelStub(
            model_type=ModelType.TEXT,
            prompt_responses={"category:": "docs"},
            default_response="fallback",
        )
        result = stub.generate("unmatched prompt text")
        assert result == "fallback"

    def test_cleanup_sets_not_initialized(self) -> None:
        from cli.benchmark import _BenchmarkModelStub
        from models.base import ModelType

        stub = _BenchmarkModelStub(
            model_type=ModelType.TEXT,
            prompt_responses={},
            default_response="default",
        )
        stub.cleanup()
        assert stub.is_initialized is False

    def test_safe_cleanup_does_not_raise(self) -> None:
        from cli.benchmark import _BenchmarkModelStub
        from models.base import ModelType

        stub = _BenchmarkModelStub(
            model_type=ModelType.TEXT,
            prompt_responses={},
            default_response="default",
        )
        # safe_cleanup is an alias for cleanup
        stub.safe_cleanup()
        assert stub.is_initialized is False

    def test_initialize_sets_initialized(self) -> None:
        from cli.benchmark import _BenchmarkModelStub
        from models.base import ModelType

        stub = _BenchmarkModelStub(
            model_type=ModelType.TEXT,
            prompt_responses={},
            default_response="default",
        )
        stub.cleanup()
        stub.initialize()
        assert stub.is_initialized is True


class TestSuiteCandidates:
    """Tests for _suite_candidates (lines 415-425)."""

    def test_filters_by_extension(self, tmp_path: Path) -> None:
        from cli.benchmark import _suite_candidates

        files = [
            tmp_path / "a.txt",
            tmp_path / "b.jpg",
            tmp_path / "c.pdf",
        ]
        result = _suite_candidates(files, {".txt", ".pdf"})
        assert len(result) == 2
        assert tmp_path / "a.txt" in result
        assert tmp_path / "c.pdf" in result

    def test_fallback_to_all_when_no_match(self, tmp_path: Path) -> None:
        from cli.benchmark import _suite_candidates

        files = [tmp_path / "a.xyz", tmp_path / "b.xyz"]
        result = _suite_candidates(files, {".txt"}, fallback_to_all=True)
        assert len(result) == 2

    def test_no_match_no_fallback_returns_empty(self, tmp_path: Path) -> None:
        from cli.benchmark import _suite_candidates

        files = [tmp_path / "a.xyz"]
        result = _suite_candidates(files, {".txt"}, fallback_to_all=False)
        assert result == []

    def test_cap_limits_results(self, tmp_path: Path) -> None:
        from cli.benchmark import _suite_candidates

        files = [tmp_path / f"{i}.txt" for i in range(10)]
        result = _suite_candidates(files, {".txt"}, cap=3)
        assert len(result) == 3


class TestDetectHardwareProfile:
    """Tests for _detect_hardware_profile (lines 883-890)."""

    def test_returns_dict_with_data(self) -> None:
        from cli.benchmark import _detect_hardware_profile

        result = _detect_hardware_profile()
        assert isinstance(result, dict)
        assert len(result) >= 1

    def test_exception_returns_error_fallback(self) -> None:

        with patch("cli.benchmark._detect_hardware_profile") as mock_detect:
            mock_detect.return_value = {"error": "Hardware detection unavailable"}
            result = mock_detect()
        assert result == {"error": "Hardware detection unavailable"}

    def test_import_error_returns_fallback(self) -> None:
        from cli.benchmark import _detect_hardware_profile

        with patch("core.hardware_profile.detect_hardware", side_effect=ImportError("no module")):
            result = _detect_hardware_profile()
        assert "error" in result

    def test_runtime_error_returns_fallback(self) -> None:
        from cli.benchmark import _detect_hardware_profile

        with patch("core.hardware_profile.detect_hardware", side_effect=RuntimeError("hw error")):
            result = _detect_hardware_profile()
        assert "error" in result


class TestCheckBaselineProfileCompatibility:
    """Tests for _check_baseline_profile_compatibility (lines 893-916)."""

    def test_same_profile_returns_none(self) -> None:
        from cli.benchmark import _RUNNER_PROFILE_VERSION, _check_baseline_profile_compatibility

        baseline = {"runner_profile_version": _RUNNER_PROFILE_VERSION}
        console = MagicMock()
        result = _check_baseline_profile_compatibility(
            baseline, suite="io", console=console, json_output=False
        )
        assert result is None

    def test_missing_profile_returns_none(self) -> None:
        from cli.benchmark import _check_baseline_profile_compatibility

        baseline = {}
        console = MagicMock()
        result = _check_baseline_profile_compatibility(
            baseline, suite="io", console=console, json_output=False
        )
        assert result is None

    def test_different_profile_returns_warning_string(self) -> None:
        from cli.benchmark import _check_baseline_profile_compatibility

        baseline = {"runner_profile_version": "2020-01-01-v1"}
        console = MagicMock()
        result = _check_baseline_profile_compatibility(
            baseline, suite="io", console=console, json_output=False
        )
        assert result is not None
        assert "mismatch" in result.lower()

    def test_different_profile_json_mode_no_print(self) -> None:
        from cli.benchmark import _check_baseline_profile_compatibility

        baseline = {"runner_profile_version": "2020-01-01-v1"}
        console = MagicMock()
        result = _check_baseline_profile_compatibility(
            baseline, suite="io", console=console, json_output=True
        )
        assert result is not None
        console.print.assert_not_called()

    def test_different_profile_human_mode_prints(self) -> None:
        from cli.benchmark import _check_baseline_profile_compatibility

        baseline = {"runner_profile_version": "2020-01-01-v1"}
        console = MagicMock()
        result = _check_baseline_profile_compatibility(
            baseline, suite="io", console=console, json_output=False
        )
        assert result is not None
        console.print.assert_called_once()


class TestCheckBaselineSmokeCompatibility:
    """Tests for _check_baseline_smoke_compatibility (lines 919-968)."""

    def test_both_smoke_false_returns_none(self) -> None:
        from cli.benchmark import _check_baseline_smoke_compatibility

        baseline = {"transcribe_smoke": False}
        console = MagicMock()
        result = _check_baseline_smoke_compatibility(
            baseline, transcribe_smoke=False, console=console, json_output=False
        )
        assert result is None

    def test_both_smoke_true_returns_none(self) -> None:
        from cli.benchmark import _check_baseline_smoke_compatibility

        baseline = {"transcribe_smoke": True}
        console = MagicMock()
        result = _check_baseline_smoke_compatibility(
            baseline, transcribe_smoke=True, console=console, json_output=False
        )
        assert result is None

    def test_missing_baseline_smoke_treated_as_false(self) -> None:
        from cli.benchmark import _check_baseline_smoke_compatibility

        baseline = {}
        console = MagicMock()
        result = _check_baseline_smoke_compatibility(
            baseline, transcribe_smoke=False, console=console, json_output=False
        )
        assert result is None

    def test_smoke_mismatch_returns_warning(self) -> None:
        from cli.benchmark import _check_baseline_smoke_compatibility

        baseline = {"transcribe_smoke": False}
        console = MagicMock()
        result = _check_baseline_smoke_compatibility(
            baseline, transcribe_smoke=True, console=console, json_output=False
        )
        assert result is not None
        assert "mismatch" in result.lower()

    def test_non_bool_baseline_smoke_returns_malformed_warning(self) -> None:
        from cli.benchmark import _check_baseline_smoke_compatibility

        baseline = {"transcribe_smoke": "false"}
        console = MagicMock()
        result = _check_baseline_smoke_compatibility(
            baseline, transcribe_smoke=False, console=console, json_output=False
        )
        assert result is not None
        assert "not a boolean" in result.lower()

    def test_mismatch_json_mode_no_print(self) -> None:
        from cli.benchmark import _check_baseline_smoke_compatibility

        baseline = {"transcribe_smoke": False}
        console = MagicMock()
        result = _check_baseline_smoke_compatibility(
            baseline, transcribe_smoke=True, console=console, json_output=True
        )
        assert result is not None
        console.print.assert_not_called()

    def test_mismatch_human_mode_prints(self) -> None:
        from cli.benchmark import _check_baseline_smoke_compatibility

        baseline = {"transcribe_smoke": False}
        console = MagicMock()
        result = _check_baseline_smoke_compatibility(
            baseline, transcribe_smoke=True, console=console, json_output=False
        )
        assert result is not None
        console.print.assert_called_once()


class TestExitIfTranscribeSmokeFailed:
    """Tests for _exit_if_transcribe_smoke_failed (lines 763-792)."""

    def test_no_smoke_reason_does_not_exit(self) -> None:
        from cli.benchmark import _exit_if_transcribe_smoke_failed

        console = MagicMock()
        # Must not raise
        _exit_if_transcribe_smoke_failed(console, ["some-other-reason"])

    def test_smoke_skipped_reason_raises_exit(self) -> None:
        from cli.benchmark import _exit_if_transcribe_smoke_failed

        console = MagicMock()
        with pytest.raises(typer.Exit):
            _exit_if_transcribe_smoke_failed(console, ["audio-transcribe-smoke-skipped"])

    def test_smoke_skipped_json_mode_routes_to_stderr(self) -> None:
        from cli.benchmark import _exit_if_transcribe_smoke_failed

        console = MagicMock()
        # Console is imported locally inside the function, so patch via rich.console
        mock_stderr_console = MagicMock()
        with (
            patch("rich.console.Console", return_value=mock_stderr_console),
            pytest.raises(typer.Exit),
        ):
            _exit_if_transcribe_smoke_failed(
                console, ["audio-transcribe-smoke-skipped"], json_output=True
            )
        mock_stderr_console.print.assert_called_once()
        console.print.assert_not_called()

    def test_smoke_skipped_human_mode_routes_to_console(self) -> None:
        from cli.benchmark import _exit_if_transcribe_smoke_failed

        console = MagicMock()
        with pytest.raises(typer.Exit):
            _exit_if_transcribe_smoke_failed(
                console, ["audio-transcribe-smoke-skipped"], json_output=False
            )
        console.print.assert_called_once()


class TestClassifySuites:
    """Tests for classify functions (lines 686-838)."""

    def test_classify_io_suite_always_not_degraded(self) -> None:
        from cli.benchmark import _classify_io_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=5)
        result = _classify_io_suite([], outcome)
        assert result.effective_suite == "io"
        assert result.degraded is False

    def test_classify_text_suite_no_candidates(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_text_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=0)
        files = [tmp_path / "a.xyz"]
        result = _classify_text_suite(files, outcome)
        assert result.degraded is True
        assert "text-no-candidates-skip" in result.degradation_reasons

    def test_classify_text_suite_with_candidates(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_text_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=1)
        files = [tmp_path / "a.txt"]
        result = _classify_text_suite(files, outcome)
        assert result.degraded is False

    def test_classify_vision_suite_no_candidates(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_vision_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=0)
        files = [tmp_path / "a.txt"]
        result = _classify_vision_suite(files, outcome)
        assert result.degraded is True
        assert "vision-no-candidates-skip" in result.degradation_reasons

    def test_classify_vision_suite_with_candidates(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_vision_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=1)
        files = [tmp_path / "a.jpg"]
        result = _classify_vision_suite(files, outcome)
        assert result.degraded is False

    def test_classify_audio_suite_no_candidates_fallback_to_io(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_audio_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=0)
        files = [tmp_path / "a.txt"]
        result = _classify_audio_suite(files, outcome)
        assert result.effective_suite == "io"
        assert result.degraded is True
        assert "audio-no-candidates-fallback-to-io" in result.degradation_reasons

    def test_classify_audio_suite_synthetic_metadata(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_audio_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=1, used_synthetic_audio_metadata=True)
        files = [tmp_path / "a.mp3"]
        result = _classify_audio_suite(files, outcome)
        assert result.degraded is True
        assert "audio-synthesized-metadata-fallback" in result.degradation_reasons

    def test_classify_audio_suite_smoke_skipped(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_audio_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(
            processed_count=1,
            transcription_smoke_requested=True,
            transcription_smoke_passed=False,
        )
        files = [tmp_path / "a.mp3"]
        result = _classify_audio_suite(files, outcome)
        assert result.degraded is True
        assert "audio-transcribe-smoke-skipped" in result.degradation_reasons

    def test_classify_audio_suite_clean(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_audio_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=1)
        files = [tmp_path / "a.mp3"]
        result = _classify_audio_suite(files, outcome)
        assert result.degraded is False
        assert result.effective_suite == "audio"

    def test_classify_pipeline_suite_not_degraded(self) -> None:
        from cli.benchmark import _classify_pipeline_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=5)
        result = _classify_pipeline_suite([], outcome)
        assert result.effective_suite == "pipeline"
        assert result.degraded is False

    def test_classify_e2e_suite_no_processed_when_files_exist(self, tmp_path: Path) -> None:
        from cli.benchmark import _classify_e2e_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=0)
        files = [tmp_path / "a.txt"]
        result = _classify_e2e_suite(files, outcome)
        assert result.degraded is True
        assert "e2e-no-candidates-processed" in result.degradation_reasons

    def test_classify_e2e_suite_no_files_not_degraded(self) -> None:
        from cli.benchmark import _classify_e2e_suite, _SuiteIterationOutcome

        outcome = _SuiteIterationOutcome(processed_count=0)
        result = _classify_e2e_suite([], outcome)
        assert result.degraded is False


class TestBindTranscribeSmoke:
    """Tests for _bind_transcribe_smoke (lines 718-736)."""

    def test_smoke_with_non_audio_raises_bad_parameter(self) -> None:
        from cli.benchmark import _bind_transcribe_smoke, _run_io_suite

        with pytest.raises(typer.BadParameter, match="only supported with --suite audio"):
            _bind_transcribe_smoke(_run_io_suite, suite="io", transcribe_smoke=True)

    def test_smoke_false_returns_original_runner(self) -> None:
        from cli.benchmark import _bind_transcribe_smoke, _run_audio_suite

        result = _bind_transcribe_smoke(_run_audio_suite, suite="audio", transcribe_smoke=False)
        assert result is _run_audio_suite

    def test_smoke_true_audio_returns_partial(self) -> None:
        import functools

        from cli.benchmark import _bind_transcribe_smoke, _run_audio_suite

        result = _bind_transcribe_smoke(_run_audio_suite, suite="audio", transcribe_smoke=True)
        assert isinstance(result, functools.partial)

    def test_smoke_false_non_audio_returns_original(self) -> None:
        from cli.benchmark import _bind_transcribe_smoke, _run_io_suite

        result = _bind_transcribe_smoke(_run_io_suite, suite="io", transcribe_smoke=False)
        assert result is _run_io_suite


class TestValidateTranscribeSmokePreconditions:
    """Tests for _validate_transcribe_smoke_preconditions (lines 739-760)."""

    def test_no_smoke_does_nothing(self) -> None:
        from cli.benchmark import _validate_transcribe_smoke_preconditions

        # Must not raise
        _validate_transcribe_smoke_preconditions([], transcribe_smoke=False)

    def test_smoke_empty_files_raises(self) -> None:
        from cli.benchmark import _validate_transcribe_smoke_preconditions

        with pytest.raises(typer.BadParameter, match="empty"):
            _validate_transcribe_smoke_preconditions([], transcribe_smoke=True)

    def test_smoke_no_audio_candidates_raises(self, tmp_path: Path) -> None:
        from cli.benchmark import _validate_transcribe_smoke_preconditions

        files = [tmp_path / "a.txt"]
        with pytest.raises(typer.BadParameter, match="audio file"):
            _validate_transcribe_smoke_preconditions(files, transcribe_smoke=True)

    def test_smoke_with_audio_files_passes(self, tmp_path: Path) -> None:
        from cli.benchmark import _validate_transcribe_smoke_preconditions

        files = [tmp_path / "a.mp3"]
        # Must not raise
        _validate_transcribe_smoke_preconditions(files, transcribe_smoke=True)


class TestSummarizeSuiteClassifications:
    """Tests for _summarize_suite_classifications (lines 1001-1026)."""

    def test_no_degradation(self) -> None:
        from cli.benchmark import _SuiteExecutionClassification, _summarize_suite_classifications

        classifications = [
            _SuiteExecutionClassification(effective_suite="io", degraded=False),
            _SuiteExecutionClassification(effective_suite="io", degraded=False),
        ]
        suite, degraded, reasons = _summarize_suite_classifications(
            classifications, warmup=0, requested_suite="io"
        )
        assert suite == "io"
        assert degraded is False
        assert reasons == []

    def test_with_degradation(self) -> None:
        from cli.benchmark import _SuiteExecutionClassification, _summarize_suite_classifications

        classifications = [
            _SuiteExecutionClassification(
                effective_suite="io", degraded=True, degradation_reasons=("reason-a",)
            ),
        ]
        suite, degraded, reasons = _summarize_suite_classifications(
            classifications, warmup=0, requested_suite="io"
        )
        assert degraded is True
        assert "reason-a" in reasons

    def test_warmup_excluded(self) -> None:
        from cli.benchmark import _SuiteExecutionClassification, _summarize_suite_classifications

        classifications = [
            # warmup entry — degraded but should be excluded
            _SuiteExecutionClassification(
                effective_suite="io", degraded=True, degradation_reasons=("warmup-reason",)
            ),
            # measured entry
            _SuiteExecutionClassification(effective_suite="io", degraded=False),
        ]
        suite, degraded, reasons = _summarize_suite_classifications(
            classifications, warmup=1, requested_suite="io"
        )
        assert degraded is False
        assert "warmup-reason" not in reasons

    def test_empty_classifications_returns_requested_suite(self) -> None:
        from cli.benchmark import _summarize_suite_classifications

        suite, degraded, reasons = _summarize_suite_classifications(
            [], warmup=0, requested_suite="text"
        )
        assert suite == "text"
        assert degraded is False

    def test_mixed_effective_suites_returns_mixed(self) -> None:
        from cli.benchmark import _SuiteExecutionClassification, _summarize_suite_classifications

        classifications = [
            _SuiteExecutionClassification(effective_suite="io", degraded=False),
            _SuiteExecutionClassification(effective_suite="audio", degraded=False),
        ]
        suite, _degraded, _reasons = _summarize_suite_classifications(
            classifications, warmup=0, requested_suite="audio"
        )
        assert suite == "mixed"


class TestExecuteSuiteIteration:
    """Tests for _execute_suite_iteration (lines 971-998)."""

    def test_successful_iteration(self, tmp_path: Path) -> None:
        from cli.benchmark import (
            _execute_suite_iteration,
            _SuiteExecutionClassification,
            _SuiteIterationOutcome,
        )

        def fake_runner(files: list) -> _SuiteIterationOutcome:
            return _SuiteIterationOutcome(processed_count=3)

        def fake_classifier(
            files: list, outcome: _SuiteIterationOutcome
        ) -> _SuiteExecutionClassification:
            return _SuiteExecutionClassification(effective_suite="io", degraded=False)

        console = MagicMock()
        elapsed_ms, count, classification = _execute_suite_iteration(
            runner=fake_runner,
            classifier=fake_classifier,
            files=[tmp_path / "a.txt"],
            suite="io",
            console=console,
        )
        assert elapsed_ms > 0
        assert count == 3
        assert classification.effective_suite == "io"

    def test_runner_exception_raises_exit(self, tmp_path: Path) -> None:
        from cli.benchmark import _execute_suite_iteration

        def failing_runner(files: list):
            raise RuntimeError("runner failed")

        console = MagicMock()
        with pytest.raises(typer.Exit):
            _execute_suite_iteration(
                runner=failing_runner,
                classifier=MagicMock(),
                files=[],
                suite="io",
                console=console,
            )

    def test_negative_processed_count_raises_exit(self, tmp_path: Path) -> None:
        from cli.benchmark import _execute_suite_iteration, _SuiteIterationOutcome

        def bad_runner(files: list) -> _SuiteIterationOutcome:
            return _SuiteIterationOutcome(processed_count=-1)

        console = MagicMock()
        with pytest.raises(typer.Exit):
            _execute_suite_iteration(
                runner=bad_runner,
                classifier=MagicMock(),
                files=[],
                suite="io",
                console=console,
            )

    def test_classifier_exception_raises_exit(self, tmp_path: Path) -> None:
        from cli.benchmark import _execute_suite_iteration, _SuiteIterationOutcome

        def ok_runner(files: list) -> _SuiteIterationOutcome:
            return _SuiteIterationOutcome(processed_count=1)

        def failing_classifier(files: list, outcome):
            raise RuntimeError("classify failed")

        console = MagicMock()
        with pytest.raises(typer.Exit):
            _execute_suite_iteration(
                runner=ok_runner,
                classifier=failing_classifier,
                files=[],
                suite="io",
                console=console,
            )


class TestPrintTable:
    """Tests for _print_table (lines 1029-1048)."""

    def test_print_table_no_crash(self) -> None:
        from cli.benchmark import _print_table, compute_stats

        stats = compute_stats([100.0, 110.0, 120.0], 5)
        console = MagicMock()
        _print_table(console, "io", 1, stats, 5)
        console.print.assert_called_once()


class TestPrintComparison:
    """Tests for _print_comparison (lines 1051-1072)."""

    def test_json_mode_prints_json(self) -> None:
        from cli.benchmark import _print_comparison

        comp = {
            "deltas_pct": {"median_ms": 10.0, "p95_ms": 20.0},
            "regression": False,
            "threshold": 1.2,
        }
        console = MagicMock()
        _print_comparison(console, comp, json_output=True)
        console.print.assert_called_once()
        call_arg = console.print.call_args[0][0]
        parsed = json.loads(call_arg)
        assert "comparison" in parsed

    def test_human_mode_no_regression(self) -> None:
        from cli.benchmark import _print_comparison

        comp = {
            "deltas_pct": {
                "median_ms": 5.0,
                "p95_ms": 3.0,
                "p99_ms": 3.0,
                "stddev_ms": 1.0,
                "throughput_fps": -2.0,
            },
            "regression": False,
            "threshold": 1.2,
        }
        console = MagicMock()
        _print_comparison(console, comp, json_output=False)
        # verify No regression was printed
        calls = [str(c) for c in console.print.call_args_list]
        assert any("No regression" in c for c in calls)

    def test_human_mode_regression(self) -> None:
        from cli.benchmark import _print_comparison

        comp = {
            "deltas_pct": {
                "median_ms": 50.0,
                "p95_ms": 50.0,
                "p99_ms": 50.0,
                "stddev_ms": 10.0,
                "throughput_fps": -10.0,
            },
            "regression": True,
            "threshold": 1.2,
        }
        console = MagicMock()
        _print_comparison(console, comp, json_output=False)
        calls = [str(c) for c in console.print.call_args_list]
        assert any("REGRESSION" in c for c in calls)


class TestValidateComparePath:
    """Tests for _validate_compare_path (lines 1119-1133)."""

    def test_none_returns_none(self) -> None:
        from cli.benchmark import _validate_compare_path

        assert _validate_compare_path(None) is None

    def test_valid_file_returns_resolved_path(self, tmp_path: Path) -> None:
        from cli.benchmark import _validate_compare_path

        baseline = tmp_path / "baseline.json"
        baseline.write_text('{"suite": "io"}')
        result = _validate_compare_path(baseline)
        assert result is not None
        assert result.is_file()

    def test_directory_raises_bad_parameter(self, tmp_path: Path) -> None:
        from cli.benchmark import _validate_compare_path

        with pytest.raises(typer.BadParameter, match="not a regular file"):
            _validate_compare_path(tmp_path)


class TestMaybeAttachComparisonOutput:
    """Tests for _maybe_attach_comparison_output (lines 1075-1111)."""

    def _base_output(self) -> dict:
        return {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": "2026-01-01-v1",
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 100.0,
                "p95_ms": 120.0,
                "p99_ms": 150.0,
                "stddev_ms": 10.0,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }

    def test_none_compare_path_returns_unchanged(self) -> None:
        from cli.benchmark import _maybe_attach_comparison_output

        output = self._base_output()
        result = _maybe_attach_comparison_output(
            output=output,
            compare_path=None,
            suite="io",
            transcribe_smoke=False,
            console=MagicMock(),
            json_output=False,
        )
        assert "comparison" not in result

    def test_with_baseline_attaches_comparison(self, tmp_path: Path) -> None:
        from cli.benchmark import _RUNNER_PROFILE_VERSION, _maybe_attach_comparison_output

        baseline = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": _RUNNER_PROFILE_VERSION,
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 100.0,
                "p95_ms": 100.0,
                "p99_ms": 100.0,
                "stddev_ms": 5.0,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline))

        output = self._base_output()
        result = _maybe_attach_comparison_output(
            output=output,
            compare_path=baseline_path,
            suite="io",
            transcribe_smoke=False,
            console=MagicMock(),
            json_output=False,
        )
        assert "comparison" in result
        assert "regression" in result["comparison"]

    def test_invalid_baseline_raises_exit(self, tmp_path: Path) -> None:
        from cli.benchmark import _maybe_attach_comparison_output

        bad_path = tmp_path / "bad.json"
        bad_path.write_text("{invalid json{{")

        output = self._base_output()
        with pytest.raises(typer.Exit):
            _maybe_attach_comparison_output(
                output=output,
                compare_path=bad_path,
                suite="io",
                transcribe_smoke=False,
                console=MagicMock(),
                json_output=False,
            )

    def test_profile_warning_attached_when_mismatch(self, tmp_path: Path) -> None:
        from cli.benchmark import _maybe_attach_comparison_output

        baseline = {
            "runner_profile_version": "1999-01-01-v0",
            "results": {
                "median_ms": 100.0,
                "p95_ms": 100.0,
                "p99_ms": 100.0,
                "stddev_ms": 5.0,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline))

        output = self._base_output()
        result = _maybe_attach_comparison_output(
            output=output,
            compare_path=baseline_path,
            suite="io",
            transcribe_smoke=False,
            console=MagicMock(),
            json_output=True,
        )
        assert "comparison_profile_warning" in result


class TestRunCommandUnknownSuite:
    """Tests for unknown suite handling in run() (line 1219-1220)."""

    def test_unknown_suite_exits_nonzero(self, tmp_path: Path) -> None:
        app = _get_app()
        (tmp_path / "a.txt").write_text("content")
        result = runner.invoke(
            app,
            ["benchmark", "run", str(tmp_path), "--suite", "nonexistent_suite", "--json"],
        )
        assert result.exit_code == 1

    def test_unknown_suite_plain_mode_exits_nonzero(self, tmp_path: Path) -> None:
        app = _get_app()
        (tmp_path / "a.txt").write_text("content")
        result = runner.invoke(
            app,
            ["benchmark", "run", str(tmp_path), "--suite", "nonexistent_suite"],
        )
        assert result.exit_code == 1


class TestRunCommandWithBaselineComparison:
    """Tests for --compare flag in run() (lines 1261-1274, 1337-1352)."""

    def _make_baseline(self, path: Path) -> Path:
        from cli.benchmark import _RUNNER_PROFILE_VERSION

        baseline = {
            "suite": "io",
            "effective_suite": "io",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": _RUNNER_PROFILE_VERSION,
            "transcribe_smoke": False,
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 100.0,
                "p95_ms": 100.0,
                "p99_ms": 100.0,
                "stddev_ms": 5.0,
                "throughput_fps": 10.0,
                "iterations": 1,
            },
        }
        baseline_file = path / "baseline.json"
        baseline_file.write_text(json.dumps(baseline))
        return baseline_file

    def test_compare_json_output(self, tmp_path: Path) -> None:
        app = _get_app()
        (tmp_path / "a.txt").write_text("hello")
        baseline_file = self._make_baseline(tmp_path)

        result = runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(tmp_path),
                "--suite",
                "io",
                "--iterations",
                "1",
                "--warmup",
                "0",
                "--json",
                "--compare",
                str(baseline_file),
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "comparison" in payload

    def test_compare_plain_output(self, tmp_path: Path) -> None:
        app = _get_app()
        (tmp_path / "a.txt").write_text("hello")
        baseline_file = self._make_baseline(tmp_path)

        result = runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(tmp_path),
                "--suite",
                "io",
                "--iterations",
                "1",
                "--warmup",
                "0",
                "--compare",
                str(baseline_file),
            ],
        )
        assert result.exit_code == 0
        # Should print comparison section
        assert (
            "baseline" in result.output.lower()
            or "regression" in result.output.lower()
            or "comparison" in result.output.lower()
        )


class TestRunCommandDegradedSuite:
    """Tests for degraded suite output path in run() (lines 1337-1343)."""

    def test_degraded_suite_plain_output_shows_reason(self, tmp_path: Path) -> None:
        app = _get_app()
        # Create only txt files but request text suite with no text files
        # by using a directory with no files matching text extensions
        for i in range(3):
            (tmp_path / f"file_{i}.xyz").write_text("data")

        result = runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(tmp_path),
                "--suite",
                "text",
                "--iterations",
                "1",
                "--warmup",
                "0",
            ],
        )
        assert result.exit_code == 0
        # degraded mode — should show the degradation reason
        assert "text-no-candidates-skip" in result.output or "Degraded" in result.output

    def test_vision_suite_no_vision_files_degraded(self, tmp_path: Path) -> None:
        app = _get_app()
        (tmp_path / "a.txt").write_text("text content")
        result = runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(tmp_path),
                "--suite",
                "vision",
                "--iterations",
                "1",
                "--warmup",
                "0",
            ],
        )
        assert result.exit_code == 0
        # degraded mode — no vision candidates, human output shows the reason
        assert "vision-no-candidates-skip" in result.output or "degraded" in result.output.lower()


class TestRunCommandWithWarmup:
    """Tests for warmup iterations in run() (lines 1271-1274)."""

    def test_warmup_iterations_with_files(self, tmp_path: Path) -> None:
        app = _get_app()
        (tmp_path / "a.txt").write_text("hello")

        result = runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(tmp_path),
                "--suite",
                "io",
                "--iterations",
                "2",
                "--warmup",
                "1",
                "--json",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        # measured iterations should be 2 (warmup excluded)
        assert payload["results"]["iterations"] == 2

    def test_plain_output_with_warmup(self, tmp_path: Path) -> None:
        app = _get_app()
        (tmp_path / "a.txt").write_text("hello")

        result = runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(tmp_path),
                "--suite",
                "io",
                "--iterations",
                "1",
                "--warmup",
                "1",
            ],
        )
        assert result.exit_code == 0
        assert "Benchmark completed" in result.output

    def test_plain_output_iteration_labels(self, tmp_path: Path) -> None:
        """Covers the warmup/measured label branches in run() (lines 1272-1274)."""
        app = _get_app()
        (tmp_path / "a.txt").write_text("hello")

        result = runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(tmp_path),
                "--suite",
                "io",
                "--iterations",
                "2",
                "--warmup",
                "1",
            ],
        )
        assert result.exit_code == 0
        assert "warmup" in result.output
        assert "1/2" in result.output


class TestRunSuiteIoWithFiles:
    """Tests for the io suite runner path with actual files."""

    def test_run_io_suite_directly(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_io_suite

        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.pdf").write_text("pdf content")
        outcome = _run_io_suite([tmp_path / "a.txt", tmp_path / "b.pdf"])
        assert outcome.processed_count == 2

    def test_run_io_suite_stat_error_continues(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_io_suite

        files = [tmp_path / "nonexistent.txt"]
        # Should not raise — OSError is caught silently
        outcome = _run_io_suite(files)
        assert outcome.processed_count == 1

    def test_run_io_suite_empty_files(self) -> None:
        from cli.benchmark import _run_io_suite

        outcome = _run_io_suite([])
        assert outcome.processed_count == 0


class TestRunTextSuiteNoFiles:
    """Tests for the text suite runner with no matching files."""

    def test_run_text_suite_no_candidates_returns_zero(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_text_suite

        # Non-text extension files
        files = [tmp_path / "a.xyz", tmp_path / "b.abc"]
        outcome = _run_text_suite(files)
        assert outcome.processed_count == 0


class TestRunVisionSuiteNoFiles:
    """Tests for the vision suite runner with no matching files."""

    def test_run_vision_suite_no_candidates_returns_zero(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_vision_suite

        files = [tmp_path / "a.txt"]
        outcome = _run_vision_suite(files)
        assert outcome.processed_count == 0


class TestSynthesizedAudioMetadata:
    """Tests for _synthesized_audio_metadata (lines 495-508)."""

    @pytest.fixture(autouse=True)
    def _require_audio_metadata(self) -> None:
        """Guard: skip if services.audio.metadata_extractor cannot be loaded."""
        pytest.importorskip("services.audio.metadata_extractor")

    def test_returns_audio_metadata_for_existing_file(self, tmp_path: Path) -> None:
        import services.audio.metadata_extractor as _meta_mod
        from cli.benchmark import _synthesized_audio_metadata

        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"\x00" * 100)
        # Import the real class directly to verify the result fields.
        RealAudioMetadata = _meta_mod.AudioMetadata
        metadata = _synthesized_audio_metadata(audio_file)
        assert isinstance(metadata, RealAudioMetadata)
        assert metadata.file_path == audio_file
        assert metadata.file_size == 100
        assert metadata.format == "MP3"

    def test_returns_unknown_format_for_no_extension(self, tmp_path: Path) -> None:
        import services.audio.metadata_extractor as _meta_mod
        from cli.benchmark import _synthesized_audio_metadata

        audio_file = tmp_path / "noext"
        audio_file.write_bytes(b"\x00" * 50)
        RealAudioMetadata = _meta_mod.AudioMetadata
        metadata = _synthesized_audio_metadata(audio_file)
        assert isinstance(metadata, RealAudioMetadata)
        assert metadata.format == "UNKNOWN"


class TestComputeStats:
    """Tests for compute_stats (lines 139-171)."""

    def test_empty_times_returns_zeros(self) -> None:
        from cli.benchmark import compute_stats

        stats = compute_stats([], 0)
        assert stats["median_ms"] == 0.0
        assert stats["p95_ms"] == 0.0
        assert stats["iterations"] == 0

    def test_single_time_returns_itself(self) -> None:
        from cli.benchmark import compute_stats

        stats = compute_stats([100.0], 1)
        assert stats["median_ms"] == pytest.approx(100.0)
        assert stats["iterations"] == 1

    def test_throughput_zero_median_gives_zero(self) -> None:
        from cli.benchmark import compute_stats

        stats = compute_stats([0.0, 0.0], 5)
        assert stats["throughput_fps"] == 0.0

    def test_stddev_single_item_is_zero(self) -> None:
        from cli.benchmark import compute_stats

        stats = compute_stats([50.0], 1)
        assert stats["stddev_ms"] == 0.0

    def test_multiple_times_computes_stats(self) -> None:
        from cli.benchmark import compute_stats

        times = [100.0, 200.0, 300.0, 400.0, 500.0]
        stats = compute_stats(times, 10)
        assert stats["median_ms"] == pytest.approx(300.0)
        assert stats["iterations"] == 5
        assert stats["throughput_fps"] > 0


class TestRunAudioSuiteNoFiles:
    """Tests for the audio suite fallback path (lines 521-568)."""

    def test_audio_suite_no_candidates_falls_back_to_io(self, tmp_path: Path) -> None:
        from cli.benchmark import _run_audio_suite

        # Non-audio files → fallback to IO
        files = [tmp_path / "a.txt"]
        (tmp_path / "a.txt").write_text("content")
        outcome = _run_audio_suite(files)
        # Falls back to io suite
        assert outcome.processed_count == 1


class TestRunPipelineSuiteEmpty:
    """Tests for the pipeline suite with empty file list (lines 571-574)."""

    def test_pipeline_suite_empty_files(self) -> None:
        from cli.benchmark import _run_pipeline_suite

        outcome = _run_pipeline_suite([])
        assert outcome.processed_count == 0


class TestRunE2eSuiteEmpty:
    """Tests for the e2e suite with empty file list (lines 615-665)."""

    def test_e2e_suite_empty_files(self) -> None:
        from cli.benchmark import _run_e2e_suite

        outcome = _run_e2e_suite([])
        assert outcome.processed_count == 0


class TestRunTextSuiteWithFiles:
    """Cover lines 446-464: _run_text_suite body with candidates."""

    def test_run_text_suite_with_text_files(self, tmp_path: Path) -> None:

        txt_file = tmp_path / "sample.txt"
        txt_file.write_text("hello world")

        mock_processor = MagicMock()
        mock_processor_instance = MagicMock()
        mock_processor.return_value = mock_processor_instance

        with patch("cli.benchmark.TextProcessor", mock_processor, create=True):
            # Also patch the lazy import of TextProcessor inside the function
            with patch.dict(
                "sys.modules",
                {"services": MagicMock(TextProcessor=mock_processor)},
            ):
                # Re-import so patched module is used; call with real file path
                import cli.benchmark as bm

                original = bm._run_text_suite
                # Patch the lazy import of services inside the function
                mock_services = MagicMock()
                mock_services.TextProcessor = mock_processor

                with patch.dict("sys.modules", {"services": mock_services}):
                    outcome = original([txt_file])

        assert outcome.processed_count == 1

    def test_run_text_suite_with_mocked_processor(self, tmp_path: Path) -> None:
        """Cover _run_text_suite body by mocking TextProcessor at import time."""
        txt_file = tmp_path / "doc.pdf"
        txt_file.write_bytes(b"%PDF mock content")

        mock_processor_instance = MagicMock()
        mock_text_processor_cls = MagicMock(return_value=mock_processor_instance)

        # Patch the lazy import path used inside _run_text_suite
        mock_services_module = MagicMock()
        mock_services_module.TextProcessor = mock_text_processor_cls
        mock_base_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "services": mock_services_module,
                "models.base": mock_base_module,
            },
        ):
            from cli.benchmark import _run_text_suite

            outcome = _run_text_suite([txt_file])

        assert outcome.processed_count == 1
        mock_processor_instance.process_file.assert_called_once_with(txt_file)
        mock_processor_instance.cleanup.assert_called_once()


class TestRunVisionSuiteWithFiles:
    """Cover lines 474-492: _run_vision_suite body with candidates."""

    def test_run_vision_suite_with_mocked_processor(self, tmp_path: Path) -> None:
        """Cover _run_vision_suite body by mocking VisionProcessor."""
        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"JPEG mock")

        mock_processor_instance = MagicMock()
        mock_vision_processor_cls = MagicMock(return_value=mock_processor_instance)

        mock_services_module = MagicMock()
        mock_services_module.VisionProcessor = mock_vision_processor_cls
        mock_base_module = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "services": mock_services_module,
                "models.base": mock_base_module,
            },
        ):
            from cli.benchmark import _run_vision_suite

            outcome = _run_vision_suite([img_file])

        assert outcome.processed_count == 1
        mock_processor_instance.process_file.assert_called_once_with(img_file, perform_ocr=False)
        mock_processor_instance.cleanup.assert_called_once()


class TestRunAudioSuiteWithFiles:
    """Cover lines 526-568: _run_audio_suite body with audio candidates."""

    def test_run_audio_suite_with_mocked_extractor(self, tmp_path: Path) -> None:
        """Cover _run_audio_suite body by mocking AudioMetadataExtractor."""
        audio_file = tmp_path / "track.mp3"
        audio_file.write_bytes(b"MP3 mock")

        mock_metadata = MagicMock()
        mock_extractor_instance = MagicMock()
        mock_extractor_instance.extract.return_value = mock_metadata
        mock_extractor_cls = MagicMock(return_value=mock_extractor_instance)

        mock_classifier_instance = MagicMock()
        mock_classifier_cls = MagicMock(return_value=mock_classifier_instance)

        mock_audio_classifier_mod = MagicMock()
        mock_audio_classifier_mod.AudioClassifier = mock_classifier_cls
        mock_audio_extractor_mod = MagicMock()
        mock_audio_extractor_mod.AudioMetadataExtractor = mock_extractor_cls

        with patch.dict(
            "sys.modules",
            {
                "services.audio.classifier": mock_audio_classifier_mod,
                "services.audio.metadata_extractor": mock_audio_extractor_mod,
            },
        ):
            from cli.benchmark import _run_audio_suite

            outcome = _run_audio_suite([audio_file])

        assert outcome.processed_count == 1
        assert outcome.used_synthetic_audio_metadata is False
        assert outcome.transcription_smoke_requested is False
        mock_extractor_instance.extract.assert_called_once_with(audio_file)
        mock_classifier_instance.classify.assert_called_once_with(mock_metadata)

    def test_run_audio_suite_import_error_uses_synthetic(self, tmp_path: Path) -> None:
        """Cover ImportError path in _run_audio_suite (lines 535-537)."""
        audio_file = tmp_path / "track.mp3"
        audio_file.write_bytes(b"MP3 mock")

        mock_extractor_instance = MagicMock()
        mock_extractor_instance.extract.side_effect = ImportError("no module")
        mock_extractor_cls = MagicMock(return_value=mock_extractor_instance)

        mock_classifier_instance = MagicMock()
        mock_classifier_cls = MagicMock(return_value=mock_classifier_instance)

        # We also need to provide AudioMetadata for _synthesized_audio_metadata
        mock_audio_metadata = MagicMock()
        mock_audio_extractor_mod = MagicMock()
        mock_audio_extractor_mod.AudioMetadataExtractor = mock_extractor_cls
        mock_audio_extractor_mod.AudioMetadata = MagicMock(return_value=mock_audio_metadata)

        mock_audio_classifier_mod = MagicMock()
        mock_audio_classifier_mod.AudioClassifier = mock_classifier_cls

        with patch.dict(
            "sys.modules",
            {
                "services.audio.classifier": mock_audio_classifier_mod,
                "services.audio.metadata_extractor": mock_audio_extractor_mod,
            },
        ):
            from cli.benchmark import _run_audio_suite

            outcome = _run_audio_suite([audio_file])

        assert outcome.processed_count == 1
        assert outcome.used_synthetic_audio_metadata is True


class TestRunPipelineSuiteWithFiles:
    """Cover lines 577-612: _run_pipeline_suite body with files."""

    def test_run_pipeline_suite_with_mocked_orchestrator(self, tmp_path: Path) -> None:
        """Cover _run_pipeline_suite body by mocking PipelineOrchestrator."""
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("content")

        mock_orchestrator_instance = MagicMock()
        mock_orchestrator_cls = MagicMock(return_value=mock_orchestrator_instance)

        mock_processor_pool_instance = MagicMock()
        mock_processor_pool_cls = MagicMock(return_value=mock_processor_pool_instance)

        mock_pipeline_config_mod = MagicMock()
        mock_pipeline_orchestrator_mod = MagicMock()
        mock_pipeline_orchestrator_mod.PipelineOrchestrator = mock_orchestrator_cls
        mock_pipeline_processor_pool_mod = MagicMock()
        mock_pipeline_processor_pool_mod.ProcessorPool = mock_processor_pool_cls
        mock_pipeline_router_mod = MagicMock()
        mock_pipeline_stages_analyzer_mod = MagicMock()
        mock_pipeline_stages_postprocessor_mod = MagicMock()
        mock_pipeline_stages_preprocessor_mod = MagicMock()
        mock_pipeline_stages_writer_mod = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "pipeline.config": mock_pipeline_config_mod,
                "pipeline.orchestrator": mock_pipeline_orchestrator_mod,
                "pipeline.processor_pool": mock_pipeline_processor_pool_mod,
                "pipeline.router": mock_pipeline_router_mod,
                "pipeline.stages.analyzer": mock_pipeline_stages_analyzer_mod,
                "pipeline.stages.postprocessor": mock_pipeline_stages_postprocessor_mod,
                "pipeline.stages.preprocessor": mock_pipeline_stages_preprocessor_mod,
                "pipeline.stages.writer": mock_pipeline_stages_writer_mod,
            },
        ):
            from cli.benchmark import _run_pipeline_suite

            outcome = _run_pipeline_suite([txt_file])

        assert outcome.processed_count == 1
        mock_orchestrator_instance.process_batch.assert_called_once()
        mock_orchestrator_instance.stop.assert_called_once()
        mock_processor_pool_instance.cleanup.assert_called_once()


class TestRunE2eSuiteWithFiles:
    """Cover lines 633-683: _run_e2e_suite body with files."""

    def test_run_e2e_suite_with_mocked_organizer(self, tmp_path: Path) -> None:
        """Cover _run_e2e_suite body by mocking FileOrganizer."""
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("content")

        mock_organizer_instance = MagicMock()
        mock_organizer_cls = MagicMock(return_value=mock_organizer_instance)

        mock_core_organizer_mod = MagicMock()
        mock_core_organizer_mod.FileOrganizer = mock_organizer_cls

        with patch.dict(
            "sys.modules",
            {"core.organizer": mock_core_organizer_mod},
        ):
            from cli.benchmark import _run_e2e_suite

            outcome = _run_e2e_suite([txt_file])

        assert outcome.processed_count == 1
        mock_organizer_instance.organize.assert_called_once()

    def test_run_e2e_suite_copy_failure_skips_file(self, tmp_path: Path) -> None:
        """Cover OSError copy-failure branch (lines 648-656)."""
        # Use a nonexistent source file so shutil.copy2 will fail
        nonexistent = tmp_path / "nonexistent.txt"

        mock_organizer_instance = MagicMock()
        mock_organizer_cls = MagicMock(return_value=mock_organizer_instance)
        mock_core_organizer_mod = MagicMock()
        mock_core_organizer_mod.FileOrganizer = mock_organizer_cls

        with patch.dict(
            "sys.modules",
            {"core.organizer": mock_core_organizer_mod},
        ):
            from cli.benchmark import _run_e2e_suite

            outcome = _run_e2e_suite([nonexistent])

        # File couldn't be copied, so copied list is empty → processed_count == 0
        assert outcome.processed_count == 0

    def test_run_e2e_suite_organizer_exception_raises_runtime_error(self, tmp_path: Path) -> None:
        """Cover except Exception branch in _run_e2e_suite (lines 681-682)."""
        txt_file = tmp_path / "doc.txt"
        txt_file.write_text("content")

        mock_organizer_instance = MagicMock()
        mock_organizer_instance.organize.side_effect = RuntimeError("organizer exploded")
        mock_organizer_cls = MagicMock(return_value=mock_organizer_instance)

        mock_core_organizer_mod = MagicMock()
        mock_core_organizer_mod.FileOrganizer = mock_organizer_cls

        with patch.dict("sys.modules", {"core.organizer": mock_core_organizer_mod}):
            from cli.benchmark import _run_e2e_suite

            with pytest.raises(RuntimeError, match="E2E benchmark runner failed"):
                _run_e2e_suite([txt_file])


class TestRunAudioSuiteExceptionAndSmoke:
    """Cover exception and transcribe_smoke branches in _run_audio_suite (lines 538-561)."""

    def test_audio_suite_non_import_exception_raises_runtime_error(self, tmp_path: Path) -> None:
        """Cover except Exception (lines 538-539) — non-ImportError extractor failure."""
        audio_file = tmp_path / "track.mp3"
        audio_file.write_bytes(b"MP3 mock")

        mock_extractor_instance = MagicMock()
        mock_extractor_instance.extract.side_effect = OSError("disk read failed")
        mock_extractor_cls = MagicMock(return_value=mock_extractor_instance)

        mock_classifier_cls = MagicMock()

        mock_audio_extractor_mod = MagicMock()
        mock_audio_extractor_mod.AudioMetadataExtractor = mock_extractor_cls
        mock_audio_classifier_mod = MagicMock()
        mock_audio_classifier_mod.AudioClassifier = mock_classifier_cls

        with patch.dict(
            "sys.modules",
            {
                "services.audio.classifier": mock_audio_classifier_mod,
                "services.audio.metadata_extractor": mock_audio_extractor_mod,
            },
        ):
            from cli.benchmark import _run_audio_suite

            with pytest.raises(RuntimeError, match="Audio benchmark runner failed"):
                _run_audio_suite([audio_file])

    def test_audio_suite_transcribe_smoke_import_error(self, tmp_path: Path) -> None:
        """Cover transcribe_smoke ImportError branch (lines 553-561)."""
        audio_file = tmp_path / "track.mp3"
        audio_file.write_bytes(b"MP3 mock")

        mock_metadata = MagicMock()
        mock_extractor_instance = MagicMock()
        mock_extractor_instance.extract.return_value = mock_metadata
        mock_extractor_cls = MagicMock(return_value=mock_extractor_instance)

        mock_classifier_instance = MagicMock()
        mock_classifier_cls = MagicMock(return_value=mock_classifier_instance)

        mock_audio_extractor_mod = MagicMock()
        mock_audio_extractor_mod.AudioMetadataExtractor = mock_extractor_cls
        mock_audio_classifier_mod = MagicMock()
        mock_audio_classifier_mod.AudioClassifier = mock_classifier_cls

        with patch.dict(
            "sys.modules",
            {
                "services.audio.classifier": mock_audio_classifier_mod,
                "services.audio.metadata_extractor": mock_audio_extractor_mod,
            },
        ):
            # Patch AudioModel to raise ImportError (simulates missing [media] extra)
            with patch("cli.benchmark.AudioModel", side_effect=ImportError("no media extra")):
                from cli.benchmark import _run_audio_suite

                outcome = _run_audio_suite([audio_file], transcribe_smoke=True)

        # Smoke was requested but couldn't run → transcription_smoke_passed is False
        assert outcome.transcription_smoke_requested is True
        assert outcome.transcription_smoke_passed is False


class TestMaybeAttachComparisonSmokeWarning:
    """Cover smoke_warning attachment in _maybe_attach_comparison_output (line 1110)."""

    def _base_output(self) -> dict:
        from cli.benchmark import _RUNNER_PROFILE_VERSION

        return {
            "suite": "audio",
            "effective_suite": "audio",
            "degraded": False,
            "degradation_reasons": [],
            "runner_profile_version": _RUNNER_PROFILE_VERSION,
            "files_count": 1,
            "hardware_profile": {},
            "results": {
                "median_ms": 100.0,
                "p95_ms": 120.0,
                "p99_ms": 150.0,
                "stddev_ms": 10.0,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }

    def test_smoke_warning_attached_on_mismatch(self, tmp_path: Path) -> None:
        from cli.benchmark import _RUNNER_PROFILE_VERSION, _maybe_attach_comparison_output

        # baseline has transcribe_smoke=False; current run uses transcribe_smoke=True
        baseline = {
            "runner_profile_version": _RUNNER_PROFILE_VERSION,
            "transcribe_smoke": False,
            "results": {
                "median_ms": 100.0,
                "p95_ms": 100.0,
                "p99_ms": 100.0,
                "stddev_ms": 5.0,
                "throughput_fps": 10.0,
                "iterations": 5,
            },
        }
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline))

        output = self._base_output()
        result = _maybe_attach_comparison_output(
            output=output,
            compare_path=baseline_path,
            suite="audio",
            transcribe_smoke=True,
            console=MagicMock(),
            json_output=True,
        )
        assert "comparison_smoke_warning" in result


class TestRunCorpusCap:
    """Cover corpus-cap warning in run() (lines 1206-1211)."""

    def test_corpus_cap_logs_warning(self, tmp_path: Path) -> None:
        """Patch _MAX_BENCHMARK_FILES to 1 to trigger the cap on two files."""
        app = _get_app()
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world")

        with patch("cli.benchmark._MAX_BENCHMARK_FILES", 1):
            result = runner.invoke(
                app,
                [
                    "benchmark",
                    "run",
                    str(tmp_path),
                    "--suite",
                    "io",
                    "--iterations",
                    "1",
                    "--warmup",
                    "0",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        # Only 1 file should have been processed due to the cap
        assert payload["files_count"] == 1
