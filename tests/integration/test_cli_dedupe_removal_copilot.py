"""Integration tests for cli/dedupe_removal.py and cli/copilot.py.

Coverage targets:
- dedupe_removal.py → ≥ 80%
- copilot.py        → ≥ 80%
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file_dict(path: Path, size: int = 1024) -> dict:
    return {"path": path, "size": size, "mtime": 1_700_000_000.0}


def _make_file_meta(path: Path, size: int = 1024) -> MagicMock:
    meta = MagicMock()
    meta.path = path
    meta.size = size
    meta.modified_time = datetime(2025, 1, 1, tzinfo=UTC)
    return meta


def _make_duplicate_group(files: list[MagicMock]) -> MagicMock:
    group = MagicMock()
    group.files = files
    return group


# ---------------------------------------------------------------------------
# Tests: cli/dedupe_removal.py — remove_files()
# ---------------------------------------------------------------------------


class TestRemoveFiles:
    """Tests for remove_files()."""

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        from rich.console import Console

        from file_organizer.cli.dedupe_removal import remove_files

        f = tmp_path / "duplicate.txt"
        f.write_text("data")

        files = [_make_file_dict(f, size=100)]
        console = Console(file=open("/dev/null", "w"), no_color=True)
        removed, saved = remove_files(files, [0], None, dry_run=True, console=console)

        assert removed == 1
        assert saved == 100
        assert f.exists(), "dry run must not delete the file"
        console.file.close()

    def test_actual_removal(self, tmp_path: Path) -> None:
        from rich.console import Console

        from file_organizer.cli.dedupe_removal import remove_files

        f = tmp_path / "dup.txt"
        f.write_text("content")

        files = [_make_file_dict(f, size=200)]
        console = Console(file=open("/dev/null", "w"), no_color=True)
        removed, saved = remove_files(files, [0], None, dry_run=False, console=console)

        assert removed == 1
        assert saved == 200
        assert not f.exists(), "file should have been deleted"
        console.file.close()

    def test_multiple_indices(self, tmp_path: Path) -> None:
        from rich.console import Console

        from file_organizer.cli.dedupe_removal import remove_files

        f1 = tmp_path / "dup1.txt"
        f2 = tmp_path / "dup2.txt"
        f1.write_text("a")
        f2.write_text("b")

        files = [_make_file_dict(f1, size=50), _make_file_dict(f2, size=75)]
        console = Console(file=open("/dev/null", "w"), no_color=True)
        removed, saved = remove_files(files, [0, 1], None, dry_run=False, console=console)

        assert removed == 2
        assert saved == 125
        assert not f1.exists()
        assert not f2.exists()
        console.file.close()

    def test_empty_indices(self, tmp_path: Path) -> None:
        from rich.console import Console

        from file_organizer.cli.dedupe_removal import remove_files

        f = tmp_path / "keep.txt"
        f.write_text("keep me")

        files = [_make_file_dict(f)]
        console = Console(file=open("/dev/null", "w"), no_color=True)
        removed, saved = remove_files(files, [], None, dry_run=False, console=console)

        assert removed == 0
        assert saved == 0
        assert f.exists()
        console.file.close()

    def test_oserror_is_handled_gracefully(self, tmp_path: Path) -> None:
        """OSError on unlink should not propagate; counter stays at 0."""
        from rich.console import Console

        from file_organizer.cli.dedupe_removal import remove_files

        missing = tmp_path / "gone_already.txt"
        # File doesn't exist — unlink() will raise FileNotFoundError (an OSError)
        files = [_make_file_dict(missing, size=512)]
        console = Console(file=open("/dev/null", "w"), no_color=True)
        removed, saved = remove_files(files, [0], None, dry_run=False, console=console)

        # The function catches OSError so removed stays 0
        assert removed == 0
        assert saved == 0
        console.file.close()

    def test_backup_is_created_when_backup_manager_provided(self, tmp_path: Path) -> None:
        from rich.console import Console

        from file_organizer.cli.dedupe_removal import remove_files

        f = tmp_path / "tobackup.txt"
        f.write_text("important")

        backup_mgr = MagicMock()
        backup_mgr.create_backup.return_value = tmp_path / "backup" / "tobackup.txt"

        files = [_make_file_dict(f, size=300)]
        console = Console(file=open("/dev/null", "w"), no_color=True)
        removed, saved = remove_files(files, [0], backup_mgr, dry_run=False, console=console)

        assert removed == 1
        backup_mgr.create_backup.assert_called_once_with(f)
        console.file.close()

    def test_backup_skipped_in_dry_run(self, tmp_path: Path) -> None:
        from rich.console import Console

        from file_organizer.cli.dedupe_removal import remove_files

        f = tmp_path / "nodrybackup.txt"
        f.write_text("x")

        backup_mgr = MagicMock()
        files = [_make_file_dict(f, size=10)]
        console = Console(file=open("/dev/null", "w"), no_color=True)
        remove_files(files, [0], backup_mgr, dry_run=True, console=console)

        backup_mgr.create_backup.assert_not_called()
        console.file.close()


# ---------------------------------------------------------------------------
# Tests: cli/dedupe_removal.py — process_duplicate_group()
# ---------------------------------------------------------------------------


class TestProcessDuplicateGroup:
    """Tests for process_duplicate_group()."""

    def _make_console(self, tmp_path: Path) -> tuple[object, object]:
        from rich.console import Console

        null_file = open(tmp_path / "console_out.txt", "w")
        return Console(file=null_file, no_color=True), null_file

    def test_skip_returns_zero(self, tmp_path: Path) -> None:
        """When get_user_selection returns [] the group is skipped."""
        from file_organizer.cli.dedupe_removal import process_duplicate_group

        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("data")
        f2.write_text("data")

        group = _make_duplicate_group([_make_file_meta(f1), _make_file_meta(f2)])
        console, null_file = self._make_console(tmp_path)

        with (
            patch("file_organizer.cli.dedupe_display.display_duplicate_group"),
            patch(
                "file_organizer.cli.dedupe_strategy.select_files_to_keep",
                side_effect=lambda files, strategy: files,
            ),
            patch(
                "file_organizer.cli.dedupe_strategy.get_user_selection",
                return_value=[],
            ),
        ):
            removed, saved = process_duplicate_group(
                group_id=1,
                file_hash="deadbeef",
                group=group,
                total_groups=1,
                strategy="newest",
                batch=False,
                backup_manager=None,
                dry_run=True,
                console=console,
            )

        assert removed == 0
        assert saved == 0
        null_file.close()

    def test_remove_files_called_when_indices_present(self, tmp_path: Path) -> None:
        """When get_user_selection returns indices, remove_files is called."""
        from file_organizer.cli.dedupe_removal import process_duplicate_group

        f1 = tmp_path / "dup1.txt"
        f2 = tmp_path / "dup2.txt"
        f1.write_text("copy")
        f2.write_text("copy")

        group = _make_duplicate_group([_make_file_meta(f1, 100), _make_file_meta(f2, 100)])
        console, null_file = self._make_console(tmp_path)

        with (
            patch("file_organizer.cli.dedupe_display.display_duplicate_group"),
            patch(
                "file_organizer.cli.dedupe_strategy.select_files_to_keep",
                side_effect=lambda files, strategy: files,
            ),
            patch(
                "file_organizer.cli.dedupe_strategy.get_user_selection",
                return_value=[1],
            ),
        ):
            removed, saved = process_duplicate_group(
                group_id=1,
                file_hash="deadbeef",
                group=group,
                total_groups=2,
                strategy="newest",
                batch=True,
                backup_manager=None,
                dry_run=False,
                console=console,
            )

        assert removed == 1
        assert saved == 100
        assert not f2.exists()
        null_file.close()


# ---------------------------------------------------------------------------
# Tests: cli/copilot.py  (via CLI runner)
# ---------------------------------------------------------------------------


class TestCopilotStatusCommand:
    """Tests for `file-organizer copilot status`."""

    def test_status_with_ollama_available(self, cli_runner: object) -> None:
        from file_organizer.cli.main import app

        fake_ollama = MagicMock()
        fake_client = MagicMock()
        fake_client.list.return_value = {"models": [{"name": "llama3:8b"}, {"name": "qwen2.5:3b"}]}
        fake_ollama.Client.return_value = fake_client

        with patch.dict("sys.modules", {"ollama": fake_ollama}):
            result = cli_runner.invoke(app, ["copilot", "status"])

        assert result.exit_code == 0
        assert "Copilot Status" in result.output
        assert "ready" in result.output.lower()

    def test_status_with_ollama_unavailable(self, cli_runner: object) -> None:
        from file_organizer.cli.main import app

        fake_ollama = MagicMock()
        fake_client = MagicMock()
        fake_client.list.side_effect = ConnectionError("Ollama not running")
        fake_ollama.Client.return_value = fake_client

        with patch.dict("sys.modules", {"ollama": fake_ollama}):
            result = cli_runner.invoke(app, ["copilot", "status"])

        assert result.exit_code == 0
        assert "unavailable" in result.output.lower()
        assert "ready" in result.output.lower()

    def test_status_ollama_import_error(self, cli_runner: object) -> None:
        """When ollama module cannot be imported, status still shows ready."""
        from file_organizer.cli.main import app

        with patch.dict("sys.modules", {"ollama": None}):
            result = cli_runner.invoke(app, ["copilot", "status"])

        assert result.exit_code == 0
        assert "ready" in result.output.lower()


class TestCopilotChatCommand:
    """Tests for `file-organizer copilot chat`."""

    def test_single_shot_message(self, cli_runner: object, tmp_path: Path) -> None:
        from file_organizer.cli.main import app

        with patch("file_organizer.services.copilot.engine.CopilotEngine") as MockEngine:
            MockEngine.return_value.chat.return_value = "Organising your files now."
            result = cli_runner.invoke(
                app,
                ["copilot", "chat", "organise my documents", "--dir", str(tmp_path)],
            )

        assert result.exit_code == 0
        assert "Organising your files now." in result.output
        MockEngine.assert_called_once_with(working_directory=str(tmp_path))
        MockEngine.return_value.chat.assert_called_once_with("organise my documents")

    def test_single_shot_uses_cwd_when_no_dir(
        self, cli_runner: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        from file_organizer.cli.main import app

        monkeypatch.chdir(tmp_path)
        with patch("file_organizer.services.copilot.engine.CopilotEngine") as MockEngine:
            MockEngine.return_value.chat.return_value = "Done."
            result = cli_runner.invoke(app, ["copilot", "chat", "hello"])

        assert result.exit_code == 0
        assert "Done." in result.output
        MockEngine.assert_called_once_with(working_directory=str(tmp_path))
        MockEngine.return_value.chat.assert_called_once_with("hello")

    def test_interactive_repl_quit(self, cli_runner: object) -> None:
        """Sending 'quit' to the REPL should exit cleanly."""
        from file_organizer.cli.main import app

        with patch("file_organizer.services.copilot.engine.CopilotEngine"):
            result = cli_runner.invoke(app, ["copilot", "chat"], input="quit\n")

        assert result.exit_code == 0
        assert "Goodbye" in result.output

    def test_interactive_repl_exit_command(self, cli_runner: object) -> None:
        from file_organizer.cli.main import app

        with patch("file_organizer.services.copilot.engine.CopilotEngine"):
            result = cli_runner.invoke(app, ["copilot", "chat"], input="exit\n")

        assert result.exit_code == 0
        assert "Goodbye" in result.output

    def test_interactive_repl_q_command(self, cli_runner: object) -> None:
        from file_organizer.cli.main import app

        with patch("file_organizer.services.copilot.engine.CopilotEngine"):
            result = cli_runner.invoke(app, ["copilot", "chat"], input="q\n")

        assert result.exit_code == 0
        assert "Goodbye" in result.output

    def test_interactive_repl_eof_exits(self, cli_runner: object) -> None:
        """EOF (empty input stream) should exit without error."""
        from file_organizer.cli.main import app

        with patch("file_organizer.services.copilot.engine.CopilotEngine"):
            result = cli_runner.invoke(app, ["copilot", "chat"], input="")

        assert result.exit_code == 0

    def test_interactive_repl_message_and_quit(self, cli_runner: object) -> None:
        """Chat with one message then quit."""
        from file_organizer.cli.main import app

        with patch("file_organizer.services.copilot.engine.CopilotEngine") as MockEngine:
            MockEngine.return_value.chat.return_value = "Files organised."
            result = cli_runner.invoke(
                app,
                ["copilot", "chat"],
                input="organise ~/Downloads\nquit\n",
            )

        assert result.exit_code == 0
        assert "Files organised." in result.output
        MockEngine.return_value.chat.assert_called_once_with("organise ~/Downloads")

    def test_interactive_repl_skips_blank_lines(self, cli_runner: object) -> None:
        """Blank lines should not trigger a chat call."""
        from file_organizer.cli.main import app

        with patch("file_organizer.services.copilot.engine.CopilotEngine") as MockEngine:
            result = cli_runner.invoke(
                app,
                ["copilot", "chat"],
                input="\n\nquit\n",
            )

        assert result.exit_code == 0
        MockEngine.return_value.chat.assert_not_called()
