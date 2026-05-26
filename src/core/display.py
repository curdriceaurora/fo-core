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
    if result.fallback_files:
        # #406: Vision timeouts that took the metadata-only path. They're
        # included in `processed_files` but flagged separately so the user
        # knows N placements are low-confidence and should be reviewed.
        console.print(
            f"  [yellow]Categorized via fallback "
            f"(review recommended): {result.fallback_files}[/yellow]"
        )
    if result.low_confidence_files:
        # #409: every file whose confidence score fell below the
        # configured threshold. Distinct from `fallback_files` —
        # fallbacks are one source of low confidence; vision /text
        # error returns (confidence=0.0) are another. Show a small
        # preview to keep the summary scannable; users can grep the
        # session log for `confidence=` to see the full audit trail.
        _preview = ", ".join(result.low_confidence_files[:5])
        if len(result.low_confidence_files) > 5:
            _preview += f", … (+{len(result.low_confidence_files) - 5} more)"
        console.print(
            f"  [yellow]Review recommended: "
            f"{len(result.low_confidence_files)} files[/yellow] "
            f"[dim]({_preview})[/dim]"
        )
    if result.deduplicated_files:
        console.print(f"  [dim]Duplicates removed: {result.deduplicated_files}[/dim]")
    if result.errors:
        for file_str, err in result.errors[:10]:
            file_name = Path(file_str).name
            console.print(f"    [red]✗[/red] {file_name}: {err}")
        if len(result.errors) > 10:
            console.print(f"    ... and {len(result.errors) - 10} more")
    console.print(f"  Processing time: {result.processing_time:.2f}s")

    # Structured error breakdown (#411). Renders only when any bucket
    # has > 0 entries — clean runs stay quiet. The recommendation
    # line fires per-bucket when that bucket exceeds 10% of the
    # total scanned files (issue #411 acceptance criterion).
    _render_error_breakdown(console, result)

    # Inference duration stats (#410). Vision and text are aggregated
    # separately so operators can spot a slow modality even when the
    # other is fine. Lines only render when the corresponding sample
    # list is non-empty (i.e. the run actually invoked that modality).
    _render_inference_stats(console, "Vision", result.vision_inference_ms_samples)
    _render_inference_stats(console, "Text", result.text_inference_ms_samples)

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


def _percentile(samples: list[float], p: float) -> float:
    """Return the *p*-th percentile (0 <= p <= 100) of *samples*.

    Uses linear interpolation between the closest ranks — equivalent to
    NumPy's default ``method="linear"`` but kept dependency-free so the
    summary renderer never needs to import NumPy. Returns 0.0 for an
    empty sample list so callers don't have to special-case it.
    """
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    ordered = sorted(samples)
    # Clamp p into [0, 100] then translate to a fractional index in
    # [0, len-1]. Linear interpolation between neighbours yields the
    # same percentile NumPy / pandas produce for the same data.
    p_clamped = max(0.0, min(100.0, p))
    rank = (p_clamped / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def _render_inference_stats(console: Console, label: str, samples: list[float]) -> None:
    """Render mean/p50/p95/p99 for an inference-time sample list (#410).

    Args:
        console: Rich console to print to.
        label: ``"Vision"`` or ``"Text"`` — used as the section header.
        samples: List of per-file inference durations in milliseconds.
            An empty list short-circuits — the section is omitted from
            the summary so runs that didn't invoke that modality stay
            uncluttered.
    """
    if not samples:
        return
    mean_ms = sum(samples) / len(samples)
    p50 = _percentile(samples, 50)
    p95 = _percentile(samples, 95)
    p99 = _percentile(samples, 99)
    console.print(
        f"  [dim]{label} inference — mean: {mean_ms / 1000:.2f}s, "
        f"p50: {p50 / 1000:.2f}s, p95: {p95 / 1000:.2f}s, "
        f"p99: {p99 / 1000:.2f}s (n={len(samples)})[/dim]"
    )


# Fraction of `total_files` above which a single error bucket gets a
# bolded recommendation line in the summary (#411 acceptance criterion).
_RECOMMENDATION_THRESHOLD: float = 0.10


def _render_error_breakdown(console: Console, result: OrganizationResult) -> None:
    """Render the per-category failure breakdown (#411).

    Walks ``result.error_breakdown`` in descending count order, prints
    one line per bucket with the count + one example basename, and
    fires a recommendation line for any bucket whose share of
    ``total_files`` exceeds ``_RECOMMENDATION_THRESHOLD``.

    Short-circuits when the breakdown is empty so clean runs stay
    quiet.
    """
    if not result.error_breakdown:
        return

    # ``error_breakdown`` is typed ``Counter[str]`` so dataclass init
    # stays simple. The organizer only ever writes keys produced by
    # ``classify_error()``, every one of which is also a key of
    # ``RECOMMENDATIONS`` — direct subscript is safe.
    from core.error_taxonomy import RECOMMENDATIONS

    console.print("\n[bold]Failure breakdown:[/bold]")
    items = sorted(
        result.error_breakdown.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    for category, count in items:
        example = result.error_examples.get(category, "<no example captured>")
        console.print(f"  [red]{category}[/red]: {count} [dim](e.g. {example})[/dim]")
        # Recommendation only fires when the bucket dominates the run.
        # Compare against total_files rather than failed_files so
        # categories that include vision-timeout fallbacks (which aren't
        # counted as failures) still trigger the bolded hint when they
        # represent >10% of the workload.
        if result.total_files and count / result.total_files > _RECOMMENDATION_THRESHOLD:
            console.print(f"    [bold yellow]→ {RECOMMENDATIONS[category]}[/bold yellow]")
