"""Tests for the main Typer CLI application."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


@pytest.mark.unit
class TestVersionCommand:
    """Tests for the version command."""

    def test_version_output(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "fo" in result.output
        assert "2.0.0" in result.output


@pytest.mark.unit
class TestConfigCommands:
    """Tests for the config sub-app."""

    def test_config_show_defaults(self, tmp_path: pytest.TempPathFactory) -> None:
        """Config show with no profile file should display defaults."""
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "default" in result.output

    def test_config_list_empty(self) -> None:
        """Config list should work even with no profiles."""
        result = runner.invoke(app, ["config", "list"])
        assert result.exit_code == 0

    def test_config_edit_runs_successfully(self) -> None:
        """Config edit should save without error."""
        result = runner.invoke(
            app,
            ["config", "edit", "--text-model", "test-model:latest"],
        )
        assert result.exit_code == 0
        assert "Saved" in result.output


@pytest.mark.unit
class TestHelpOutputs:
    """All sub-commands should produce help text."""

    @pytest.mark.parametrize(
        "cmd",
        [
            ["--help"],
            ["version", "--help"],
            ["organize", "--help"],
            ["preview", "--help"],
            ["config", "--help"],
            ["config", "show", "--help"],
            ["config", "list", "--help"],
            ["config", "edit", "--help"],
            ["model", "--help"],
            ["model", "list", "--help"],
            ["dedupe", "--help"],
            ["undo", "--help"],
            ["redo", "--help"],
            ["history", "--help"],
            ["analytics", "--help"],
            ["copilot", "--help"],
            ["copilot", "chat", "--help"],
            ["copilot", "status", "--help"],
            ["rules", "--help"],
            ["rules", "list", "--help"],
            ["rules", "sets", "--help"],
            ["rules", "add", "--help"],
            ["rules", "remove", "--help"],
            ["rules", "toggle", "--help"],
            ["rules", "preview", "--help"],
            ["rules", "export", "--help"],
            ["rules", "import", "--help"],
            ["update", "--help"],
            ["update", "check", "--help"],
            ["update", "install", "--help"],
            ["update", "rollback", "--help"],
        ],
    )
    def test_help(self, cmd: list[str]) -> None:
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0
        assert "Usage" in result.output or "usage" in result.output.lower()


@pytest.mark.unit
class TestModelListPlaceholder:
    """Model list placeholder should work."""

    def test_model_list(self) -> None:
        result = runner.invoke(app, ["model", "list"])
        assert result.exit_code == 0


@pytest.mark.unit
class TestGlobalOptions:
    """Global --verbose, --dry-run, --json flags should be accepted."""

    def test_verbose_version(self) -> None:
        result = runner.invoke(app, ["--verbose", "version"])
        assert result.exit_code == 0

    def test_dry_run_version(self) -> None:
        result = runner.invoke(app, ["--dry-run", "version"])
        assert result.exit_code == 0

    def test_json_version(self) -> None:
        result = runner.invoke(app, ["--json", "version"])
        assert result.exit_code == 0


def _make_manager(config_dir):
    """Helper to create a ConfigManager pointing at a temp dir."""
    from config import ConfigManager

    return ConfigManager(config_dir)


@pytest.mark.unit
class TestMainEntryPoint:
    """Tests for the main() entry point exception handling."""

    def test_keyboard_interrupt_handled_gracefully(self) -> None:
        from unittest.mock import patch

        from cli.main import main

        with (
            patch("cli.main._register_profile_command"),
            patch("cli.main.app", side_effect=KeyboardInterrupt()),
            patch("sys.exit") as mock_exit,
            patch("cli.main.console.print") as mock_print,
        ):
            main()
            mock_print.assert_called_with("\n[red]Operation cancelled by user.[/red]")
            mock_exit.assert_called_once_with(130)

    def test_broken_pipe_handled_gracefully(self) -> None:
        from unittest.mock import patch

        from cli.main import main

        with (
            patch("cli.main._register_profile_command"),
            patch("cli.main.app", side_effect=BrokenPipeError()),
            patch("sys.exit") as mock_exit,
            patch("os.open"),
            patch("os.dup2"),
        ):
            main()
            mock_exit.assert_called_once_with(0)

    def test_lazy_loading_prevents_heavy_imports(self) -> None:
        import subprocess
        import sys

        code = (
            "import sys\n"
            "from cli.main import app\n"
            "heavy_modules = ['sqlalchemy', 'pydantic', 'watchdog']\n"
            "loaded = [m for m in heavy_modules if m in sys.modules]\n"
            "if loaded:\n"
            "    print(','.join(loaded))\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, f"Heavy modules loaded: {result.stdout}"
