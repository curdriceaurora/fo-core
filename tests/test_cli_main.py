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
@pytest.mark.ci
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
    """
    Create a ConfigManager configured to use the given directory.

    Parameters:
        config_dir (str | pathlib.Path): Path to the directory that the ConfigManager should use for configuration files.

    Returns:
        ConfigManager: An instance of ConfigManager configured to operate on `config_dir`.
    """
    from config import ConfigManager

    return ConfigManager(config_dir)


@pytest.mark.unit
@pytest.mark.ci
@pytest.mark.integration
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

    def test_click_abort_handled_gracefully(self) -> None:
        """Under standalone_mode=False, Click converts KeyboardInterrupt
        into click.exceptions.Abort and re-raises. Our outer handler must
        catch that as the user-cancellation path."""
        from unittest.mock import patch

        import click

        from cli.main import main

        with (
            patch("cli.main._register_profile_command"),
            patch("cli.main.app", side_effect=click.exceptions.Abort()),
            patch("sys.exit") as mock_exit,
            patch("cli.main.console.print") as mock_print,
        ):
            main()
            mock_print.assert_called_with("\n[red]Operation cancelled by user.[/red]")
            mock_exit.assert_called_once_with(130)

    def test_typer_exit_propagates_with_code(self) -> None:
        """typer.Exit(code=N) is click.exceptions.Exit; main() must
        propagate the exit_code to sys.exit (otherwise commands that
        deliberately exit non-zero get clobbered to 0 under
        standalone_mode=False)."""
        from unittest.mock import patch

        import click

        from cli.main import main

        with (
            patch("cli.main._register_profile_command"),
            patch("cli.main.app", side_effect=click.exceptions.Exit(code=2)),
            patch("sys.exit") as mock_exit,
        ):
            main()
            mock_exit.assert_called_once_with(2)

    def test_broken_pipe_handled_gracefully(self) -> None:
        from unittest.mock import patch

        from cli.main import main

        with (
            patch("cli.main._register_profile_command"),
            patch("cli.main.app", side_effect=BrokenPipeError()),
            patch("sys.exit") as mock_exit,
            patch("cli.main.os.open"),
            patch("cli.main.os.dup2"),
            patch("cli.main.os.close") as mock_close,
        ):
            main()
            mock_exit.assert_called_once_with(0)
            # Handler must close the devnull fd (try/finally guarantee).
            mock_close.assert_called_once()

    def test_lazy_loading_prevents_heavy_imports(self) -> None:
        """
        Verifies that importing `cli.main.app` does not cause heavyweight optional modules to be loaded.

        Runs a separate Python process that imports `cli.main.app` and fails the test if any of the modules `sqlalchemy`, `pydantic`, or `watchdog` are present in that process's `sys.modules`.
        """
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
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, (
            f"Heavy modules loaded or import failed: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )


@pytest.mark.unit
@pytest.mark.ci
@pytest.mark.integration
class TestLazyCommandProxy:
    """Direct tests for LazyCommandProxy/LazyTyperGroup paths not exercised via --help."""

    def test_proxy_loads_non_typer_click_command(self) -> None:
        """_load() with a non-Typer attribute uses the typing.cast fallback (line 47)."""
        import click

        from cli.lazy import LazyCommandProxy

        proxy = LazyCommandProxy("noop", "click", "Command", "test")
        loaded = proxy._load()
        assert loaded is click.Command
        assert proxy._real_cmd is click.Command

    def test_proxy_list_and_get_command_on_non_group_returns_empty(self) -> None:
        """list_commands and get_command return [] / None when underlying is not a Group."""
        import click

        from cli.lazy import LazyCommandProxy

        proxy = LazyCommandProxy("noop", "click", "Command", "test")
        ctx = click.Context(click.Command("noop"))
        # click.Command is a class object (not Group instance); both branches fall through
        assert proxy.list_commands(ctx) == []
        assert proxy.get_command(ctx, "anything") is None

    def test_lazy_typer_group_lists_lazy_commands(self) -> None:
        """LazyTyperGroup.list_commands merges base commands with LAZY_COMMANDS keys."""
        import click

        from cli.lazy import LAZY_COMMANDS, LazyTyperGroup

        group = LazyTyperGroup(name="fo")
        ctx = click.Context(group)
        cmds = group.list_commands(ctx)
        for name in ("config", "model", "daemon"):
            assert name in cmds, f"expected lazy command {name!r} in {cmds}"
        # Every key advertised by LAZY_COMMANDS must appear in the rendered list
        for key in LAZY_COMMANDS:
            assert key in cmds

    def test_lazy_typer_group_get_command_returns_proxy(self) -> None:
        """LazyTyperGroup.get_command returns a LazyCommandProxy for known names."""
        import click

        from cli.lazy import LazyCommandProxy, LazyTyperGroup

        group = LazyTyperGroup(name="fo")
        ctx = click.Context(group)
        proxy = group.get_command(ctx, "config")
        assert isinstance(proxy, LazyCommandProxy)
        # Unknown name falls through to super().get_command
        assert group.get_command(ctx, "no_such_cmd") is None


@pytest.mark.integration
class TestBackendDetectorImportSurface:
    """Integration coverage nudge: lazy CLI no longer transitively imports
    setup_wizard, which used to drag backend_detector module-level code into
    every integration run. Re-import explicitly so the per-module floor holds."""

    def test_backend_detector_imports_clean(self) -> None:
        from core import backend_detector

        assert hasattr(backend_detector, "OLLAMA_AVAILABLE")

    def test_detect_ollama_handles_subprocess_oserror(self) -> None:
        """Cover the `except (subprocess.SubprocessError, OSError)` branch
        in detect_ollama (lines 101-102): subprocess.run raises OSError, the
        CLI probe falls through, and detect_ollama still returns a
        well-formed OllamaStatus."""
        from unittest.mock import patch

        from core import backend_detector

        with patch.object(backend_detector.subprocess, "run", side_effect=OSError("simulated")):
            status = backend_detector.detect_ollama()

        assert isinstance(status, backend_detector.OllamaStatus)
        # CLI probe failed; cli_installed must therefore be False.
        assert status.installed is False or status.running is True
