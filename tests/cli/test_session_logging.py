"""Tests for per-run session logging feature.

Validates that:
1. Each CLI invocation generates a unique session ID
2. Session log files are created in the sessions subdirectory
3. Session logs always capture DEBUG level output
4. Session ID is injected into all log records
5. Old session logs are cleaned up (3-day retention)
6. The `fo logs` command works correctly
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from cli.main import app
from cli.state import CLIState


@pytest.mark.unit
@pytest.mark.ci
def test_cli_state_has_session_fields() -> None:
    """CLIState exposes session_id and session_log_sink_id fields."""
    state = CLIState(session_id="test-session-123", session_log_sink_id=42)
    assert state.session_id == "test-session-123"
    assert state.session_log_sink_id == 42


@pytest.mark.unit
@pytest.mark.ci
def test_cli_state_session_fields_default_none() -> None:
    """Default CLIState has session fields set to None."""
    state = CLIState()
    assert state.session_id is None
    assert state.session_log_sink_id is None


@pytest.mark.unit
@pytest.mark.ci
def test_session_id_generated_on_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each CLI invocation generates a unique session ID with timestamp and UUID."""
    log_dir = tmp_path / "logs"

    def _fake_paths() -> dict[str, Path]:
        return {"logs": log_dir}

    monkeypatch.setattr("config.path_manager.get_canonical_paths", _fake_paths)
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    result = runner.invoke(app, ["logs", "--list"])
    assert result.exit_code == 0

    # Session log directory should be created
    session_dir = log_dir / "sessions"
    assert session_dir.exists(), "sessions directory should be created"

    # Session log file should exist with timestamp-uuid format
    session_files = list(session_dir.glob("fo-*.log"))
    assert len(session_files) == 1, "exactly one session log should be created"

    session_file = session_files[0]
    # Filename format: fo-2026-05-23T12-34-56-abc12345.log
    assert session_file.stem.startswith("fo-2")
    assert len(session_file.stem) > len("fo-2026-05-23T12-34-56-")  # Has UUID suffix


