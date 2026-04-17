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


def _get_state() -> CLIState:
    """Return the CLIState from the active typer context, or defaults.

    Falls back to a default CLIState() when called outside a typer
    invocation (e.g. direct function calls in unit tests).
    """
    try:
        import click

        ctx = click.get_current_context()
        if isinstance(ctx.obj, CLIState):
            return ctx.obj
    except RuntimeError:
        pass
    return CLIState()
