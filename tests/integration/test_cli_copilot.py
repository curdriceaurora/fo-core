"""Integration tests for cli/copilot.py.

Coverage target: copilot.py = 100% (per
``scripts/coverage/integration_module_floor_baseline.json``).

Originally part of ``test_cli_dedupe_removal_copilot.py``; that file was
removed in PR #205 alongside the legacy ``cli/dedupe_removal.py`` module.
The copilot tests are independent — they exercise ``cli/copilot.py`` via
the CLI runner — so they are split into this standalone file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# Tests: cli/copilot.py  (via CLI runner)
# ---------------------------------------------------------------------------


class TestCopilotStatusCommand:
    """Tests for `fo copilot status`."""

    def test_status_with_ollama_available(self, cli_runner: object) -> None:
        from cli.main import app

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
        from cli.main import app

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
        from cli.main import app

        with patch.dict("sys.modules", {"ollama": None}):
            result = cli_runner.invoke(app, ["copilot", "status"])

        assert result.exit_code == 0
        assert "ready" in result.output.lower()


class TestCopilotChatCommand:
    """Tests for `fo copilot chat`."""

    def test_single_shot_message(self, cli_runner: object, tmp_path: Path) -> None:
        from cli.main import app

        with patch("services.copilot.engine.CopilotEngine") as MockEngine:
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

        from cli.main import app

        monkeypatch.chdir(tmp_path)
        with patch("services.copilot.engine.CopilotEngine") as MockEngine:
            MockEngine.return_value.chat.return_value = "Done."
            result = cli_runner.invoke(app, ["copilot", "chat", "hello"])

        assert result.exit_code == 0
        assert "Done." in result.output
        MockEngine.assert_called_once_with(working_directory=str(tmp_path))
        MockEngine.return_value.chat.assert_called_once_with("hello")

    def test_interactive_repl_quit(self, cli_runner: object) -> None:
        """Sending 'quit' to the REPL should exit cleanly."""
        from cli.main import app

        with patch("services.copilot.engine.CopilotEngine"):
            result = cli_runner.invoke(app, ["copilot", "chat"], input="quit\n")

        assert result.exit_code == 0
        assert "Goodbye" in result.output

    def test_interactive_repl_exit_command(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("services.copilot.engine.CopilotEngine"):
            result = cli_runner.invoke(app, ["copilot", "chat"], input="exit\n")

        assert result.exit_code == 0
        assert "Goodbye" in result.output

    def test_interactive_repl_q_command(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("services.copilot.engine.CopilotEngine"):
            result = cli_runner.invoke(app, ["copilot", "chat"], input="q\n")

        assert result.exit_code == 0
        assert "Goodbye" in result.output

    def test_interactive_repl_eof_exits(self, cli_runner: object) -> None:
        """EOF (empty input stream) should exit without error."""
        from cli.main import app

        with patch("services.copilot.engine.CopilotEngine"):
            result = cli_runner.invoke(app, ["copilot", "chat"], input="")

        assert result.exit_code == 0

    def test_interactive_repl_message_and_quit(self, cli_runner: object) -> None:
        """Chat with one message then quit."""
        from cli.main import app

        with patch("services.copilot.engine.CopilotEngine") as MockEngine:
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
        from cli.main import app

        with patch("services.copilot.engine.CopilotEngine") as MockEngine:
            result = cli_runner.invoke(
                app,
                ["copilot", "chat"],
                input="\n\nquit\n",
            )

        assert result.exit_code == 0
        MockEngine.return_value.chat.assert_not_called()
