"""Main Typer CLI application for File Organizer.

Provides the unified entry point with all commands and sub-apps.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import typer
from rich.console import Console

from cli.autotag_v2 import autotag_app
from cli.benchmark import benchmark_app
from cli.config_cli import config_app
from cli.copilot import copilot_app
from cli.daemon import daemon_app
from cli.dedupe_v2 import dedupe_app
from cli.doctor import doctor
from cli.models_cli import model_app
from cli.organize import organize, preview
from cli.rules import rules_app
from cli.setup import setup_app
from cli.state import CLIState, _get_state
from cli.suggest import suggest_app
from cli.update import update_app
from cli.utilities import analyze, search
from undo._journal import default_journal_path as _default_journal_path
from undo.durable_move import sweep as _durable_move_sweep

console = Console()


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="fo",
    help="AI-powered local file management with privacy-first architecture.",
    no_args_is_help=True,
    rich_markup_mode=cast(Any, "rich"),
)

# ---------------------------------------------------------------------------
# State / callbacks
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    """Print the application version for the eager ``--version`` option."""
    if not value:
        return
    from version import __version__

    console.print(f"fo {__version__}")
    raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without executing."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm all prompts."),
    no_interactive: bool = typer.Option(
        False, "--no-interactive", help="Disable interactive prompts."
    ),
    version_flag: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the application version and exit.",
    ),
) -> None:
    """Global options applied to all commands."""
    _ = version_flag
    ctx.obj = CLIState(
        verbose=verbose,
        dry_run=dry_run,
        json_output=json_output,
        yes=yes,
        no_interactive=no_interactive,
    )

    from cli.interactive import set_flags
    from utils.log_redact import install_on_root

    # A.creds: attach the credential-redacting log filter to the root logger
    # so every ``logging.getLogger(__name__)`` in ``src/`` inherits protection
    # against api_key / token / secret / password / bearer leaks — even when
    # a future code path accidentally stuffs a secret into a log message or
    # exception args. Installed at the CLI entry point so the filter exists
    # before any command runs.
    install_on_root()

    # F7 (hardening roadmap #159): sweep any interrupted durable_move
    # operations from a prior crashed run. Runs before any command so
    # the on-disk state is coherent before the user's intent executes.
    # Failures here are logged + swallowed — a sweep error is never
    # worth crashing the CLI over; the next run will retry.
    try:
        _durable_move_sweep(_default_journal_path())
    except Exception:
        # Coderabbit PRRT_kwDOR_Rkws59fzVf: log at WARNING, not DEBUG.
        # Most users don't run with debug verbosity, so a permanently
        # unreadable journal (permission denied, corrupted JSONL) would
        # silently accumulate unrecovered entries across every
        # invocation with zero operator signal. WARNING surfaces the
        # problem without impacting normal runs (the journal is
        # missing/empty on the common path and ``sweep`` fast-exits
        # before hitting any of these error paths).
        logging.getLogger(__name__).warning(
            "durable_move sweep at CLI startup failed; "
            "interrupted-move recovery may be stuck. Inspect %s",
            _default_journal_path(),
            exc_info=True,
        )

    set_flags(yes=yes, no_interactive=no_interactive)


# ---------------------------------------------------------------------------
# Top-level commands (registered from sub-modules)
# ---------------------------------------------------------------------------

app.command()(organize)
app.command()(preview)
app.command()(search)
app.command()(analyze)
app.command()(doctor)


@app.command()
def version() -> None:
    """Show the application version."""
    from version import __version__

    console.print(f"fo {__version__}")


@app.command(name="hardware-info")
def hardware_info(
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Detect and display hardware capabilities."""
    from core.hardware_profile import detect_hardware

    profile = detect_hardware()

    if json_out or _get_state().json_output:
        import json

        console.print_json(json.dumps(profile.to_dict()))
    else:
        console.print("[bold]Hardware Profile[/bold]")
        console.print(f"  GPU type:            {profile.gpu_type.value}")
        console.print(f"  GPU name:            {profile.gpu_name or 'N/A'}")
        console.print(f"  VRAM:                {profile.vram_gb} GB")
        console.print(f"  System RAM:          {profile.ram_gb} GB")
        console.print(f"  CPU cores:           {profile.cpu_cores}")
        console.print(f"  OS:                  {profile.os_name}")
        console.print(f"  Architecture:        {profile.arch}")
        console.print()
        console.print("[bold]Recommendations[/bold]")
        console.print(f"  Text model:          {profile.recommended_text_model()}")
        console.print(f"  Parallel workers:    {profile.recommended_workers()}")


