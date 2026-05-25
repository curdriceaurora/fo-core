"""Integration tests for organize/preview command options.

Covers: _resolve_parallel_settings (conflict detection, sequential, no_prefetch),
--max-workers, --sequential, --no-vision/--text-only, --prefetch-depth, preview
command with the same flag combinations.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from cli.organize import _resolve_parallel_settings

pytestmark = [pytest.mark.integration]


class TestResolveParallelSettings:
    """Unit-level tests for the _resolve_parallel_settings helper."""

    def test_sequential_and_max_workers_conflict_raises(self) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            _resolve_parallel_settings(sequential=True, max_workers=4, prefetch_depth=2)
        assert exc_info.value.exit_code == 2

    def test_sequential_with_no_max_workers_is_ok(self) -> None:
        workers, depth = _resolve_parallel_settings(
            sequential=True, max_workers=None, prefetch_depth=2
        )
        assert workers == 1
        assert depth == 0

    def test_sequential_with_max_workers_1_is_ok(self) -> None:
        workers, depth = _resolve_parallel_settings(
            sequential=True, max_workers=1, prefetch_depth=2
        )
        assert workers == 1
        assert depth == 0

    def test_max_workers_auto_when_not_sequential(self) -> None:
        """Auto-resolve always returns an int — never None (#408)."""
        workers, depth = _resolve_parallel_settings(
            sequential=False, max_workers=None, prefetch_depth=2
        )
        assert isinstance(workers, int)
        assert workers >= 1
        # Hard ceiling regardless of provider / OLLAMA_NUM_PARALLEL
        assert workers <= 4
        assert depth == 2

    def test_max_workers_auto_ollama_default_caps_at_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With Ollama (default provider) and OLLAMA_NUM_PARALLEL unset → 1 worker (#408)."""
        from unittest.mock import patch

        # Confirm OLLAMA_NUM_PARALLEL is actually unset for this test's
        # intent — patch.dict({}, clear=False) doesn't remove the var.
        monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)
        with (
            patch("cli.organize.os.cpu_count", return_value=32),
            patch("cli.organize._ollama_num_parallel", return_value=1),
            patch("cli.organize._get_current_provider_lazy", return_value="ollama"),
        ):
            workers, _ = _resolve_parallel_settings(
                sequential=False, max_workers=None, prefetch_depth=2
            )
        # min(4, 32//2, OLLAMA_NUM_PARALLEL=1) = 1
        assert workers == 1

    def test_max_workers_auto_respects_ollama_num_parallel(self) -> None:
        """If user has OLLAMA_NUM_PARALLEL=4, auto-default scales to it (#408)."""
        from unittest.mock import patch

        with (
            patch("cli.organize.os.cpu_count", return_value=32),
            patch("cli.organize._ollama_num_parallel", return_value=4),
            patch("cli.organize._get_current_provider_lazy", return_value="ollama"),
        ):
            workers, _ = _resolve_parallel_settings(
                sequential=False, max_workers=None, prefetch_depth=2
            )
        # min(4 ceiling, 32//2=16, OLLAMA_NUM_PARALLEL=4) = 4
        assert workers == 4

    def test_max_workers_auto_ollama_num_parallel_above_ceiling(self) -> None:
        """OLLAMA_NUM_PARALLEL=12 still capped at the worker ceiling (4)."""
        from unittest.mock import patch

        with (
            patch("cli.organize.os.cpu_count", return_value=32),
            patch("cli.organize._ollama_num_parallel", return_value=12),
            patch("cli.organize._get_current_provider_lazy", return_value="ollama"),
        ):
            workers, _ = _resolve_parallel_settings(
                sequential=False, max_workers=None, prefetch_depth=2
            )
        assert workers == 4

    def test_max_workers_auto_remote_provider_ignores_ollama_env(self) -> None:
        """openai / claude don't honour OLLAMA_NUM_PARALLEL (server-side concurrency)."""
        from unittest.mock import patch

        with (
            patch("cli.organize.os.cpu_count", return_value=16),
            patch("cli.organize._ollama_num_parallel", return_value=1),
            patch("cli.organize._get_current_provider_lazy", return_value="openai"),
        ):
            workers, _ = _resolve_parallel_settings(
                sequential=False, max_workers=None, prefetch_depth=2
            )
        # min(4, 16//2) = 4 — Ollama cap not applied for remote providers
        assert workers == 4

    def test_max_workers_auto_llama_cpp_ignores_ollama_env(self) -> None:
        """llama_cpp uses its own model instances — OLLAMA_NUM_PARALLEL doesn't apply.

        Codex P2 catch on PR #423: capping llama_cpp / mlx by the Ollama
        env var silently disables the new auto-default for those
        providers when users haven't configured Ollama at all.
        """
        from unittest.mock import patch

        with (
            patch("cli.organize.os.cpu_count", return_value=8),
            patch("cli.organize._ollama_num_parallel", return_value=1),
            patch("cli.organize._get_current_provider_lazy", return_value="llama_cpp"),
        ):
            workers, _ = _resolve_parallel_settings(
                sequential=False, max_workers=None, prefetch_depth=2
            )
        # min(4, 8//2) = 4 — Ollama cap NOT applied for llama_cpp
        assert workers == 4

    def test_max_workers_auto_mlx_ignores_ollama_env(self) -> None:
        """mlx is Apple-Silicon-only in-process inference; OLLAMA_NUM_PARALLEL irrelevant."""
        from unittest.mock import patch

        with (
            patch("cli.organize.os.cpu_count", return_value=8),
            patch("cli.organize._ollama_num_parallel", return_value=1),
            patch("cli.organize._get_current_provider_lazy", return_value="mlx"),
        ):
            workers, _ = _resolve_parallel_settings(
                sequential=False, max_workers=None, prefetch_depth=2
            )
        assert workers == 4

    def test_max_workers_auto_respects_low_core_count(self) -> None:
        """On a single-core machine the auto-default still produces a sane 1."""
        from unittest.mock import patch

        with (
            patch("cli.organize.os.cpu_count", return_value=1),
            patch("cli.organize._ollama_num_parallel", return_value=8),
        ):
            workers, _ = _resolve_parallel_settings(
                sequential=False, max_workers=None, prefetch_depth=2
            )
        # min(4, max(1, 1 // 2), 8) → cpu cap wins at 1
        assert workers == 1

    def test_max_workers_auto_handles_cpu_count_none(self) -> None:
        """os.cpu_count() returning None (rare but possible) → fallback to 1."""
        from unittest.mock import patch

        with patch("cli.organize.os.cpu_count", return_value=None):
            workers, _ = _resolve_parallel_settings(
                sequential=False, max_workers=None, prefetch_depth=2
            )
        assert workers == 1

    def test_ollama_num_parallel_invalid_value_defaults_to_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-int OLLAMA_NUM_PARALLEL silently degrades to 1."""
        from cli.organize import _ollama_num_parallel

        monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "garbage")
        assert _ollama_num_parallel() == 1

    def test_ollama_num_parallel_negative_value_clamps_to_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative OLLAMA_NUM_PARALLEL also clamps to 1 (defensive)."""
        from cli.organize import _ollama_num_parallel

        monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "-5")
        assert _ollama_num_parallel() == 1

    def test_ollama_num_parallel_reads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A valid OLLAMA_NUM_PARALLEL value is returned verbatim."""
        from cli.organize import _ollama_num_parallel

        monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "8")
        assert _ollama_num_parallel() == 8

    def test_max_workers_explicit(self) -> None:
        workers, depth = _resolve_parallel_settings(
            sequential=False, max_workers=3, prefetch_depth=2
        )
        assert workers == 3
        assert depth == 2

    def test_no_prefetch_alias_zeroes_depth(self) -> None:
        workers, depth = _resolve_parallel_settings(
            sequential=False, max_workers=2, prefetch_depth=4, no_prefetch=True
        )
        assert workers == 2
        assert depth == 0

    def test_prefetch_depth_zero_respected(self) -> None:
        workers, depth = _resolve_parallel_settings(
            sequential=False, max_workers=None, prefetch_depth=0
        )
        assert depth == 0

    def test_sequential_overrides_prefetch_depth(self) -> None:
        """sequential=True must set depth=0 regardless of prefetch_depth value."""
        workers, depth = _resolve_parallel_settings(
            sequential=True, max_workers=1, prefetch_depth=5
        )
        assert depth == 0


class TestOrganizeCLIOptions:
    """Integration tests for the organize command option parsing and routing."""

    def test_organize_sequential_flag(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "organize",
                str(integration_source_dir),
                str(integration_output_dir),
                "--dry-run",
                "--sequential",
            ],
        )
        assert result.exit_code == 0

    def test_organize_max_workers(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "organize",
                str(integration_source_dir),
                str(integration_output_dir),
                "--dry-run",
                "--max-workers",
                "2",
            ],
        )
        assert result.exit_code == 0

    def test_organize_no_vision_flag(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "organize",
                str(integration_source_dir),
                str(integration_output_dir),
                "--dry-run",
                "--no-vision",
            ],
        )
        assert result.exit_code == 0

    def test_organize_text_only_alias(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        """--text-only is an alias for --no-vision."""
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "organize",
                str(integration_source_dir),
                str(integration_output_dir),
                "--dry-run",
                "--text-only",
            ],
        )
        assert result.exit_code == 0

    def test_organize_no_prefetch_flag(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "organize",
                str(integration_source_dir),
                str(integration_output_dir),
                "--dry-run",
                "--no-prefetch",
            ],
        )
        assert result.exit_code == 0

    def test_organize_prefetch_depth_zero(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "organize",
                str(integration_source_dir),
                str(integration_output_dir),
                "--dry-run",
                "--prefetch-depth",
                "0",
            ],
        )
        assert result.exit_code == 0

    def test_organize_sequential_max_workers_conflict_exits_2(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        """--sequential combined with --max-workers > 1 must exit code 2."""
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "organize",
                str(integration_source_dir),
                str(integration_output_dir),
                "--sequential",
                "--max-workers",
                "4",
            ],
        )
        assert result.exit_code == 2

    def test_organize_exception_exits_1(
        self,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        """A FileOrganizer exception propagates as exit code 1."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from cli.main import app

        with patch(
            "core.organizer.FileOrganizer.organize",
            side_effect=RuntimeError("simulated organizer failure"),
        ):
            result = CliRunner().invoke(
                app,
                [
                    "organize",
                    str(integration_source_dir),
                    str(integration_output_dir),
                ],
            )
        assert result.exit_code == 1
        assert "error" in result.output.lower()


class TestPreviewCLIOptions:
    def test_preview_sequential_flag(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
    ) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "preview",
                str(integration_source_dir),
                "--sequential",
            ],
        )
        assert result.exit_code == 0

    def test_preview_max_workers(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
    ) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "preview",
                str(integration_source_dir),
                "--max-workers",
                "3",
            ],
        )
        assert result.exit_code == 0

    def test_preview_no_vision(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
    ) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "preview",
                str(integration_source_dir),
                "--no-vision",
            ],
        )
        assert result.exit_code == 0

    def test_preview_sequential_max_workers_conflict(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
    ) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        result = CliRunner().invoke(
            app,
            [
                "preview",
                str(integration_source_dir),
                "--sequential",
                "--max-workers",
                "2",
            ],
        )
        assert result.exit_code == 2

    def test_preview_exception_exits_1(
        self,
        integration_source_dir: Path,
    ) -> None:
        from unittest.mock import patch

        from typer.testing import CliRunner

        from cli.main import app

        with patch(
            "core.organizer.FileOrganizer.organize",
            side_effect=RuntimeError("simulated failure"),
        ):
            result = CliRunner().invoke(app, ["preview", str(integration_source_dir)])
        assert result.exit_code == 1
        assert "error" in result.output.lower()