@pytest.mark.unit
@pytest.mark.ci
def test_session_log_always_debug_level(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Session log is always DEBUG level, even without --debug flag."""
    captured: list[dict[str, Any]] = []
    original_add = __import__("loguru").logger.add

    def _spy_add(_sink: Any, **kwargs: Any) -> int:
        captured.append({"sink": str(_sink), **kwargs})
        return original_add(_sink, **kwargs)

    log_dir = tmp_path / "logs"
    monkeypatch.setattr("config.path_manager.get_canonical_paths", lambda: {"logs": log_dir})
    monkeypatch.setattr("loguru.logger.add", _spy_add)
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    # Run WITHOUT --debug flag
    result = runner.invoke(app, ["logs", "--list"])
    assert result.exit_code == 0

    # Find session log sink (in sessions/ subdirectory)
    session_sinks = [c for c in captured if "sessions" in c.get("sink", "")]
    assert session_sinks, "session log sink should be added"
    assert session_sinks[0].get("level") == "DEBUG", "session log is always DEBUG"


@pytest.mark.unit
@pytest.mark.ci
def test_session_log_uses_ndjson_format(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Session log uses serialize=True for NDJSON format."""
    captured: list[dict[str, Any]] = []
    original_add = __import__("loguru").logger.add

    def _spy_add(_sink: Any, **kwargs: Any) -> int:
        captured.append({"sink": str(_sink), **kwargs})
        return original_add(_sink, **kwargs)

    log_dir = tmp_path / "logs"
    monkeypatch.setattr("config.path_manager.get_canonical_paths", lambda: {"logs": log_dir})
    monkeypatch.setattr("loguru.logger.add", _spy_add)
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    result = runner.invoke(app, ["logs", "--list"])
    assert result.exit_code == 0

    session_sinks = [c for c in captured if "sessions" in c.get("sink", "")]
    assert session_sinks
    assert session_sinks[0].get("serialize") is True, "session log uses NDJSON format"


@pytest.mark.unit
@pytest.mark.ci
def test_session_id_injected_in_log_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Session ID is injected into every log record via custom filter."""
    log_dir = tmp_path / "logs"
    monkeypatch.setattr("config.path_manager.get_canonical_paths", lambda: {"logs": log_dir})
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    result = runner.invoke(app, ["logs", "--list"])
    assert result.exit_code == 0

    # Find the session log file
    session_dir = log_dir / "sessions"
    session_files = list(session_dir.glob("fo-*.log"))
    assert session_files

    # The session_id should be in the filename
    session_file = session_files[0]
    filename_session_id = session_file.stem[3:]  # Strip "fo-" prefix

    # Read the log file and verify session_id is in the records
    if session_file.stat().st_size > 0:
        with session_file.open("r") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    # Session ID should be in extra field
                    assert "session_id" in record.get("extra", {}), "session_id should be in extra"
                    assert record["extra"]["session_id"] == filename_session_id


@pytest.mark.unit
@pytest.mark.ci
def test_old_session_logs_cleaned_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Session logs older than 3 days are deleted on CLI startup."""
    log_dir = tmp_path / "logs"
    session_dir = log_dir / "sessions"
    session_dir.mkdir(parents=True)

    # Create an old session log (4 days ago)
    old_log = session_dir / "fo-2026-01-01T00-00-00-old12345.log"
    old_log.write_text("old log\n")
    # Set mtime to 4 days ago
    four_days_ago = time.time() - (4 * 86400)
    old_log.touch()
    # Python's Path.touch() doesn't support setting mtime, use os.utime
    import os

    os.utime(old_log, (four_days_ago, four_days_ago))

    # Create a recent session log (1 day ago)
    recent_log = session_dir / "fo-2026-05-22T00-00-00-recent12.log"
    recent_log.write_text("recent log\n")

    monkeypatch.setattr("config.path_manager.get_canonical_paths", lambda: {"logs": log_dir})
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    result = runner.invoke(app, ["logs", "--list"])
    assert result.exit_code == 0

    # Old log should be deleted
    assert not old_log.exists(), "old session log should be cleaned up"
    # Recent log should still exist
    assert recent_log.exists(), "recent session log should be retained"


@pytest.mark.unit
@pytest.mark.ci
def test_unwritable_session_log_dir_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unwritable session log directory doesn't crash CLI."""

    def _raise_oserror_on_mkdir(*args: Any, **kwargs: Any) -> None:
        raise OSError("permission denied")

    # Mock Path.mkdir to raise OSError
    original_mkdir = Path.mkdir

    def _patched_mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
        if "sessions" in str(self):
            raise OSError("permission denied")
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _patched_mkdir)
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    result = runner.invoke(app, ["hardware-info"])
    # Should degrade gracefully and still exit 0
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# fo logs command tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
def test_logs_command_shows_main_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fo logs shows the main fo.log file by default."""
    from cli.logs import logs_command

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    main_log = log_dir / "fo.log"
    main_log.write_text("line 1\nline 2\nline 3\n")

    monkeypatch.setattr("cli.logs.get_canonical_paths", lambda: {"logs": log_dir})

    # Capture stdout
    import io

    captured_output = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured_output)

    logs_command(follow=False, lines=2, session=False, list_sessions=False)

    output = captured_output.getvalue()
    assert "line 2" in output
    assert "line 3" in output
    assert "line 1" not in output  # Only last 2 lines


@pytest.mark.unit
@pytest.mark.ci
def test_logs_command_shows_latest_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fo logs --session shows the most recent session log."""
    from cli.logs import logs_command

    log_dir = tmp_path / "logs"
    session_dir = log_dir / "sessions"
    session_dir.mkdir(parents=True)

    # Create two session logs with different mtimes
    old_session = session_dir / "fo-2026-05-20T10-00-00-old12345.log"
    old_session.write_text("old session\n")

    new_session = session_dir / "fo-2026-05-23T14-00-00-new12345.log"
    new_session.write_text("new session line 1\nnew session line 2\n")

    # Set mtime to ensure ordering
    import os

    old_time = time.time() - 86400  # 1 day ago
    os.utime(old_session, (old_time, old_time))

    monkeypatch.setattr("cli.logs.get_canonical_paths", lambda: {"logs": log_dir})

    # Capture stdout
    import io

    captured_output = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured_output)

    logs_command(follow=False, lines=50, session=True, list_sessions=False)

    output = captured_output.getvalue()
    assert "new session" in output
    assert "old session" not in output