# ---------------------------------------------------------------------------
# Sub-apps (config, model, and third-party integrations)
# ---------------------------------------------------------------------------

app.add_typer(config_app, name="config")
app.add_typer(model_app, name="model")
app.add_typer(autotag_app, name="autotag")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(copilot_app, name="copilot")
app.add_typer(daemon_app, name="daemon")
app.add_typer(dedupe_app, name="dedupe")
app.add_typer(rules_app, name="rules")
app.add_typer(setup_app, name="setup")
app.add_typer(suggest_app, name="suggest")
app.add_typer(update_app, name="update")


# ---------------------------------------------------------------------------
# Operation history / undo / redo commands
# ---------------------------------------------------------------------------


@app.command()
def undo(
    operation_id: int | None = typer.Option(None, help="Specific operation ID to undo."),
    transaction_id: str | None = typer.Option(None, help="Transaction ID to undo."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without executing."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
) -> None:
    """Undo file operations."""
    from cli.undo_redo import undo_command as _undo

    code = _undo(
        operation_id=operation_id,
        transaction_id=transaction_id,
        dry_run=dry_run or _get_state().dry_run,
        verbose=verbose or _get_state().verbose,
    )
    raise typer.Exit(code=code)


@app.command()
def redo(
    operation_id: int | None = typer.Option(None, help="Specific operation ID to redo."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without executing."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
) -> None:
    """Redo file operations."""
    from cli.undo_redo import redo_command as _redo

    code = _redo(
        operation_id=operation_id,
        dry_run=dry_run or _get_state().dry_run,
        verbose=verbose or _get_state().verbose,
    )
    raise typer.Exit(code=code)


@app.command()
def history(
    limit: int = typer.Option(10, help="Maximum number of operations to show."),
    operation_type: str | None = typer.Option(None, "--type", help="Filter by type."),
    status: str | None = typer.Option(None, help="Filter by status."),
    stats: bool = typer.Option(False, help="Show statistics."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
) -> None:
    """View operation history."""
    from cli.undo_redo import history_command as _history

    code = _history(
        limit=limit,
        operation_type=operation_type,
        status=status,
        stats=stats,
        verbose=verbose or _get_state().verbose,
    )
    raise typer.Exit(code=code)


@app.command()
def analytics(
    directory: Path | None = typer.Argument(None, help="Directory to analyze."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
) -> None:
    """Display storage analytics dashboard."""
    from cli.analytics import analytics_command
    from cli.path_validation import resolve_cli_path

    args: list[str] = []
    if directory is not None:
        # A.cli: resolve + validate the directory argument before
        # handing the string back to the Click-compat analytics_command.
        directory = resolve_cli_path(directory, must_exist=True, must_be_dir=True)
        args.append(str(directory))
    if verbose or _get_state().verbose:
        args.append("--verbose")

    code = analytics_command(args)
    raise typer.Exit(code=code)


# ---------------------------------------------------------------------------
# Profile sub-app — Click interop (deferred to reduce startup latency)
# ---------------------------------------------------------------------------

# NOTE: profile_command registration is deferred to main() to avoid loading
# cli.profile (and its heavy intelligence service chain) at
# module import time.  Typer wraps Click, so we register it just before app().


def _register_profile_command() -> None:
    """Lazily register the Click-based profile sub-command."""
    try:
        from cli.profile import profile_command as _profile_click_group

        typer_click_object = typer.main.get_group(app)
        typer_click_object.add_command(_profile_click_group, "profile")
    except ImportError:
        # Profile module may fail to import if intelligence services
        # are not installed; we degrade gracefully.
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for ``fo`` / ``fo`` console scripts."""
    _register_profile_command()
    app()


if __name__ == "__main__":
    main()
