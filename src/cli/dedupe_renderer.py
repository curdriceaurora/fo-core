"""Output backends for the ``dedupe`` CLI sub-app.

Issue #157 / Epic D / D4 Renderer extraction.

Three implementations, selected via ``--format``:

- :class:`RichRenderer` (default): Rich tables + colored output for terminals.
- :class:`JsonRenderer`: buffered single-document JSON envelope, flushed in
  :meth:`end`. Suitable for ``dedupe scan --format=json | jq``.
- :class:`PlainRenderer`: line-oriented, no colors, no tables. Suitable for
  ``awk`` / ``cut`` pipelines.

All three implement :class:`Renderer` (a runtime-checkable Protocol).
``--format=rich`` produces output identical to the pre-D4 behavior of
``dedupe_v2.py`` (modulo the flag plumbing).
"""

from __future__ import annotations

import contextlib
import json
import sys
from contextlib import AbstractContextManager
from pathlib import Path
from typing import IO, Any, Literal, Protocol, runtime_checkable

from rich.console import Console
from rich.table import Table

from services.deduplication.index import DuplicateGroup

MessageLevel = Literal["info", "success", "warning", "error"]
ResolveAction = Literal["would_remove", "removed", "error"]

_VALID_FORMATS = ("rich", "json", "plain")
_VALID_LEVELS: tuple[MessageLevel, ...] = ("info", "success", "warning", "error")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Renderer(Protocol):
    """Output backend protocol for the dedupe CLI.

    Lifecycle: :meth:`begin` (once, with the command name) → arbitrary
    ``render_*`` calls → :meth:`end` (once). For ``RichRenderer`` and
    ``PlainRenderer`` the lifecycle methods are no-ops; for
    ``JsonRenderer`` they bracket the buffered envelope.
    """

    def begin(self, command: str) -> None: ...
    def end(self) -> None: ...
    def status(self, message: str) -> AbstractContextManager[None]: ...
    def render_groups_header(self, count: int) -> None: ...
    def render_groups(self, groups: dict[str, DuplicateGroup]) -> None: ...
    def render_resolve_action(
        self,
        action: ResolveAction,
        path: Path,
        error: str | None = None,
    ) -> None: ...
    def render_resolve_summary(self, removed_count: int, dry_run: bool) -> None: ...
    def render_report(
        self,
        stats: dict[str, Any],
        groups: dict[str, DuplicateGroup],
        total_wasted: int,
    ) -> None: ...
    def render_message(self, level: MessageLevel, message: str) -> None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_size(size: int) -> str:
    """Format file size in human-readable units (binary, 1024-step)."""
    value: float = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _validate_level(level: str) -> MessageLevel:
    """Reject unknown message levels.

    Per anti-pattern T10: predicates and dispatchers must reject inputs that
    look valid but aren't, with a message naming the accepted alternatives.
    """
    if level not in _VALID_LEVELS:
        raise ValueError(
            f"Unknown message level {level!r}; expected one of: " + ", ".join(_VALID_LEVELS)
        )
    # After the membership check above, mypy narrows ``level`` to the Literal.
    return level


# ---------------------------------------------------------------------------
# RichRenderer — visible terminal output (default)
# ---------------------------------------------------------------------------


_LEVEL_RICH_STYLE: dict[MessageLevel, str] = {
    "info": "dim",
    "success": "green",
    "warning": "yellow",
    "error": "red",
}