@pytest.mark.unit
@pytest.mark.ci
def test_logs_command_lists_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fo logs --list shows all available session logs."""
    from cli.logs import logs_command

    log_dir = tmp_path / "logs"
    session_dir = log_dir / "sessions"
    session_dir.mkdir(parents=True)

    session1 = session_dir / "fo-2026-05-20T10-00-00-abc12345.log"
    session1.write_text("session 1\n")

    session2 = session_dir / "fo-2026-05-23T14-00-00-def67890.log"
    session2.write_text("session 2\n")

    monkeypatch.setattr("cli.logs.get_canonical_paths", lambda: {"logs": log_dir})

    # Capture stdout
    import io

    captured_output = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured_output)

    logs_command(follow=False, lines=50, session=False, list_sessions=True)

    output = captured_output.getvalue()
    assert "Session Logs" in output
    # Both sessions should be listed
    assert "abc12345" in output
    assert "def67890" in output


@pytest.mark.unit
@pytest.mark.ci
def test_logs_command_no_sessions_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fo logs --session exits with error when no session logs exist."""
    from cli.logs import logs_command

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # No sessions directory

    monkeypatch.setattr("cli.logs.get_canonical_paths", lambda: {"logs": log_dir})

    with pytest.raises(typer.Exit) as exc_info:
        logs_command(follow=False, lines=50, session=True, list_sessions=False)

    assert exc_info.value.exit_code == 1


@pytest.mark.unit
@pytest.mark.ci
def test_logs_command_main_log_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fo logs exits with error when main fo.log doesn't exist."""
    from cli.logs import logs_command

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # No fo.log file

    monkeypatch.setattr("cli.logs.get_canonical_paths", lambda: {"logs": log_dir})

    with pytest.raises(typer.Exit) as exc_info:
        logs_command(follow=False, lines=50, session=False, list_sessions=False)

    assert exc_info.value.exit_code == 1


@pytest.mark.unit
@pytest.mark.ci
def test_logs_command_follow_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fo logs --follow shows tail then follows the file."""
    from cli.logs import logs_command

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    main_log = log_dir / "fo.log"
    main_log.write_text("existing line\n")

    monkeypatch.setattr("cli.logs.get_canonical_paths", lambda: {"logs": log_dir})

    call_count = 0

    def _fake_sleep(_: float) -> None:
        nonlocal call_count
        call_count += 1
        raise KeyboardInterrupt

    monkeypatch.setattr("cli.logs.time.sleep", _fake_sleep)

    import io

    captured_output = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured_output)

    with pytest.raises(typer.Exit) as exc_info:
        logs_command(follow=True, lines=5, session=False, list_sessions=False)

    assert exc_info.value.exit_code == 130
    assert "existing line" in captured_output.getvalue()


@pytest.mark.unit
@pytest.mark.ci
def test_logs_command_exception_handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Generic exception in logs_command is caught and exits with code 1."""
    from cli.logs import logs_command

    def _raise_oserror() -> dict[str, Path]:
        raise OSError("disk error")

    monkeypatch.setattr("cli.logs.get_canonical_paths", _raise_oserror)

    with pytest.raises(typer.Exit) as exc_info:
        logs_command(follow=False, lines=50, session=False, list_sessions=False)

    assert exc_info.value.exit_code == 1


@pytest.mark.unit
@pytest.mark.ci
def test_list_session_logs_no_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_list_session_logs prints a warning when sessions/ dir doesn't exist."""
    from cli.logs import _list_session_logs

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # No sessions subdirectory

    messages: list[str] = []
    monkeypatch.setattr(
        "cli.logs.console", type("C", (), {"print": lambda self, m: messages.append(m)})()
    )

    _list_session_logs(log_dir)

    assert any("No session logs directory" in m for m in messages)


