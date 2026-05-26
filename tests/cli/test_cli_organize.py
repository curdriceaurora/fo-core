"""Tests for the organize CLI commands (organize.py).

Tests the ``organize`` and ``preview`` top-level commands with mocked
FileOrganizer service.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from cli.main import app

pytestmark = [pytest.mark.unit, pytest.mark.ci]

runner = CliRunner()

# Auto-mock the setup gate so tests don't require a completed setup wizard.
_SETUP_PATCH = "cli.organize._check_setup_completed"


def _fake_check_setup() -> None:
    """Mimic ``_check_setup_completed`` when setup is not done."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    console.print()
    console.print(
        Panel.fit(
            "[bold yellow]First-time setup required[/bold yellow]\n\n"
            "File Organizer needs to be configured before use.\n"
            "Run the setup wizard to get started:\n\n"
            "  [bold cyan]fo setup[/bold cyan]\n\n"
            "This will detect your system capabilities and configure\n"
            "the optimal AI models for your hardware.",
            border_style="yellow",
        )
    )
    console.print()
    raise typer.Exit(code=1)


@pytest.fixture(autouse=True)
def _bypass_setup_check():
    """Bypass the setup-completed gate for all tests in this module."""
    with patch(_SETUP_PATCH, return_value=True):
        yield


def _mock_result(
    total: int = 10,
    processed: int = 8,
    skipped: int = 1,
    failed: int = 1,
) -> MagicMock:
    """Create a mock organize result."""
    result = MagicMock()
    result.total_files = total
    result.processed_files = processed
    result.skipped_files = skipped
    result.failed_files = failed
    return result


# ---------------------------------------------------------------------------
# organize
# ---------------------------------------------------------------------------


