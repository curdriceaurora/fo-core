"""Main Typer CLI application for File Organizer.

Provides the unified entry point with all commands and sub-apps.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any, cast

import click
import typer
from rich.console import Console

from cli.doctor import doctor
from cli.lazy import LazyTyperGroup
from cli.organize import organize, preview
from cli.state import CLIState, _get_state, _merge_flag
from cli.utilities import analyze, search
from undo._journal import default_journal_path as _default_journal_path
from undo.durable_move import sweep as _durable_move_sweep

console = Console()


def _fo_version() -> str:
    try:
        return _pkg_version("fo-core")
    except _PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="fo",
    help="AI-powered local file management with privacy-first architecture.",
    no_args_is_help=True,
    rich_markup_mode=cast(Any, "rich"),
    cls=LazyTyperGroup,
)

# ---------------------------------------------------------------------------
# State / callbacks
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    """Print the application version for the eager ``--version`` option."""
    if not value:
        return

    console.print(f"fo {_fo_version()}")
    raise typer.Exit()


_SETUP_GATE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "setup",
        "version",
        "doctor",
        "update",
        "recover",
        "config",
        "hardware-info",
        "logs",  # Allow `fo logs` pre-setup for diagnostics
    }
)
"""Commands that work pre-setup. They're either bootstrap (`setup`,
`config`), read-only diagnostics (`doctor`, `version`, `hardware-info`,
`logs`), the updater, or emergency recovery. Adding a command here
relaxes the first-run gate for it — verify the command doesn't write
or organize files first."""


def _cleanup_old_session_logs(logs_dir: Path, retention_days: int = 3) -> None:
    """Remove session log files older than retention_days.

    Session logs have filenames like: fo-2026-05-23T12-34-56-abc123.log

    Parameters:
        logs_dir (Path): The logs directory containing session subdirectory.
        retention_days (int): Number of days to retain session logs (default: 3).
    """
    session_log_dir = logs_dir / "sessions"
    if not session_log_dir.exists():
        return

    cutoff_time = time.time() - (retention_days * 86400)  # 86400 seconds per day
    try:
        for log_file in session_log_dir.glob("fo-*.log"):
            if log_file.stat().st_mtime < cutoff_time:
                try:
                    log_file.unlink()
                except OSError:
                    pass  # Skip files we can't delete (permissions, in-use, etc.)
    except OSError:
        pass  # If we can't read the directory, skip cleanup gracefully


@app.callback()
def main_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without executing."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm all prompts."),
    interactive: bool = typer.Option(
        True, "--interactive/--no-interactive", help="Toggle interactive prompts."
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help=(
            "Enable verbose logging and surface tracebacks on errors. "
            "Required for filing useful beta bug reports."
        ),
    ),
    version_flag: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the application version and exit.",
    ),
) -> None:
    """Initialize global CLI state and perform startup bookkeeping.

    Sets ctx.obj to a CLIState containing the provided flags (with CLIState.no_interactive set to the inverse of `interactive`), generates a unique session ID for this invocation, installs the credential-redacting log filter on the root logger, sets up per-run session logging, and runs a durable-move recovery sweep on the default journal to clean up interrupted operations. The startup sweep is skipped when the invoked subcommand is "recover"; if the sweep raises an exception it is logged at WARNING and execution continues.

    Parameters:
        ctx (typer.Context): Typer invocation context used to store CLIState.
        interactive (bool): If False, stored state will set `no_interactive=True`.
        version_flag (bool): Eager version callback value (accepted and ignored here).
    """
    _ = version_flag

    # Generate unique session ID for this CLI invocation
    # Format: YYYY-MM-DDTHH-MM-SS-{short_uuid}
    session_timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    session_uuid = str(uuid.uuid4())[:8]  # First 8 chars of UUID for brevity
    session_id = f"{session_timestamp}-{session_uuid}"

    ctx.obj = CLIState(
        verbose=verbose,
        dry_run=dry_run,
        json_output=json_output,
        yes=yes,
        no_interactive=not interactive,
        debug=debug,
        session_id=session_id,
    )

    if debug:
        # Install a loguru DEBUG-level stderr handler so every
        # `loguru.logger.*` call across `src/` surfaces during this
        # invocation. `backtrace=True` gives frame-linked tracebacks for
        # swallowed exceptions logged via `logger.exception(...)`.
        # `diagnose=False` (deliberately): diagnose=True annotates each
        # frame with local variable values, which can expose credentials,
        # API keys, or other sensitive runtime state when the output is
        # shared in a bug report. Adding here (vs at module import)
        # keeps the no-debug path zero-overhead.
        import sys as _sys

        from loguru import logger as _loguru_logger

        _sink_id = _loguru_logger.add(_sys.stderr, level="DEBUG", backtrace=True, diagnose=False)

        def _remove_debug_sink() -> None:
            try:
                _loguru_logger.remove(_sink_id)
            except ValueError:
                pass  # already removed

        ctx.call_on_close(_remove_debug_sink)

    # Rotating JSON file log — always installed so errors outlive the terminal
    # session.  Uses loguru's built-in rotation + compression instead of a
    # system logrotate dependency so the app is self-contained.
    #
    # Location: {state_dir}/logs/fo.log  (platform-appropriate via XDG / platformdirs)
    #   macOS:   ~/Library/Application Support/fo/logs/fo.log
    #   Linux:   ~/.local/state/fo/logs/fo.log
    #   Windows: %APPDATA%\fo\logs\fo.log
    #
    # diagnose=False: never write local variable values to disk — they can
    # contain secrets, API keys, or PII even in production paths.
    _file_log_level = "DEBUG" if debug else "WARNING"
    try:
        from loguru import logger as _ll

        from config.path_manager import get_canonical_paths as _get_canonical_paths

        _log_dir = _get_canonical_paths()["logs"]
        _log_dir.mkdir(parents=True, exist_ok=True)
        _file_sink_id = _ll.add(
            _log_dir / "fo.log",
            level=_file_log_level,
            rotation="10 MB",
            retention="7 days",
            compression="gz",
            serialize=True,
            backtrace=False,
            diagnose=False,
            encoding="utf-8",
            enqueue=True,
        )

        def _remove_file_sink() -> None:
            try:
                _ll.remove(_file_sink_id)
            except ValueError:
                pass  # sink already removed or was never real (e.g. test spy)

        ctx.call_on_close(_remove_file_sink)
        ctx.obj.file_log_sink_id = _file_sink_id  # A5: expose ID so callers don't bare-remove
    except OSError:
        pass  # log dir unwritable — degrade gracefully

    # Per-run session log — always DEBUG level for post-run analysis
    # Location: {state_dir}/logs/sessions/fo-YYYY-MM-DDTHH-MM-SS-{uuid}.log
    # Retention: 3 days (cleaned up on next CLI startup)
    #
    # These logs capture full DEBUG output for every CLI invocation
    # regardless of the --debug flag, enabling post-run diagnosis.
    # The session_id is injected into every log record via a custom patcher.
    try:
        from loguru import logger as _session_logger

        from config.path_manager import get_canonical_paths as _get_session_paths

        _session_log_dir = _get_session_paths()["logs"]
        _session_subdir = _session_log_dir / "sessions"
        _session_subdir.mkdir(parents=True, exist_ok=True)

        # Clean up old session logs (3-day retention)
        _cleanup_old_session_logs(_session_log_dir, retention_days=3)

        # Session log filename: fo-2026-05-23T12-34-56-abc123.log
        _session_log_file = _session_subdir / f"fo-{session_id}.log"

        def _session_filter(record: Any) -> bool:
            record["extra"]["session_id"] = session_id
            return True

        _session_sink_id = _session_logger.add(
            _session_log_file,
            level="DEBUG",  # Always DEBUG for full diagnostics
            serialize=True,  # NDJSON format for structured analysis
            backtrace=False,
            diagnose=False,
            encoding="utf-8",
            enqueue=True,
            filter=_session_filter,
        )

        def _remove_session_sink() -> None:
            try:
                _session_logger.remove(_session_sink_id)
            except ValueError:
                pass  # sink already removed

        ctx.call_on_close(_remove_session_sink)
        ctx.obj.session_log_sink_id = _session_sink_id
    except OSError:
        pass  # session log dir unwritable — degrade gracefully

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
    #
    # F7.1 / codex lCbV / coderabbit lDDy: SKIP the startup sweep when
    # the user is invoking ``fo recover``. ``recover`` is the read-only
    # preview of what sweep would do; running sweep first would mutate
    # state (unlink, compact) before the preview ran and then report
    # "no retained entries" — breaking the read-only contract and
    # making the preview unreliable.
    # Step 3 (UX): the first-run setup gate covered only `organize`/`preview`
    # before — every other command would fail with cryptic stack traces if
    # the user hadn't run `fo setup` yet. Promote the check to the callback
    # so all entry commands except an explicit allowlist get the friendly
    # "First-time setup required" panel. The allowlist holds bootstrap +
    # read-only diagnostic commands.
    # ctx.resilient_parsing is True only during shell-completion parsing,
    # NOT for --help (Click fires the eager --help option after the callback,
    # with resilient_parsing=False).  LazyTyperGroup.parse_args stashes
    # whether --help/-h was present in ctx.meta['help_requested'] before
    # Click's resolve_command consumes the args — that flag is the reliable
    # way to skip the gate for help invocations.
    if (
        ctx.invoked_subcommand
        and ctx.invoked_subcommand not in _SETUP_GATE_ALLOWLIST
        and not ctx.resilient_parsing  # True during shell-completion only
        and not ctx.meta.get("help_requested", False)  # skip gate for --help/-h
    ):
        from cli.organize import _check_setup_completed

        _check_setup_completed()

    if ctx.invoked_subcommand != "recover":
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


# ---------------------------------------------------------------------------
# Top-level commands (registered from sub-modules)
# ---------------------------------------------------------------------------

app.command()(organize)
app.command()(preview)
app.command()(search)
app.command()(analyze)
app.command()(doctor)


@app.command()
def logs(
    ctx: typer.Context,
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output (tail -f)."),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show."),
    session: bool = typer.Option(
        False,
        "--session",
        help="Show latest session log instead of main fo.log.",
    ),
    list_sessions: bool = typer.Option(
        False,
        "--list",
        "-l",
        help="List all available session logs.",
    ),
) -> None:
    """View or tail fo log files."""
    from cli.logs import logs_command
    from cli.state import _get_state

    _ = ctx  # ctx is used implicitly by _get_state via click.get_current_context
    state = _get_state()
    logs_command(
        follow=follow,
        lines=lines,
        session=session,
        list_sessions=list_sessions,
        current_session_id=state.session_id,
    )


@app.command()
def version() -> None:
    """Show the application version."""
    console.print(f"fo {_fo_version()}")


@app.command(name="hardware-info")
def hardware_info(
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Print the current machine's hardware profile to the console.

    If `json_out` is True or the global CLI state requests JSON output, prints the profile as structured JSON; otherwise prints a human-readable summary of detected hardware and recommendations.

    Parameters:
        json_out (bool): Force JSON formatted output when True.
    """
    from core.hardware_profile import detect_hardware

    profile = detect_hardware()

    if json_out or _get_state().json_output:
        console.print_json(data=profile.to_dict())
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