@pytest.mark.unit
@pytest.mark.ci
def test_list_session_logs_empty_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_list_session_logs prints a warning when sessions/ dir exists but is empty."""
    from cli.logs import _list_session_logs

    log_dir = tmp_path / "logs"
    session_dir = log_dir / "sessions"
    session_dir.mkdir(parents=True)
    # No log files inside

    messages: list[str] = []
    monkeypatch.setattr(
        "cli.logs.console", type("C", (), {"print": lambda self, m: messages.append(m)})()
    )

    _list_session_logs(log_dir)

    assert any("No session logs found" in m for m in messages)


@pytest.mark.unit
@pytest.mark.ci
def test_get_latest_session_log_all_excluded(tmp_path: Path) -> None:
    """_get_latest_session_log returns None when all files match the exclusion."""
    from cli.logs import _get_latest_session_log

    log_dir = tmp_path / "logs"
    session_dir = log_dir / "sessions"
    session_dir.mkdir(parents=True)

    session_file = session_dir / "fo-2026-05-24T10-00-00-abc12345.log"
    session_file.write_text("session\n")

    result = _get_latest_session_log(log_dir, exclude_session_id="2026-05-24T10-00-00-abc12345")
    assert result is None


@pytest.mark.unit
@pytest.mark.ci
def test_show_last_lines_file_not_found(tmp_path: Path) -> None:
    """_show_last_lines raises typer.Exit(1) when file doesn't exist."""
    from cli.logs import _show_last_lines

    missing = tmp_path / "nonexistent.log"

    with pytest.raises(typer.Exit) as exc_info:
        _show_last_lines(missing, 10)

    assert exc_info.value.exit_code == 1


@pytest.mark.unit
@pytest.mark.ci
def test_tail_follow_file_not_found(tmp_path: Path) -> None:
    """_tail_follow raises typer.Exit(1) when file doesn't exist."""
    from cli.logs import _tail_follow

    missing = tmp_path / "nonexistent.log"

    with pytest.raises(typer.Exit) as exc_info:
        _tail_follow(missing)

    assert exc_info.value.exit_code == 1


@pytest.mark.unit
@pytest.mark.ci
def test_tail_follow_shows_existing_lines(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_tail_follow outputs existing lines before entering follow mode."""
    from cli.logs import _tail_follow

    log_file = tmp_path / "test.log"
    log_file.write_text("line A\nline B\nline C\n")

    def _raise_keyboard_interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("cli.logs.time.sleep", _raise_keyboard_interrupt)

    import io

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)

    with pytest.raises(KeyboardInterrupt):
        _tail_follow(log_file, num_lines=2)

    output = captured.getvalue()
    assert "line B" in output
    assert "line C" in output
    assert "line A" not in output  # Only last 2 lines


# ---------------------------------------------------------------------------
# main.py _cleanup_old_session_logs coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
def test_cleanup_old_session_logs_no_dir(tmp_path: Path) -> None:
    """_cleanup_old_session_logs is a no-op when sessions/ dir doesn't exist."""
    from cli.main import _cleanup_old_session_logs

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # No sessions subdirectory

    # Should not raise
    _cleanup_old_session_logs(log_dir)


@pytest.mark.unit
@pytest.mark.ci
def test_cleanup_old_session_logs_oserror_on_unlink(tmp_path: Path) -> None:
    """_cleanup_old_session_logs silently ignores OSError on file unlink."""
    import os

    from cli.main import _cleanup_old_session_logs

    log_dir = tmp_path / "logs"
    session_dir = log_dir / "sessions"
    session_dir.mkdir(parents=True)

    old_log = session_dir / "fo-2026-01-01T00-00-00-old12345.log"
    old_log.write_text("old\n")
    four_days_ago = time.time() - (4 * 86400)
    os.utime(old_log, (four_days_ago, four_days_ago))

    original_unlink = Path.unlink

    def _raise_on_unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("permission denied")

    Path.unlink = _raise_on_unlink  # type: ignore[method-assign]
    try:
        _cleanup_old_session_logs(log_dir)
    finally:
        Path.unlink = original_unlink  # type: ignore[method-assign]

    # Should not raise; file still exists since unlink was mocked
    assert old_log.exists()


# ---------------------------------------------------------------------------
# fo logs Typer command (via app runner) coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
def test_logs_typer_command_via_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fo logs works when invoked via the Typer app (covers main.py logs command)."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    main_log = log_dir / "fo.log"
    main_log.write_text("app log line\n")

    monkeypatch.setattr("config.path_manager.get_canonical_paths", lambda: {"logs": log_dir})
    monkeypatch.setattr("cli.logs.get_canonical_paths", lambda: {"logs": log_dir})
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 0
    assert "app log line" in result.output