class TestOrganize:
    """Tests for the ``organize`` command."""

    def test_organize_fails_when_setup_not_completed(self, tmp_path: Path) -> None:
        """When setup is not completed, organize should exit with code 1 and show guidance."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        with patch(_SETUP_PATCH, side_effect=_fake_check_setup):
            result = runner.invoke(app, ["organize", str(input_dir), str(output_dir)])

        assert result.exit_code == 1
        assert "First-time setup required" in result.output
        assert "fo setup" in result.output

    @patch("core.organizer.FileOrganizer")
    def test_organize_basic(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(app, ["organize", str(input_dir), str(output_dir)])
        assert result.exit_code == 0
        assert "8 processed" in result.output
        assert "1 skipped" in result.output
        mock_cls.assert_called_once_with(
            dry_run=False,
            parallel_workers=ANY,  # auto-default min(4, cpu_count()//2) per #408
            prefetch_depth=2,
            enable_vision=True,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_organize_timeout_per_file_flag_propagates(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """--timeout-per-file forwards to FileOrganizer(timeout_per_file=N) (#396)."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(
            app,
            [
                "organize",
                str(input_dir),
                str(output_dir),
                "--timeout-per-file",
                "90",
            ],
        )
        assert result.exit_code == 0
        # The flag value should land verbatim in the FileOrganizer kwarg
        assert mock_cls.call_args.kwargs["timeout_per_file"] == 90.0

    @patch("core.organizer.FileOrganizer")
    def test_organize_timeout_per_file_zero_rejected_by_typer(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """--timeout-per-file 0 is rejected at the Typer layer (min=1.0)."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "organize",
                str(input_dir),
                str(output_dir),
                "--timeout-per-file",
                "0",
            ],
        )
        # Typer's min=1.0 validator exits 2 (POSIX usage-error convention)
        assert result.exit_code == 2
        mock_cls.assert_not_called()

    @patch("core.organizer.FileOrganizer")
    @patch("config.manager.ConfigManager")
    def test_organize_timeout_omitted_reads_from_app_config(
        self, mock_config_cls: MagicMock, mock_org_cls: MagicMock, tmp_path: Path
    ) -> None:
        """When --timeout-per-file is omitted, AppConfig.processing.timeout_per_file wins (#396)."""
        from config.schema import AppConfig, ProcessingSettings

        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_manager = MagicMock()
        mock_manager.load.return_value = AppConfig(
            processing=ProcessingSettings(timeout_per_file=120.0)
        )
        mock_config_cls.return_value = mock_manager

        mock_org_cls.return_value.organize.return_value = _mock_result()

        result = runner.invoke(app, ["organize", str(input_dir), str(output_dir)])
        assert result.exit_code == 0
        assert mock_org_cls.call_args.kwargs["timeout_per_file"] == 120.0

    @patch("core.organizer.FileOrganizer")
    @patch("config.manager.ConfigManager")
    def test_organize_explicit_flag_overrides_app_config(
        self, mock_config_cls: MagicMock, mock_org_cls: MagicMock, tmp_path: Path
    ) -> None:
        """Explicit --timeout-per-file beats AppConfig.processing.timeout_per_file."""
        from config.schema import AppConfig, ProcessingSettings

        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_manager = MagicMock()
        mock_manager.load.return_value = AppConfig(
            processing=ProcessingSettings(timeout_per_file=120.0)
        )
        mock_config_cls.return_value = mock_manager
        mock_org_cls.return_value.organize.return_value = _mock_result()

        result = runner.invoke(
            app,
            [
                "organize",
                str(input_dir),
                str(output_dir),
                "--timeout-per-file",
                "45",
            ],
        )
        assert result.exit_code == 0
        # Explicit --timeout-per-file value reaches the organizer verbatim,
        # bypassing the config value (120.0) that ConfigManager would have
        # supplied.  (Config IS loaded — but for the unrelated #408 worker
        # auto-default, not for the timeout resolver.)
        assert mock_org_cls.call_args.kwargs["timeout_per_file"] == 45.0

    @patch("core.organizer.FileOrganizer")
    @patch("config.manager.ConfigManager", side_effect=RuntimeError("config broken"))
    def test_organize_config_load_failure_falls_back_to_dataclass_default(
        self, mock_config_cls: MagicMock, mock_org_cls: MagicMock, tmp_path: Path
    ) -> None:
        """If ConfigManager raises, the resolver degrades to ProcessingSettings()'s 300.0 default."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org_cls.return_value.organize.return_value = _mock_result()

        result = runner.invoke(app, ["organize", str(input_dir), str(output_dir)])
        assert result.exit_code == 0
        assert mock_org_cls.call_args.kwargs["timeout_per_file"] == 300.0

    @patch("core.organizer.FileOrganizer")
    def test_organize_dry_run(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(app, ["organize", str(input_dir), str(output_dir), "--dry-run"])
        assert result.exit_code == 0
        assert "dry run" in result.output.lower() or "Dry run" in result.output
        mock_cls.assert_called_once_with(
            dry_run=True,
            parallel_workers=ANY,  # auto-default min(4, cpu_count()//2) per #408
            prefetch_depth=2,
            enable_vision=True,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_organize_workers_alias_propagates(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        """--workers N is an alias for --max-workers N (#408)."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(
            app,
            ["organize", str(input_dir), str(output_dir), "--workers", "2"],
        )
        assert result.exit_code == 0
        assert mock_cls.call_args.kwargs["parallel_workers"] == 2

    @patch("core.organizer.FileOrganizer")
    def test_organize_no_workers_flag_uses_auto_default(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """Omitting --workers / --max-workers picks min(4, cpu_count() // 2) (#408)."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(app, ["organize", str(input_dir), str(output_dir)])
        assert result.exit_code == 0
        workers = mock_cls.call_args.kwargs["parallel_workers"]
        assert isinstance(workers, int)
        assert 1 <= workers <= 4  # auto-default ceiling

    @patch("core.organizer.FileOrganizer")
    def test_organize_parallel_controls(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        """CLI parallel controls should be wired into runtime config."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(
            app,
            [
                "organize",
                str(input_dir),
                str(output_dir),
                "--max-workers",
                "3",
                "--prefetch-depth",
                "1",
                "--no-vision",
            ],
        )
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=False,
            parallel_workers=3,
            prefetch_depth=1,
            enable_vision=False,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_organize_sequential_forces_single_worker(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """--sequential should force one worker and disable queue-ahead."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(
            app,
            ["organize", str(input_dir), str(output_dir), "--sequential"],
        )
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=False,
            parallel_workers=1,
            prefetch_depth=0,
            enable_vision=True,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_organize_rejects_incompatible_worker_flags(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """--sequential and --max-workers>1 should fail fast."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "organize",
                str(input_dir),
                str(output_dir),
                "--sequential",
                "--max-workers",
                "4",
            ],
        )
        assert result.exit_code == 2
        assert "--sequential cannot be combined with --max-workers > 1" in result.output
        mock_cls.assert_not_called()

    @patch("core.organizer.FileOrganizer")
    def test_organize_text_only_alias_for_no_vision(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """--text-only should route as --no-vision (enable_vision=False)."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(
            app,
            ["organize", str(input_dir), str(output_dir), "--text-only"],
        )
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=False,
            parallel_workers=ANY,  # auto-default min(4, cpu_count()//2) per #408
            prefetch_depth=2,
            enable_vision=False,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_organize_no_prefetch_flag_passes_through(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """--no-prefetch should be forwarded as no_prefetch=True."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(
            app,
            ["organize", str(input_dir), str(output_dir), "--no-prefetch"],
        )
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=False,
            parallel_workers=ANY,  # auto-default min(4, cpu_count()//2) per #408
            prefetch_depth=0,
            enable_vision=True,
            no_prefetch=True,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_organize_sequential_with_max_workers_one_is_valid(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """--sequential with --max-workers 1 should succeed."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(
            app,
            [
                "organize",
                str(input_dir),
                str(output_dir),
                "--sequential",
                "--max-workers",
                "1",
            ],
        )
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=False,
            parallel_workers=1,
            prefetch_depth=0,
            enable_vision=True,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_organize_prefetch_depth_zero_explicit(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """Explicit --prefetch-depth 0 should be forwarded unchanged."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result()

        result = runner.invoke(
            app,
            ["organize", str(input_dir), str(output_dir), "--prefetch-depth", "0"],
        )
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=False,
            parallel_workers=ANY,  # auto-default min(4, cpu_count()//2) per #408
            prefetch_depth=0,
            enable_vision=True,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch(
        "core.organizer.FileOrganizer",
        side_effect=RuntimeError("Ollama not running"),
    )
    def test_organize_error(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        result = runner.invoke(app, ["organize", str(input_dir), str(output_dir)])
        assert result.exit_code == 1
        assert "Ollama not running" in result.output

    @patch("core.organizer.FileOrganizer")
    def test_organize_show_skipped_flag_forwarded(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """--show-skipped is forwarded to organizer.organize via kwarg (#412)."""
        from collections import Counter

        from core.types import OrganizationResult

        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        # Real OrganizationResult so the JSON helper / CLI summary can read fields.
        mock_org.organize.return_value = OrganizationResult(
            total_files=15,
            processed_files=12,
            skipped_files=3,
            skipped_by_extension=Counter({".nib": 2, ".stl": 1}),
        )

        result = runner.invoke(
            app,
            ["organize", str(input_dir), str(output_dir), "--show-skipped"],
        )
        assert result.exit_code == 0
        # The kwarg was forwarded so the underlying summary renderer would
        # print the full breakdown.
        mock_org.organize.assert_called_once()
        kwargs = mock_org.organize.call_args.kwargs
        assert kwargs.get("show_skipped") is True

    @patch("core.organizer.FileOrganizer")
    def test_organize_json_output_includes_skipped_by_extension(
        self, mock_cls: MagicMock, tmp_path: Path
    ) -> None:
        """--json output includes skipped_by_extension as a dict (#412)."""
        import json
        from collections import Counter

        from core.types import OrganizationResult

        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = OrganizationResult(
            total_files=10,
            processed_files=7,
            skipped_files=3,
            skipped_by_extension=Counter({".nib": 2, ".stl": 1}),
        )

        result = runner.invoke(
            app,
            ["organize", str(input_dir), str(output_dir), "--json"],
        )
        assert result.exit_code == 0
        # Locate the JSON object in the output (skip any Rich-styled lines).
        json_start = result.output.find("{")
        json_end = result.output.rfind("}") + 1
        assert json_start != -1 and json_end > json_start
        payload = json.loads(result.output[json_start:json_end])
        assert payload["skipped_by_extension"] == {".nib": 2, ".stl": 1}
        assert payload["skipped_files"] == 3
        assert payload["processed_files"] == 7
        assert payload["total_files"] == 10


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


class TestPreview:
    """Tests for the ``preview`` command."""

    def test_preview_fails_when_setup_not_completed(self, tmp_path: Path) -> None:
        """When setup is not completed, preview should exit with code 1 and show guidance."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        with patch(_SETUP_PATCH, side_effect=_fake_check_setup):
            result = runner.invoke(app, ["preview", str(input_dir)])

        assert result.exit_code == 1
        assert "First-time setup required" in result.output
        assert "fo setup" in result.output

    @patch("core.organizer.FileOrganizer")
    def test_preview_basic(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result(total=15)

        result = runner.invoke(app, ["preview", str(tmp_path)])
        assert result.exit_code == 0
        assert "15" in result.output
        mock_cls.assert_called_once_with(
            dry_run=True,
            parallel_workers=ANY,  # auto-default min(4, cpu_count()//2) per #408
            prefetch_depth=2,
            enable_vision=True,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_preview_max_workers(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result(total=5)

        result = runner.invoke(app, ["preview", str(tmp_path), "--max-workers", "4"])
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=True,
            parallel_workers=4,
            prefetch_depth=2,
            enable_vision=True,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_preview_sequential(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result(total=5)

        result = runner.invoke(app, ["preview", str(tmp_path), "--sequential"])
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=True,
            parallel_workers=1,
            prefetch_depth=0,
            enable_vision=True,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_preview_no_vision(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result(total=5)

        result = runner.invoke(app, ["preview", str(tmp_path), "--no-vision"])
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=True,
            parallel_workers=ANY,  # auto-default min(4, cpu_count()//2) per #408
            prefetch_depth=2,
            enable_vision=False,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_preview_text_only_alias(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result(total=5)

        result = runner.invoke(app, ["preview", str(tmp_path), "--text-only"])
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=True,
            parallel_workers=ANY,  # auto-default min(4, cpu_count()//2) per #408
            prefetch_depth=2,
            enable_vision=False,
            no_prefetch=False,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    @patch("core.organizer.FileOrganizer")
    def test_preview_no_prefetch(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        mock_org = MagicMock()
        mock_cls.return_value = mock_org
        mock_org.organize.return_value = _mock_result(total=5)

        result = runner.invoke(app, ["preview", str(tmp_path), "--no-prefetch"])
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(
            dry_run=True,
            parallel_workers=ANY,  # auto-default min(4, cpu_count()//2) per #408
            prefetch_depth=0,
            enable_vision=True,
            no_prefetch=True,
            transcribe_audio=False,
            max_transcribe_seconds=600.0,
            timeout_per_file=300.0,
        )

    def test_preview_sequential_conflicts_with_max_workers(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["preview", str(tmp_path), "--sequential", "--max-workers", "4"]
        )
        assert result.exit_code == 2
        assert "--sequential" in result.output

    @patch(
        "core.organizer.FileOrganizer",
        side_effect=ValueError("Invalid directory"),
    )
    def test_preview_error(self, mock_cls: MagicMock, tmp_path: Path) -> None:
        result = runner.invoke(app, ["preview", str(tmp_path)])
        assert result.exit_code == 1

        assert "Invalid directory" in result.output
