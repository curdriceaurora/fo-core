"""Commands for viewing and managing fo log files."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from config.path_manager import get_canonical_paths

console = Console()


def logs_command(
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
    """View or tail fo log files.

    By default, shows the last 50 lines of the main fo.log file.
    Use --session to view the most recent session log instead.
    Use --list to see all available session logs.

    Examples:
        fo logs                    # Show last 50 lines of fo.log
        fo logs --follow           # Tail fo.log (like tail -f)
        fo logs --session          # Show latest session log
        fo logs --list             # List all session logs
        fo logs --session --lines 100  # Show last 100 lines of latest session
    """
    try:
        paths = get_canonical_paths()
        log_dir = paths["logs"]

        if list_sessions:
            _list_session_logs(log_dir)
            return

        if session:
            log_file = _get_latest_session_log(log_dir)
            if log_file is None:
                console.print("[yellow]No session logs found.[/yellow]")
                raise typer.Exit(1)
        else:
            log_file = log_dir / "fo.log"
            if not log_file.exists():
                console.print(f"[yellow]Log file not found: {log_file}[/yellow]")
                raise typer.Exit(1)

        if follow:
            _tail_follow(log_file)
        else:
            _show_last_lines(log_file, lines)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        raise typer.Exit(130) from None
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


def _list_session_logs(log_dir: Path) -> None:
    """List all available session log files."""
    session_dir = log_dir / "sessions"
    if not session_dir.exists():
        console.print("[yellow]No session logs directory found.[/yellow]")
        return

    session_files = sorted(
        session_dir.glob("fo-*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    )

    if not session_files:
        console.print("[yellow]No session logs found.[/yellow]")
        return

    console.print(f"[bold]Session Logs[/bold] ({len(session_files)} found)\n")
    console.print(f"Location: {session_dir}\n")

    for log_file in session_files:
        # Extract timestamp from filename: fo-2026-05-23T12-34-56-abc123.log
        size_mb = log_file.stat().st_size / (1024 * 1024)
        console.print(f"  {log_file.name:60s}  {size_mb:6.2f} MB")


def _get_latest_session_log(log_dir: Path) -> Path | None:
    """Get the most recent session log file."""
    session_dir = log_dir / "sessions"
    if not session_dir.exists():
        return None

    session_files = list(session_dir.glob("fo-*.log"))
    if not session_files:
        return None

    # Sort by modification time, most recent first
    return max(session_files, key=lambda p: p.stat().st_mtime)


def _show_last_lines(log_file: Path, num_lines: int) -> None:
    """Show the last N lines of a log file."""
    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as f:
            # Read all lines and show the last num_lines
            lines = f.readlines()
            start_idx = max(0, len(lines) - num_lines)
            for line in lines[start_idx:]:
                # Write directly to stdout for proper CliRunner capture
                sys.stdout.write(line)
                sys.stdout.flush()
    except FileNotFoundError as e:
        console.print(f"[red]File not found: {log_file}[/red]")
        raise typer.Exit(1) from e
    except PermissionError as e:
        console.print(f"[red]Permission denied: {log_file}[/red]")
        raise typer.Exit(1) from e


def _tail_follow(log_file: Path) -> None:
    """Follow a log file like 'tail -f'."""
    import time

    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as f:
            # Seek to end of file
            f.seek(0, 2)
            console.print(f"[dim]Following {log_file} (Ctrl+C to stop)...[/dim]\n")

            while True:
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.1)  # Wait a bit before checking again
    except FileNotFoundError as e:
        console.print(f"[red]File not found: {log_file}[/red]")
        raise typer.Exit(1) from e
    except PermissionError as e:
        console.print(f"[red]Permission denied: {log_file}[/red]")
        raise typer.Exit(1) from e