# Sub-apps are loaded lazily via cli.lazy.LazyTyperGroup


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
    """Undo previously recorded file operations.

    Parameters:
        operation_id: Specific operation ID to target for undo; if omitted, other filters or recent operations may be considered.
        transaction_id: Transaction ID to target for undo; if provided, undoes operations within that transaction.
        dry_run: If true, show the actions that would be performed without making changes.
        verbose: If true, emit more detailed output during the undo process.
    """
    from cli.undo_redo import undo_command as _undo

    code = _undo(
        operation_id=operation_id,
        transaction_id=transaction_id,
        dry_run=_merge_flag(dry_run, _get_state().dry_run),
        verbose=_merge_flag(verbose, _get_state().verbose),
    )
    raise typer.Exit(code=code if code is not None else 1)


@app.command()
def redo(
    operation_id: int | None = typer.Option(None, help="Specific operation ID to redo."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without executing."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
) -> None:
    """Redo previously recorded file operations.

    Parameters:
        operation_id (int | None): Specific operation ID to redo; if omitted, the command will select the default/recent operation.
        dry_run (bool): Preview actions without making changes; local flag is merged with global dry-run state.
        verbose (bool): Enable verbose output; local flag is merged with global verbose state.
    """
    from cli.undo_redo import redo_command as _redo

    code = _redo(
        operation_id=operation_id,
        dry_run=_merge_flag(dry_run, _get_state().dry_run),
        verbose=_merge_flag(verbose, _get_state().verbose),
    )
    raise typer.Exit(code=code if code is not None else 1)


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
        verbose=_merge_flag(verbose, _get_state().verbose),
    )
    raise typer.Exit(code=code if code is not None else 1)


