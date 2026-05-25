"""Tests for main Typer CLI app."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cli.main import _fo_version, app

runner = CliRunner()


@pytest.mark.ci
def test_fo_version_returns_version_string() -> None:
    """_fo_version() returns the installed version string."""
    with patch("cli.main._pkg_version", return_value="9.9.9"):
        assert _fo_version() == "9.9.9"


@pytest.mark.ci
def test_fo_version_fallback_on_package_not_found() -> None:
    """_fo_version() returns 'unknown' when the package isn't installed."""
    with patch("cli.main._pkg_version", side_effect=PackageNotFoundError):
        assert _fo_version() == "unknown"


@pytest.mark.ci
def test_version_command():
    """Test the version command output."""
    with patch("cli.main._fo_version", return_value="1.2.3"):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "fo 1.2.3" in result.stdout


@pytest.mark.ci
def test_version_command_skips_startup_bookkeeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """`fo version` should print metadata without log sinks or recovery sweeps."""
    log_sink_calls: list[object] = []
    sweep_calls: list[object] = []

    monkeypatch.setattr("loguru.logger.add", lambda sink, **_kwargs: log_sink_calls.append(sink))
    monkeypatch.setattr("cli.main._durable_move_sweep", lambda journal: sweep_calls.append(journal))

    with patch("cli.main._fo_version", return_value="1.2.3"):
        result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "fo 1.2.3" in result.stdout
    assert log_sink_calls == []
    assert sweep_calls == []


@pytest.mark.ci
def test_version_flag():
    """Test the eager --version flag output."""
    with patch("cli.main._fo_version", return_value="1.2.3"):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "fo 1.2.3" in result.stdout


@pytest.mark.uses_setup_gate
@patch("config.manager.ConfigManager")
def test_organize_requires_setup_completed(mock_cm):
    """organize exits with code 1 when setup is incomplete."""
    mock_cm.return_value.load.return_value.setup_completed = False
    result = runner.invoke(app, ["organize", "in", "out"])
    assert result.exit_code == 1
    assert "setup" in result.stdout.lower()


@pytest.mark.uses_setup_gate
@patch("config.manager.ConfigManager")
def test_preview_requires_setup_completed(mock_cm):
    """preview exits with code 1 when setup is incomplete."""
    mock_cm.return_value.load.return_value.setup_completed = False
    result = runner.invoke(app, ["preview", "in_dir"])
    assert result.exit_code == 1
    assert "setup" in result.stdout.lower()


@patch("cli.organize._check_setup_completed", return_value=True)
@patch("core.organizer.FileOrganizer")
def test_organize_command_live(mock_organizer_cls, _mock_setup, tmp_path):
    """Test organize command executes FileOrganizer correctly."""
    mock_instance = MagicMock()
    mock_result = MagicMock(processed_files=5, skipped_files=1, failed_files=0)
    mock_instance.organize.return_value = mock_result
    mock_organizer_cls.return_value = mock_instance

    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()

    result = runner.invoke(app, ["organize", str(in_dir), str(out_dir)])

    assert result.exit_code == 0
    assert "Organizing" in result.stdout
    mock_organizer_cls.assert_called_once_with(
        dry_run=False,
        parallel_workers=None,
        prefetch_depth=2,
        enable_vision=True,
        no_prefetch=False,
        transcribe_audio=False,
        max_transcribe_seconds=600.0,
    )
    # show_skipped kwarg is always forwarded (defaults to False) since #412.
    mock_instance.organize.assert_called_once_with(in_dir, out_dir, show_skipped=False)


@patch("cli.organize._check_setup_completed", return_value=True)
@patch("core.organizer.FileOrganizer")
def test_organize_command_dry_run(mock_organizer_cls, _mock_setup, tmp_path):
    """Test organize command processes dry-run flag."""
    mock_instance = MagicMock()
    mock_result = MagicMock(processed_files=3, skipped_files=0, failed_files=0)
    mock_instance.organize.return_value = mock_result
    mock_organizer_cls.return_value = mock_instance

    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    # A.cli: input_dir must exist; output_dir may not (organizer creates it).
    in_dir.mkdir()

    result = runner.invoke(app, ["organize", str(in_dir), str(out_dir), "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run mode" in result.stdout
    mock_organizer_cls.assert_called_once_with(
        dry_run=True,
        parallel_workers=None,
        prefetch_depth=2,
        enable_vision=True,
        no_prefetch=False,
        transcribe_audio=False,
        max_transcribe_seconds=600.0,
    )
    # A.cli resolves the path args before dispatching; the service sees
    # the canonical absolute form. show_skipped=False default since #412.
    mock_instance.organize.assert_called_once_with(
        in_dir.resolve(), out_dir.resolve(), show_skipped=False
    )


@patch("cli.organize._check_setup_completed", return_value=True)
@patch("core.organizer.FileOrganizer")
def test_organize_command_error(mock_organizer_cls, _mock_setup, tmp_path):
    """Test organize command handles exceptions gracefully."""
    mock_instance = MagicMock()
    mock_instance.organize.side_effect = RuntimeError("Something broke")
    mock_organizer_cls.return_value = mock_instance

    # A.cli: real directories required so the service-layer error path
    # (not CLI-arg-validation path) is exercised.
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["organize", str(in_dir), str(out_dir)])

    assert result.exit_code == 1
    assert "Error: Something broke" in result.stdout


@patch("cli.organize._check_setup_completed", return_value=True)
@patch("core.organizer.FileOrganizer")
def test_preview_command(mock_organizer_cls, _mock_setup, tmp_path):
    """Test preview command runs organizer in dry_run mode."""
    mock_instance = MagicMock()
    mock_result = MagicMock(total_files=10)
    mock_instance.organize.return_value = mock_result
    mock_organizer_cls.return_value = mock_instance

    in_dir = tmp_path / "in_dir"
    in_dir.mkdir()
    result = runner.invoke(app, ["preview", str(in_dir)])

    assert result.exit_code == 0
    assert "Previewing" in result.stdout
    mock_organizer_cls.assert_called_once_with(
        dry_run=True,
        parallel_workers=None,
        prefetch_depth=2,
        enable_vision=True,
        no_prefetch=False,
        transcribe_audio=False,
        max_transcribe_seconds=600.0,
    )
    resolved = in_dir.resolve()
    mock_instance.organize.assert_called_once_with(resolved, resolved)


@patch("cli.organize._check_setup_completed", return_value=True)
@patch("core.organizer.FileOrganizer")
def test_preview_command_error(mock_organizer_cls, _mock_setup, tmp_path):
    """Test preview command handles exceptions."""
    mock_instance = MagicMock()
    mock_instance.organize.side_effect = ValueError("Bad input")
    mock_organizer_cls.return_value = mock_instance

    in_dir = tmp_path / "in_dir"
    in_dir.mkdir()
    result = runner.invoke(app, ["preview", str(in_dir)])

    assert result.exit_code == 1
    assert "Error: Bad input" in result.stdout