class RichRenderer:
    """Rich-console output. Tables, colors, status spinners."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console if console is not None else Console()

    def begin(self, command: str) -> None:
        # Rich output is unbuffered; nothing to do.
        del command

    def end(self) -> None:
        # Rich output is unbuffered; nothing to do.
        return

    def status(self, message: str) -> AbstractContextManager[None]:
        # Rich's ``Console.status`` returns a Status object that is itself a
        # context manager. Cast for the Protocol's narrower return type.
        return self._console.status(message)  # type: ignore[return-value]

    def render_groups_header(self, count: int) -> None:
        self._console.print(f"Found [bold]{count}[/bold] duplicate groups.\n")

    def render_groups(self, groups: dict[str, DuplicateGroup]) -> None:
        for hash_val, group in groups.items():
            table = Table(title=f"Group {hash_val[:12]}…", show_lines=True)
            table.add_column("#", style="dim", width=3)
            table.add_column("Path")
            table.add_column("Size", justify="right")
            table.add_column("Modified")
            for idx, fmeta in enumerate(group.files, 1):
                table.add_row(
                    str(idx),
                    str(fmeta.path),
                    _format_size(fmeta.size),
                    fmeta.modified_time.strftime("%Y-%m-%d %H:%M"),
                )
            self._console.print(table)
            self._console.print(
                f"  [dim]Wasted space: {_format_size(group.wasted_space)}[/dim]\n"
            )

    def render_resolve_action(
        self,
        action: ResolveAction,
        path: Path,
        error: str | None = None,
    ) -> None:
        if action == "would_remove":
            self._console.print(f"  [dim]Would remove:[/dim] {path}")
        elif action == "removed":
            self._console.print(f"  [red]Removed:[/red] {path}")
        elif action == "error":
            self._console.print(f"  [red]Error removing {path}: {error}[/red]")
        else:
            raise ValueError(
                f"Unknown resolve action {action!r}; expected one of: would_remove, removed, error"
            )

    def render_resolve_summary(self, removed_count: int, dry_run: bool) -> None:
        if dry_run:
            self._console.print("\n[yellow]Dry run — no files were removed.[/yellow]")
        else:
            self._console.print(f"\n[green]Removed {removed_count} duplicate files.[/green]")

    def render_report(
        self,
        stats: dict[str, Any],
        groups: dict[str, DuplicateGroup],
        total_wasted: int,
    ) -> None:
        table = Table(title="Duplicate Report")
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right")
        table.add_row("Duplicate groups", str(len(groups)))
        table.add_row("Total files scanned", str(stats.get("total_files", "?")))
        table.add_row("Total duplicate files", str(stats.get("duplicate_files", "?")))
        table.add_row("Wasted space", _format_size(total_wasted))
        self._console.print(table)

    def render_message(self, level: MessageLevel, message: str) -> None:
        validated = _validate_level(level)
        style = _LEVEL_RICH_STYLE[validated]
        self._console.print(f"[{style}]{message}[/{style}]")


# ---------------------------------------------------------------------------
# JsonRenderer — buffered single-document envelope
# ---------------------------------------------------------------------------


class JsonRenderer:
    """Buffer all calls; emit one JSON document on :meth:`end`.

    Stdout receives only the JSON envelope. Per spec §2.1, banner / warnings
    / errors go to stderr as plain lines so ``--format=json | jq`` works.
    """

    def __init__(
        self,
        stream: IO[str] | None = None,
        stderr: IO[str] | None = None,
    ) -> None:
        self._stream: IO[str] = stream if stream is not None else sys.stdout
        self._stderr: IO[str] = stderr if stderr is not None else sys.stderr
        self._command: str | None = None
        self._groups_payload: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []
        self._summary: dict[str, Any] = {}
        self._began: bool = False

    def begin(self, command: str) -> None:
        self._command = command
        self._began = True

    def end(self) -> None:
        # ``end`` without ``begin`` is a no-op (matches stdout idle behavior).
        if not self._began:
            return
        envelope: dict[str, Any] = {
            "version": 1,
            "command": self._command,
        }
        if self._groups_payload:
            envelope["groups"] = self._groups_payload
        if self._actions:
            envelope["actions"] = self._actions
        if self._summary:
            envelope["summary"] = self._summary
        json.dump(envelope, self._stream)
        # Reset for potential reuse.
        self._began = False
        self._groups_payload = []
        self._actions = []
        self._summary = {}
        self._command = None

    def status(self, message: str) -> AbstractContextManager[None]:
        # Status spinners are visual; JSON renderer must not pollute stdout.
        del message
        return contextlib.nullcontext()

    def render_groups_header(self, count: int) -> None:
        # The group count is implicit in the JSON ``groups`` array length;
        # don't add a redundant "header" key.
        del count

    def render_groups(self, groups: dict[str, DuplicateGroup]) -> None:
        for hash_val, group in groups.items():
            self._groups_payload.append(
                {
                    "hash": hash_val,
                    "count": group.count,
                    "total_size": group.total_size,
                    "wasted_space": group.wasted_space,
                    "files": [str(f.path) for f in group.files],
                }
            )

    def render_resolve_action(
        self,
        action: ResolveAction,
        path: Path,
        error: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {"action": action, "path": str(path)}
        if error is not None:
            entry["error"] = error
        self._actions.append(entry)

    def render_resolve_summary(self, removed_count: int, dry_run: bool) -> None:
        self._summary = {"removed_count": removed_count, "dry_run": dry_run}

    def render_report(
        self,
        stats: dict[str, Any],
        groups: dict[str, DuplicateGroup],
        total_wasted: int,
    ) -> None:
        self._summary = {
            "total_files": stats.get("total_files"),
            "duplicate_files": stats.get("duplicate_files"),
            "duplicate_groups": len(groups),
            "total_wasted": total_wasted,
        }

    def render_message(self, level: MessageLevel, message: str) -> None:
        validated = _validate_level(level)
        # Per spec: warnings / errors go to stderr (visible), not stdout JSON.
        self._stderr.write(f"{validated}: {message}\n")


# ---------------------------------------------------------------------------
# PlainRenderer — line-oriented, no colors
# ---------------------------------------------------------------------------


class PlainRenderer:
    """Line-oriented, no colors, no tables. Pipe-friendly."""

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream: IO[str] = stream if stream is not None else sys.stdout

    def _write(self, text: str) -> None:
        self._stream.write(text)
        if not text.endswith("\n"):
            self._stream.write("\n")

    def begin(self, command: str) -> None:
        del command

    def end(self) -> None:
        return

    def status(self, message: str) -> AbstractContextManager[None]:
        del message
        return contextlib.nullcontext()

    def render_groups_header(self, count: int) -> None:
        self._write(f"Found {count} duplicate groups.")

    def render_groups(self, groups: dict[str, DuplicateGroup]) -> None:
        for hash_val, group in groups.items():
            self._write(f"{hash_val}:")
            for fmeta in group.files:
                # TAB indent + path + size; awk-friendly.
                self._write(f"\t{fmeta.path}\t{fmeta.size}")

    def render_resolve_action(
        self,
        action: ResolveAction,
        path: Path,
        error: str | None = None,
    ) -> None:
        if action == "error":
            self._write(f"error: {path}: {error}")
        else:
            # 'removed' / 'would_remove'
            self._write(f"{action}: {path}")

    def render_resolve_summary(self, removed_count: int, dry_run: bool) -> None:
        self._write(f"removed_count: {removed_count}")
        self._write(f"dry_run: {str(dry_run).lower()}")

    def render_report(
        self,
        stats: dict[str, Any],
        groups: dict[str, DuplicateGroup],
        total_wasted: int,
    ) -> None:
        self._write(f"total_files: {stats.get('total_files', 0)}")
        self._write(f"duplicate_files: {stats.get('duplicate_files', 0)}")
        self._write(f"duplicate_groups: {len(groups)}")
        self._write(f"total_wasted: {total_wasted}")

    def render_message(self, level: MessageLevel, message: str) -> None:
        validated = _validate_level(level)
        self._write(f"{validated}: {message}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_renderer(format: str) -> Renderer:
    """Construct a :class:`Renderer` for the given format string.

    Accepts ``rich``, ``json``, or ``plain`` (case-insensitive). Anything else
    raises :class:`ValueError`. The factory does not bind a console / stream;
    each renderer uses its own default (terminal, ``sys.stdout``).
    """
    normalized = format.lower()
    if normalized == "rich":
        return RichRenderer()
    if normalized == "json":
        return JsonRenderer()
    if normalized == "plain":
        return PlainRenderer()
    raise ValueError(
        f"Unknown format {format!r}; expected one of: " + ", ".join(_VALID_FORMATS)
    )


__all__ = [
    "JsonRenderer",
    "MessageLevel",
    "PlainRenderer",
    "Renderer",
    "ResolveAction",
    "RichRenderer",
    "make_renderer",
]
