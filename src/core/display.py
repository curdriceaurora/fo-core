# pyre-ignore-all-errors
"""Rich UI display helpers for the file organizer.

Provides progress bar creation, file-type breakdown tables, and
summary output. Extracted from ``organizer.py`` to separate
presentation concerns from orchestration logic.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from core.types import OrganizationResult

# Top-N cap for the unsupported-extension breakdown rendered in the
# summary. Beyond N entries the remaining tail is summarized in a hint
# line that points users at ``--show-skipped`` for the full list.
TOP_SKIPPED_EXTENSIONS: int = 10


def show_file_breakdown(
    console: Console,
    *,
    text_files: list[Path],
    image_files: list[Path],
    video_files: list[Path],
    audio_files: list[Path],
    cad_files: list[Path],
    other_files: list[Path],
) -> None:
    """Show a Rich table with file type counts."""
    table = Table(title="File Type Breakdown", show_header=True)
    table.add_column("Type", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_column("Status", style="yellow")

    table.add_row("Text files", str(len(text_files)), "✓ Will process")
    table.add_row("Images", str(len(image_files)), "✓ Will process")
    table.add_row("Videos", str(len(video_files)), "✓ Will process (metadata)")
    table.add_row("Audio", str(len(audio_files)), "✓ Will process (metadata)")
    table.add_row("CAD files", str(len(cad_files)), "✓ Will process")
    table.add_row("Other", str(len(other_files)), "⊘ Skip (unsupported)")

    console.print(table)


def show_summary(
    console: Console,
    result: OrganizationResult,
    output_path: Path,
    *,
    dry_run: bool,
    show_skipped: bool = False,
) -> None:
    """Show final organization summary.

    Args:
        console: Rich console to print to.
        result: Aggregate organize result.
        output_path: Destination directory (printed in the structure section).
        dry_run: When True, append the dry-run reminder banner.
        show_skipped: When True, print every entry of
            ``result.skipped_by_extension`` instead of the top-N preview.
            Wired to ``--show-skipped`` on the ``fo organize`` command.
    """
    console.print("\n" + "=" * 70)
    console.print("[bold green]Organization Complete![/bold green]")
    console.print("=" * 70)

    console.print("\n[bold]Statistics:[/bold]")
    console.print(f"  Total files scanned: {result.total_files}")
    console.print(f"  [green]Processed: {result.processed_files}[/green]")
    console.print(f"  [yellow]Skipped: {result.skipped_files}[/yellow]")
    console.print(f"  [red]Failed: {result.failed_files}[/red]")
    if result.deduplicated_files:
        console.print(f"  [dim]Duplicates removed: {result.deduplicated_files}[/dim]")
    if result.errors:
        for file_str, err in result.errors[:10]:
            file_name = Path(file_str).name
            console.print(f"    [red]✗[/red] {file_name}: {err}")
        if len(result.errors) > 10:
            console.print(f"    ... and {len(result.errors) - 10} more")
    console.print(f"  Processing time: {result.processing_time:.2f}s")

    # Skipped-extension breakdown (#412). Render whenever anything was
    # skipped; --show-skipped expands past TOP_SKIPPED_EXTENSIONS.
    if result.skipped_by_extension:
        _render_skipped_extensions(console, result, show_skipped=show_skipped)

    if result.organized_structure:
        console.print("\n[bold]Organized Structure:[/bold]")
        console.print(f"[cyan]{output_path}/[/cyan]")

        for folder, files in sorted(result.organized_structure.items()):
            console.print(f"  [cyan]├── {folder}/[/cyan]")
            for i, filename in enumerate(sorted(files)):
                prefix = "└──" if i == len(files) - 1 else "├──"
                console.print(f"       {prefix} {filename}")

    if dry_run:
        console.print("\n[yellow]⚠️  DRY RUN - No files were actually moved[/yellow]")
        console.print("[dim]Run without --dry-run to perform actual organization[/dim]")
    else:
        console.print(f"\n[green]✓ Files organized in: {output_path}[/green]")


def _render_skipped_extensions(
    console: Console,
    result: OrganizationResult,
    *,
    show_skipped: bool,
) -> None:
    """Render the top-N (or full) breakdown of skipped extensions.

    Sorted by count descending, then by extension name ascending so the
    output is stable when several extensions share a count.
    """
    items = sorted(
        result.skipped_by_extension.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    total_distinct = len(items)

    if show_skipped or total_distinct <= TOP_SKIPPED_EXTENSIONS:
        header = "Skipped by extension"
        visible = items
        tail = 0
    else:
        header = f"Top {TOP_SKIPPED_EXTENSIONS} skipped extensions"
        visible = items[:TOP_SKIPPED_EXTENSIONS]
        tail = total_distinct - TOP_SKIPPED_EXTENSIONS

    console.print(f"\n[bold yellow]{header}:[/bold yellow]")
    for ext, count in visible:
        console.print(f"  [yellow]{ext}[/yellow]: {count}")
    if tail:
        console.print(f"  [dim]({tail} more — use --show-skipped for the full list)[/dim]")


def create_progress(console: Console) -> Progress:
    """Create a standard Rich progress bar."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    )