@app.command()  # noqa: G3 (journal is a read-only path from user state dir)
def recover(
    journal: Path | None = typer.Option(
        None,
        help="Override path to durable_move.journal (defaults to the user state dir).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
) -> None:
    """Preview pending durable_move recovery actions without executing them.

    Exits with status 0 if no recovery work would be performed, 1 if recovery actions are planned (so callers can detect a stuck journal).

    Parameters:
        journal (Path | None): Optional override path to the durable_move.journal file (defaults to the user's state directory).
    """
    from cli.undo_recover import recover_command as _recover

    code = _recover(journal=journal, verbose=_merge_flag(verbose, _get_state().verbose))
    raise typer.Exit(code=code if code is not None else 1)


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
    if _merge_flag(verbose, _get_state().verbose):
        args.append("--verbose")

    code = analytics_command(args)
    raise typer.Exit(code=code if code is not None else 1)


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
    """Run the fo command-line application.

    Registers the deferred profile command and invokes the Typer app with
    ``standalone_mode=False`` so that ``KeyboardInterrupt`` and
    ``BrokenPipeError`` propagate out of Click for our handlers to see — under
    Click's default standalone mode the framework catches both internally and
    our outer ``except`` clauses would never fire (codex review on PR #230).

    Exit codes:
        130 — user pressed Ctrl+C (POSIX SIGINT).
          0 — stdout consumer closed the pipe (e.g. ``fo ... | head``).
        Other typer/click exits propagate their own ``exit_code``.
    """
    _register_profile_command()

    try:
        app(standalone_mode=False)
    except (KeyboardInterrupt, click.exceptions.Abort):
        # Click converts KeyboardInterrupt → click.Abort under
        # standalone_mode=False; the bare KeyboardInterrupt branch covers
        # any direct raise (and the unit-test mock path).
        console.print("\n[red]Operation cancelled by user.[/red]")
        sys.exit(130)
    except click.exceptions.UsageError as e:
        # Mimic Click's standalone-mode behavior: print the usage message
        # to stderr and exit with the typed exit code.
        e.show()
        sys.exit(e.exit_code)
    except click.exceptions.Exit as e:
        # `typer.Exit(code=N)` round-trips through this branch.
        sys.exit(e.exit_code)
    except BrokenPipeError:
        # Stdout consumer closed the pipe (canonical: `fo ... | head`).
        # Redirect both stdout and stderr to /dev/null so the interpreter's
        # final flush during shutdown does not raise another BrokenPipeError
        # and noise the terminal — this is the standard CLI pattern (git,
        # grep, etc. all behave this way).
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
            os.dup2(devnull, sys.stderr.fileno())
        finally:
            os.close(devnull)
        sys.exit(0)


if __name__ == "__main__":
    main()
