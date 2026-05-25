"""Organize and preview CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel

from cli.path_validation import resolve_cli_path, validate_pair
from cli.state import _get_state
from core.types import OrganizationResult

console = Console()

# Worker auto-default cap (#408). The previous None → cpu_count() path
# over-provisioned on multi-core machines (an M-series Mac with 16 cores
# was launching 16 simultaneous Ollama requests, saturating the model
# server). The ceiling is the upper bound regardless of host.
_AUTO_WORKERS_CEILING: int = 4

# Local providers serve at most one generation per model instance unless
# their server is configured otherwise. We honour the server-side cap so
# extra client workers can't queue behind a single in-flight inference
# and trigger the dispatcher's timeout-abandonment path (#396 + #408).
_LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama", "llama_cpp", "mlx"})


def _ollama_num_parallel() -> int:
    """Read OLLAMA_NUM_PARALLEL from env, defaulting to 1 (Ollama's own default).

    Lives behind a helper so tests can patch it. A non-int / negative
    value falls back to 1; the env var is the user's contract with the
    Ollama server, and we mirror that here rather than override it.
    """
    raw = os.environ.get("OLLAMA_NUM_PARALLEL", "1")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _auto_worker_default() -> int:
    """Resolve worker count when --workers / --max-workers is omitted (#408).

    Picks the minimum of three caps:

    1. ``_AUTO_WORKERS_CEILING`` (4) — never exceed the inference
       backend's reasonable queue depth.
    2. ``cpu_count() // 2`` — leave headroom for the OS + Ollama process
       on the same host.
    3. For local providers (ollama / llama_cpp / mlx),
       ``OLLAMA_NUM_PARALLEL`` (default 1) — match the server's
       configured parallelism so extra client threads don't pile up
       behind a single in-flight inference. Remote providers (openai /
       claude) skip this cap because they handle concurrency
       server-side with rate limits.
    """
    cpu = os.cpu_count() or 1
    cap = min(_AUTO_WORKERS_CEILING, max(1, cpu // 2))

    # Lazy-import to avoid pulling provider_env (and its loguru chain)
    # at CLI startup for `fo version` etc. (#404 fast-path).
    try:
        from config.provider_env import get_current_provider

        provider = get_current_provider()
    except Exception:
        # If provider detection fails, treat as local/Ollama (the safer
        # default — capping at 1 won't slow remote-provider users much
        # and prevents the Ollama-flood case the issue called out).
        provider = "ollama"

    if provider in _LOCAL_PROVIDERS:
        cap = min(cap, _ollama_num_parallel())
    return max(1, cap)


def _organize_result_to_json(result: OrganizationResult) -> dict[str, Any]:
    """Serialize an ``OrganizationResult`` for ``fo organize --json`` (#412).

    The ``skipped_by_extension`` Counter is rendered as a plain dict for
    JSON consumers; key order follows ``Counter.most_common()`` so the
    payload itself reflects the breakdown ranking.
    """
    return {
        "total_files": result.total_files,
        "processed_files": result.processed_files,
        "skipped_files": result.skipped_files,
        "failed_files": result.failed_files,
        "deduplicated_files": result.deduplicated_files,
        "processing_time": result.processing_time,
        "skipped_by_extension": dict(result.skipped_by_extension.most_common()),
        "errors": [{"file": file_str, "error": err} for file_str, err in result.errors],
    }


def _emit_organize_json(result: OrganizationResult) -> None:
    """Emit the JSON payload for ``fo organize --json``.

    Printed via the stdlib ``print`` so the output stays parseable; Rich
    markup tags would otherwise leak escape codes into the JSON payload.
    """
    print(json.dumps(_organize_result_to_json(result), indent=2))


def _check_setup_completed() -> bool:
    """Check if the initial setup wizard has been completed.

    Returns:
        True if setup is complete, False otherwise.

    Raises:
        typer.Exit: With code 1 if setup is not completed.
    """
    from config.manager import ConfigManager

    config_manager = ConfigManager()
    config = config_manager.load()

    if not config.setup_completed:
        console.print()
        console.print(
            Panel.fit(
                "[bold yellow]First-time setup required[/bold yellow]\n\n"
                "File Organizer needs to be configured before use.\n"
                "Run the setup wizard to get started:\n\n"
                "  [bold cyan]fo setup[/bold cyan]\n\n"
                "This will detect your system capabilities and configure\n"
                "the optimal AI models for your hardware.",
                border_style="yellow",
            )
        )
        console.print()
        raise typer.Exit(code=1)

    return True


def _resolve_parallel_settings(
    sequential: bool,
    max_workers: int | None,
    prefetch_depth: int,
    no_prefetch: bool = False,
) -> tuple[int, int]:
    """Validate and resolve parallel worker/prefetch settings (#408).

    When ``max_workers`` is ``None`` (the user didn't pass
    ``--workers`` / ``--max-workers``), we now resolve to
    ``min(4, max(1, cpu_count() // 2))`` instead of letting the
    parallel processor fall through to ``os.cpu_count()``. The previous
    default over-provisioned on multi-core hosts and saturated the
    inference backend's queue.

    Args:
        sequential: Whether to force single-worker sequential processing.
        max_workers: Requested worker count, or ``None`` for the auto
            default (see ``_auto_worker_default``).
        prefetch_depth: Requested prefetch queue depth.
        no_prefetch: Backward-compatible alias for prefetch_depth=0.

    Returns:
        Tuple of (resolved_workers, resolved_prefetch_depth). The first
        element is always a concrete ``int``; callers no longer have to
        handle the ``None`` case.

    Raises:
        typer.Exit: With code 2 if --sequential and --max-workers > 1 conflict.
    """
    if sequential and max_workers not in (None, 1):
        console.print("[red]Error: --sequential cannot be combined with --max-workers > 1[/red]")
        raise typer.Exit(code=2)
    if sequential:
        resolved = 1
    elif max_workers is None:
        resolved = _auto_worker_default()
    else:
        resolved = max_workers
    return (resolved, 0 if (sequential or no_prefetch) else prefetch_depth)


def _resolve_timeout_per_file(flag_value: float | None) -> float:
    """Pick the effective per-file timeout (#396).

    Resolution order:
    1. ``--timeout-per-file`` flag value when the user passed it.
    2. ``AppConfig.processing.timeout_per_file`` from the persisted config.
    3. The ProcessingSettings dataclass default (300.0) when no config is
       on disk.

    Config-loading errors degrade silently to the dataclass default; the
    organizer's own __init__ guard will still reject any non-positive value.
    """
    if flag_value is not None:
        return flag_value
    try:
        from config.manager import ConfigManager

        manager = ConfigManager()
        app_config = manager.load()
        return app_config.processing.timeout_per_file
    except Exception:
        from config.schema import ProcessingSettings

        return ProcessingSettings().timeout_per_file


def organize(
    input_dir: Path = typer.Argument(..., help="Directory containing files to organize."),
    output_dir: Path = typer.Argument(..., help="Destination directory for organized files."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without moving files."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
    max_workers: int | None = typer.Option(
        None,
        "--max-workers",
        "--workers",
        min=1,
        help=(
            "Number of parallel workers for file processing. When omitted, "
            "defaults to min(4, cpu_count() // 2, OLLAMA_NUM_PARALLEL) for "
            "local providers (ollama / llama_cpp / mlx) and "
            "min(4, cpu_count() // 2) for remote providers (openai / claude). "
            "If your local Ollama server is configured with `OLLAMA_NUM_PARALLEL=N` "
            "you can safely pass `--workers N` for matching parallelism. "
            "`--workers` is an alias for `--max-workers`. (#408)"
        ),
    ),
    sequential: bool = typer.Option(
        False,
        "--sequential",
        help="Force single-worker sequential processing.",
    ),
    no_vision: bool = typer.Option(
        False,
        "--no-vision",
        "--text-only",
        help="Disable vision model usage and organize images by extension fallback.",
    ),
    prefetch_depth: int = typer.Option(
        2,
        "--prefetch-depth",
        min=0,
        help=(
            "Task scheduling prefetch depth per worker (0 disables queue-ahead and "
            "uses strictly sequential submission)."
        ),
    ),
    no_prefetch: bool = typer.Option(
        False,
        "--no-prefetch",
        help="Backward-compatible alias for --prefetch-depth 0.",
    ),
    transcribe_audio: bool = typer.Option(
        False,
        "--transcribe-audio",
        help=(
            "Transcribe audio files (requires the [media] extra) and use the "
            "transcript for content-aware categorization. Off by default — "
            "transcription is the expensive operation in the audio pipeline."
        ),
    ),
    max_transcribe_seconds: float = typer.Option(
        600.0,
        "--max-transcribe-seconds",
        min=0.0,
        help=(
            "Skip transcription for audio files longer than this (seconds). "
            "Default: 600 (10 min). Set to 0 to disable the cap entirely."
        ),
    ),
    timeout_per_file: float | None = typer.Option(
        None,
        "--timeout-per-file",
        min=1.0,
        help=(
            "Per-file processing timeout in seconds. When omitted, the value from "
            "AppConfig.processing.timeout_per_file is used (default: 300). Values "
            "below ~60s tend to false-positive on vision models running large "
            "images; values above ~600s reduce the protection against genuine "
            "hangs. Persistent: `fo config set processing.timeout_per_file 600`."
        ),
    ),
    show_skipped: bool = typer.Option(
        False,
        "--show-skipped",
        help=(
            "Print the full breakdown of skipped extensions instead of the "
            "default top-10 preview. Useful when triaging which formats to "
            "support next."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit the run summary as JSON on stdout (includes "
            "`skipped_by_extension`). Suppresses the interactive Rich "
            "summary so the payload stays machine-parseable."
        ),
    ),
) -> None:
    """Organize files in a directory using AI models."""
    # First-run setup gate now lives in `cli.main.main_callback` and runs
    # for every non-allowlisted command. The previous inline call here
    # is removed (Step 3); leaving both would double-print the panel.

    # A.cli: resolve + validate both path args before any filesystem work.
    # Input must exist and be a dir; output may not exist yet (the
    # organizer creates it), but when it does exist it must be a dir.
    input_dir = resolve_cli_path(input_dir, must_exist=True, must_be_dir=True)
    output_dir = resolve_cli_path(output_dir, must_exist=False, must_be_dir=True)
    validate_pair(input_dir, output_dir)

    if not json_output:
        console.print(f"[bold]Organizing[/bold] {input_dir} -> {output_dir}")
        if dry_run or _get_state().dry_run:
            console.print("[yellow]Dry run mode — no files will be moved.[/yellow]")
    resolved_workers, resolved_prefetch_depth = _resolve_parallel_settings(
        sequential, max_workers, prefetch_depth, no_prefetch
    )

    try:
        from core.organizer import FileOrganizer

        effective_timeout_per_file = _resolve_timeout_per_file(timeout_per_file)
        organizer = FileOrganizer(
            dry_run=dry_run or _get_state().dry_run,
            parallel_workers=resolved_workers,
            prefetch_depth=resolved_prefetch_depth,
            enable_vision=not no_vision,
            no_prefetch=no_prefetch,
            transcribe_audio=transcribe_audio,
            # `--max-transcribe-seconds 0` is the documented "disable the cap"
            # value; convert to None for the organizer (None means uncapped).
            max_transcribe_seconds=max_transcribe_seconds if max_transcribe_seconds > 0 else None,
            timeout_per_file=effective_timeout_per_file,
        )
        if json_output:
            # Silence the Rich progress + summary by swapping in a no-op
            # console; the JSON payload is the only thing on stdout.
            organizer.console = Console(quiet=True)
        result = organizer.organize(input_dir, output_dir, show_skipped=show_skipped)
        if json_output:
            _emit_organize_json(result)
        else:
            console.print(
                f"[green]Done:[/green] {result.processed_files} processed, "
                f"{result.skipped_files} skipped, {result.failed_files} failed"
            )
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        # Step 3: surface the full Rich traceback when --debug is set so
        # beta testers can attach actionable repro info to bug reports.
        # Without --debug, only the red one-liner shows (current behavior).
        if _get_state().debug:
            console.print_exception(show_locals=False)
        raise typer.Exit(code=1) from exc


def preview(
    input_dir: Path = typer.Argument(..., help="Directory to preview."),
    max_workers: int | None = typer.Option(
        None,
        "--max-workers",
        "--workers",
        min=1,
        help=(
            "Number of parallel workers for file processing. Auto-default "
            "min(4, cpu_count() // 2) (#408). `--workers` is an alias."
        ),
    ),
    sequential: bool = typer.Option(
        False,
        "--sequential",
        help="Force single-worker sequential processing.",
    ),
    no_vision: bool = typer.Option(
        False,
        "--no-vision",
        "--text-only",
        help="Disable vision model usage and organize images by extension fallback.",
    ),
    prefetch_depth: int = typer.Option(
        2,
        "--prefetch-depth",
        min=0,
        help=(
            "Task scheduling prefetch depth per worker (0 disables queue-ahead and "
            "uses strictly sequential submission)."
        ),
    ),
    no_prefetch: bool = typer.Option(
        False,
        "--no-prefetch",
        help="Backward-compatible alias for --prefetch-depth 0.",
    ),
    transcribe_audio: bool = typer.Option(
        False,
        "--transcribe-audio",
        help=(
            "Transcribe audio files (requires the [media] extra) and use the "
            "transcript for content-aware categorization. Off by default."
        ),
    ),
    max_transcribe_seconds: float = typer.Option(
        600.0,
        "--max-transcribe-seconds",
        min=0.0,
        help=(
            "Skip transcription for audio files longer than this (seconds). "
            "Default: 600 (10 min). Set to 0 to disable the cap entirely."
        ),
    ),
    timeout_per_file: float | None = typer.Option(
        None,
        "--timeout-per-file",
        min=1.0,
        help=(
            "Per-file processing timeout in seconds. When omitted, the value "
            "from AppConfig.processing.timeout_per_file is used (default: 300). "
            "Issue #396 — tune to your hardware + model. Persistent: "
            "`fo config set processing.timeout_per_file 600`."
        ),
    ),
) -> None:
    """Preview how files would be organized (dry-run)."""
    # Setup gate moved to `cli.main.main_callback` (Step 3).

    # A.cli: single-path validation — preview never writes, so no
    # output-dir pair check needed.
    input_dir = resolve_cli_path(input_dir, must_exist=True, must_be_dir=True)

    console.print(f"[bold]Previewing[/bold] {input_dir}")
    resolved_workers, resolved_prefetch_depth = _resolve_parallel_settings(
        sequential, max_workers, prefetch_depth, no_prefetch
    )

    try:
        from core.organizer import FileOrganizer

        effective_timeout_per_file = _resolve_timeout_per_file(timeout_per_file)
        organizer = FileOrganizer(
            dry_run=True,
            parallel_workers=resolved_workers,
            prefetch_depth=resolved_prefetch_depth,
            enable_vision=not no_vision,
            no_prefetch=no_prefetch,
            transcribe_audio=transcribe_audio,
            max_transcribe_seconds=max_transcribe_seconds if max_transcribe_seconds > 0 else None,
            timeout_per_file=effective_timeout_per_file,
        )
        result = organizer.organize(input_dir, input_dir)
        console.print(f"[green]Preview:[/green] {result.total_files} files would be organized")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        if _get_state().debug:
            console.print_exception(show_locals=False)
        raise typer.Exit(code=1) from exc
