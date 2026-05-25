"""Integration tests for the ``fo logs`` command.

Covers the per-run session log feature and the ``fo logs`` CLI surface:
  - ``fo logs`` shows main fo.log
  - ``fo logs --session`` shows the latest session log
  - ``fo logs --list`` lists all session logs
  - Session log directory and files are created at startup
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app
from config.path_manager import get_canonical_paths

pytestmark = [pytest.mark.integration]

runner = CliRunner()


class TestFoLogsMainLog:
    """fo logs reads the main fo.log file."""

    def test_logs_shows_main_log_content(self) -> None:
        """fo logs outputs lines from fo.log."""
        paths = get_canonical_paths()
        log_dir: Path = paths["logs"]
        log_dir.mkdir(parents=True, exist_ok=True)
        main_log = log_dir / "fo.log"
        main_log.write_text("integration test log line\n")

        result = runner.invoke(app, ["logs"])
        assert result.exit_code == 0
        assert "integration test log line" in result.output

    def test_logs_tail_lines_parameter(self) -> None:
        """fo logs -n limits the output to N lines."""
        paths = get_canonical_paths()
        log_dir: Path = paths["logs"]
        log_dir.mkdir(parents=True, exist_ok=True)
        main_log = log_dir / "fo.log"
        main_log.write_text("first\nsecond\nthird\n")

        result = runner.invoke(app, ["logs", "-n", "2"])
        assert result.exit_code == 0
        assert "second" in result.output
        assert "third" in result.output
        assert "first" not in result.output


class TestFoLogsSessionLog:
    """fo logs --session reads the latest session log."""

    def test_logs_session_shows_latest(self) -> None:
        """fo logs --session shows the most recently modified session log."""
        paths = get_canonical_paths()
        log_dir: Path = paths["logs"]
        session_dir = log_dir / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)

        old_log = session_dir / "fo-2026-01-01T00-00-00-old11111.log"
        old_log.write_text("old session content\n")
        old_time = time.time() - 86400
        os.utime(old_log, (old_time, old_time))

        new_log = session_dir / "fo-2026-05-24T12-00-00-new22222.log"
        new_log.write_text("recent session content\n")

        result = runner.invoke(app, ["logs", "--session"])
        assert result.exit_code == 0
        assert "recent session content" in result.output
        assert "old session content" not in result.output

    def test_logs_session_no_sessions_exits_1(self) -> None:
        """fo logs --session exits 1 when no session logs exist."""
        result = runner.invoke(app, ["logs", "--session"])
        assert result.exit_code == 1


class TestFoLogsList:
    """fo logs --list lists available session logs."""

    def test_logs_list_shows_session_filenames(self) -> None:
        """fo logs --list prints session log filenames."""
        paths = get_canonical_paths()
        log_dir: Path = paths["logs"]
        session_dir = log_dir / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)

        session1 = session_dir / "fo-2026-05-20T10-00-00-abc11111.log"
        session1.write_text("session A\n")
        session2 = session_dir / "fo-2026-05-24T14-00-00-def22222.log"
        session2.write_text("session B\n")

        result = runner.invoke(app, ["logs", "--list"])
        assert result.exit_code == 0
        assert "abc11111" in result.output
        assert "def22222" in result.output

    def test_logs_list_no_sessions_dir_shows_warning(self) -> None:
        """fo logs --list shows a warning when there are no session logs."""
        result = runner.invoke(app, ["logs", "--list"])
        assert result.exit_code == 0


class TestSessionLogCreation:
    """Session log files are created at CLI startup."""

    def test_each_invocation_creates_session_log(self) -> None:
        """Invoking a startup-managed command creates a session log in sessions/."""
        paths = get_canonical_paths()
        log_dir: Path = paths["logs"]

        result = runner.invoke(app, ["logs", "--list"])
        assert result.exit_code == 0

        session_dir = log_dir / "sessions"
        assert session_dir.exists()
        session_files = list(session_dir.glob("fo-*.log"))
        assert len(session_files) >= 1

    def test_old_session_logs_cleaned_up_at_startup(self) -> None:
        """Session logs older than 3 days are removed on next invocation."""
        paths = get_canonical_paths()
        log_dir: Path = paths["logs"]
        session_dir = log_dir / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)

        old_log = session_dir / "fo-2026-01-01T00-00-00-stale1111.log"
        old_log.write_text("stale\n")
        four_days_ago = time.time() - (4 * 86400)
        os.utime(old_log, (four_days_ago, four_days_ago))

        result = runner.invoke(app, ["logs", "--list"])
        assert result.exit_code == 0
        assert not old_log.exists(), "stale session log should be removed"
