"""Per-invocation CLI state stored in typer's ctx.obj.

Replaces the mutable module-level singleton in cli._globals.
Each CliRunner.invoke() / CLI invocation gets a fresh CLIState written
into ctx.obj by main_callback, so no state leaks between invocations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CLIState:
    """Typed container for global CLI flags set by the main callback."""

    verbose: bool = False
    dry_run: bool = False
    json_output: bool = False
    yes: bool = False
    no_interactive: bool = False
    # When true, --debug installs a loguru DEBUG-level stderr handler and
    # CLI exception handlers surface full Rich tracebacks via
    # console.print_exception(). This is the contract `fo --debug <cmd>`
    # offers beta testers for filing actionable bug reports.
    debug: bool = False
    # Loguru sink ID for the rotating file handler installed by main_callback.
    # Stored here so commands can check it exists without removing it.
    # Only main_callback's _remove_file_sink() should call logger.remove() on it.
    file_log_sink_id: int | None = None
    # Session ID for this CLI invocation (UUID4 suffix for uniqueness).
    # Used to correlate all log records from a single run in the session log.
    session_id: str | None = None
    # Loguru sink ID for the per-run session log handler.
    # Stored here for cleanup on CLI exit.
    session_log_sink_id: int | None = None


def _get_state() -> CLIState:
    """Retrieve the active CLIState from the current Typer/Click context or a new default instance.

    If a Click/Typer context is present and its `obj` is a `CLIState`, that instance is returned; otherwise a fresh `CLIState()` is returned (e.g., when called outside a CLI invocation such as in unit tests).

    Returns:
        CLIState: the active CLIState from the context, or a default `CLIState` instance when no applicable context exists.
    """
    import click

    ctx = click.get_current_context(silent=True)
    if ctx is not None and isinstance(ctx.obj, CLIState):
        return ctx.obj
    return CLIState()


def _merge_flag(local_flag: bool, global_flag: bool) -> bool:
    """Merge a command-local boolean flag with its global CLIState equivalent."""
    return local_flag or global_flag
