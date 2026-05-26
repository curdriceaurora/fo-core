"""File type dispatch and per-type processing pipelines.

Routes files to the appropriate processor (text, image, audio, video)
and handles progress display for each batch.  Extracted from
``organizer.py`` to separate processing logic from orchestration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from rich.console import Console

from core.display import create_progress
from core.types import (
    AUDIO_FALLBACK_FOLDER,
    ERROR_FALLBACK_FOLDER,
    VIDEO_FALLBACK_FOLDER,
)
from parallel.processor import ParallelProcessor
from services import ProcessedFile, ProcessedImage, TextProcessor, VisionProcessor

if TYPE_CHECKING:
    from services.audio.metadata_extractor import AudioMetadata, AudioMetadataExtractor
    from services.video.metadata_extractor import VideoMetadataExtractor


def _maybe_transcribe(
    audio_path: Path,
    *,
    metadata: AudioMetadata,
    transcriber: Any | None,
    max_transcribe_seconds: float | None,
) -> str | None:
    """Return a transcript when a transcriber is set and duration is within cap.

    Returns ``None`` for any of:
    - No transcriber configured (the default; metadata-only categorization).
    - Duration exceeds ``max_transcribe_seconds`` (skip and warn — long files
      would dominate the organize wall-clock time).
    - The transcriber raises a recoverable exception (FileNotFound,
      RuntimeError, ImportError); we degrade to metadata-only categorization
      rather than aborting the entire organize batch on a single bad file.
    """
    if transcriber is None:
        return None
    # Defensive guard: a duck-typed transcriber that doesn't expose
    # `generate(audio_path)` would otherwise raise AttributeError and abort
    # the per-file dispatcher loop. Treat invalid transcribers the same as
    # missing — degrade to metadata-only with a warning.
    if not callable(getattr(transcriber, "generate", None)):
        logger.warning(
            "Invalid transcriber for {} (missing generate()); using metadata only.",
            audio_path.name,
        )
        return None
    duration = getattr(metadata, "duration", None)
    if (
        max_transcribe_seconds is not None
        and isinstance(duration, (int, float))
        and duration > max_transcribe_seconds
    ):
        logger.warning(
            "Audio {} exceeds transcribe cap ({:.1f}s > {:.1f}s); using metadata only.",
            audio_path.name,
            float(duration),
            float(max_transcribe_seconds),
        )
        return None
    try:
        result = transcriber.generate(str(audio_path))
    except (FileNotFoundError, OSError, ValueError, RuntimeError, ImportError) as exc:
        # OSError + ValueError cover malformed / unsupported audio
        # (faster-whisper / ctranslate2 surface decode failures via these).
        # Without them the exception escapes to the outer per-file handler
        # and marks the file as failed in AUDIO_FALLBACK_FOLDER, regressing
        # a file that's otherwise classifiable from metadata alone. Treat
        # transcription as a best-effort enhancement: degrade to
        # metadata-only categorization on any recoverable failure.
        logger.warning("Audio transcription failed for {}: {}", audio_path.name, exc)
        return None
    # `transcriber` is typed as `Any` so mypy can't see `generate`'s return
    # type; cast to str to satisfy the no-any-return gate. AudioModel.generate
    # is contracted to return str (verified in `models/audio_model.py:generate`).
    return str(result)


def _to_transcription_result(transcript: str | None, metadata: AudioMetadata) -> Any:
    """Wrap a plain transcript string for `AudioClassifier.classify(transcription=...)`.

    The classifier's Phase 3 keyword/speaker scoring expects a
    ``TranscriptionResult`` dataclass with ``.text``, ``.duration``, and
    ``.segments``. ``AudioModel.generate`` returns a plain ``str``, so we
    construct a minimal stand-in here. ``segments=[]`` disables the
    segment-based speaker-count heuristic; that's intentional — without
    real word-level timestamps we'd be inventing signal.

    Returns ``None`` when ``transcript`` is missing/empty so the
    classifier's existing ``if transcription is not None`` guard skips
    the transcription phase cleanly.
    """
    if not transcript:
        return None
    from services.audio.transcriber import TranscriptionOptions, TranscriptionResult

    return TranscriptionResult(
        text=transcript,
        segments=[],
        language="",
        language_confidence=0.0,
        duration=getattr(metadata, "duration", 0.0),
        options=TranscriptionOptions(),
    )


def process_text_files(
    files: list[Path],
    text_processor: TextProcessor,
    parallel_processor: ParallelProcessor,
    console: Console,
) -> list[ProcessedFile]:
    """Process text files through the AI text model.

    Args:
        files: Text file paths to process.
        text_processor: Initialized text processor.
        parallel_processor: Parallel processing engine.
        console: Rich console for progress output.

    Returns:
        List of processed file results.
    """
    processed: list[ProcessedFile] = []

    with create_progress(console) as progress:
        task = progress.add_task("Processing files...", total=len(files))

        def _process_one(path: Path) -> ProcessedFile:
            """Single-file processor closure passed to the parallel batch runner."""
            return text_processor.process_file(path)

        for file_result in parallel_processor.process_batch_iter(files, _process_one):
            if file_result.success:
                result = file_result.result
                processed.append(result)
                if not result.error:
                    progress.update(
                        task,
                        advance=1,
                        description=f"[green]✓[/green] {file_result.path.name}",
                    )
                else:
                    progress.update(
                        task,
                        advance=1,
                        description=f"[red]✗[/red] {file_result.path.name} (Error)",
                    )
            else:
                error_msg = file_result.error or "Unknown error"
                logger.error("Failed to process {}: {}", file_result.path, error_msg)
                processed.append(
                    ProcessedFile(
                        file_path=file_result.path,
                        description="",
                        folder_name=ERROR_FALLBACK_FOLDER,
                        filename=file_result.path.stem,
                        error=error_msg,
                        # #409: dispatcher-built failures must surface
                        # in the "Review recommended" section.
                        confidence=0.0,
                    )
                )
                progress.update(
                    task,
                    advance=1,
                    description=f"[red]✗[/red] {file_result.path.name} (Failed)",
                )

    return processed


def _is_timeout_error(error_msg: str) -> bool:
    """Match the dispatcher's timeout-error sentinel.

    The parallel processor emits ``"Timed out after Xs"`` (see
    ``parallel/processor.py``) when it abandons a long-running task.
    Other errors (read failures, corrupt files) take the regular failure
    path. Match by prefix so the timing-suffix doesn't have to be exact.
    """
    return error_msg.startswith("Timed out after")


def process_image_files(
    files: list[Path],
    vision_processor: VisionProcessor,
    parallel_processor: ParallelProcessor,
    console: Console,
) -> list[ProcessedImage]:
    """Process image files through the AI vision model.

    Args:
        files: Image file paths to process.
        vision_processor: Initialized vision processor.
        parallel_processor: Parallel processing engine.
        console: Rich console for progress output.

    Returns:
        List of processed image results.
    """
    processed: list[ProcessedImage] = []

    with create_progress(console) as progress:
        task = progress.add_task("Processing images...", total=len(files))

        def _process_one_image(path: Path) -> ProcessedImage:
            """Single-image processor closure passed to the parallel batch runner."""
            return vision_processor.process_file(path)

        for file_result in parallel_processor.process_batch_iter(files, _process_one_image):
            if file_result.success:
                result = file_result.result
                processed.append(result)
                if not result.error:
                    progress.update(
                        task,
                        advance=1,
                        description=f"[green]✓[/green] {file_result.path.name}",
                    )
                else:
                    progress.update(
                        task,
                        advance=1,
                        description=f"[red]✗[/red] {file_result.path.name} (Error)",
                    )
            else:
                error_msg = file_result.error or "Unknown error"
                # #406: vision timeouts go through the metadata fallback path
                # instead of being dropped into the error bucket. Other
                # failures (read error, corrupt image, …) still error-out.
                if _is_timeout_error(error_msg):
                    from services.vision_fallback import compute_fallback

                    fb = compute_fallback(file_result.path)
                    logger.info(
                        "Vision timed out for {}; categorized via {} → {}",
                        file_result.path.name,
                        fb.source,
                        fb.folder,
                    )
                    # Per-source confidence (#409). The vision model
                    # never actually classified this file; we're going
                    # off metadata. EXIF dates are more trustworthy
                    # than pure filename heuristics, so they earn a
                    # slightly higher score. Both land below the
                    # default 0.5 threshold and so surface in the
                    # summary's "Review recommended" section.
                    _fallback_confidence = 0.5 if fb.source == "fallback_exif" else 0.3
                    processed.append(
                        ProcessedImage(
                            file_path=file_result.path,
                            description="",
                            folder_name=fb.folder,
                            filename=fb.filename,
                            source=fb.source,
                            # Carry the timeout's wall-clock through so the
                            # #410 summary's p95/p99 reflect this image's
                            # real worst-case latency. Without this, the
                            # slowest attempts in a run are silently
                            # excluded from the percentile sample set and
                            # the observability output understates tail
                            # latency (CodeRabbit P2 on PR #424).
                            inference_ms=file_result.duration_ms,
                            confidence=_fallback_confidence,
                            # NB: no `error` field — the file is not a failure
                        )
                    )
                    progress.update(
                        task,
                        advance=1,
                        description=f"[yellow]⚠[/yellow] {file_result.path.name} (fallback)",
                    )
                else:
                    logger.error("Failed to process {}: {}", file_result.path, error_msg)
                    processed.append(
                        ProcessedImage(
                            file_path=file_result.path,
                            description="",
                            folder_name=ERROR_FALLBACK_FOLDER,
                            filename=file_result.path.stem,
                            error=error_msg,
                            # #409: dispatcher-built failures must
                            # surface in the "Review recommended"
                            # section. Default confidence=1.0 would
                            # hide them.
                            confidence=0.0,
                        )
                    )
                    progress.update(
                        task,
                        advance=1,
                        description=f"[red]✗[/red] {file_result.path.name} (Failed)",
                    )

    return processed


def process_audio_files(
    files: list[Path],
    *,
    extractor_cls: type[AudioMetadataExtractor] | None = None,
    transcriber: Any | None = None,
    max_transcribe_seconds: float | None = None,
) -> list[ProcessedFile]:
    """Process audio files using the metadata pipeline (no AI model required).

    Args:
        files: Audio file paths to process.
        extractor_cls: Optional extractor class override so organizer-level
            patch targets continue to intercept metadata extraction in tests.
        transcriber: Optional transcriber object exposing
            ``generate(audio_path: str) -> str`` (typically ``AudioModel``).
            When provided, each file within the duration cap is transcribed
            and the result attached to ``ProcessedFile.transcript`` for the
            organizer's text-categorization path. ``None`` preserves the
            metadata-only behavior.
        max_transcribe_seconds: Per-file duration cap; files longer than
            this skip transcription and fall back to metadata-only
            categorization. ``None`` (the default at this layer) means no
            cap — the CLI/organizer layer applies the 600s policy default
            and threads it down. Whisper is roughly 5-10× realtime on
            CPU, so a 600s cap keeps a single file under ~2 min of CPU
            work; use ``None`` here only when you've already gated
            duration upstream.

    Returns:
        List of processed file results.
    """
    from services.audio.classifier import AudioClassifier
    from services.audio.metadata_extractor import AudioMetadataExtractor
    from services.audio.organizer import AudioOrganizer

    extractor_type = extractor_cls or AudioMetadataExtractor
    extractor = extractor_type()
    classifier = AudioClassifier()
    organizer = AudioOrganizer()
    processed: list[ProcessedFile] = []

    for audio_path in files:
        try:
            metadata = extractor.extract(audio_path)

            # Transcribe FIRST so the result can influence classification.
            # Otherwise the user pays transcription cost and gets the same
            # metadata-only folder routing — defeating --transcribe-audio.
            transcript = _maybe_transcribe(
                audio_path,
                metadata=metadata,
                transcriber=transcriber,
                max_transcribe_seconds=max_transcribe_seconds,
            )
            transcription = _to_transcription_result(transcript, metadata)
            classification = classifier.classify(metadata, transcription=transcription)
            dest_path = organizer.generate_path(classification.audio_type, metadata)

            folder_name = dest_path.parent.as_posix()
            filename_stem = dest_path.stem

            parts = [classification.audio_type.value.capitalize()]
            if metadata.artist:
                parts.append(metadata.artist)
            if metadata.title:
                parts.append(metadata.title)
            description = (
                ": ".join(parts[:1]) + " " + " - ".join(parts[1:]) if len(parts) > 1 else parts[0]
            )

            processed.append(
                ProcessedFile(
                    file_path=audio_path,
                    description=description,
                    folder_name=folder_name,
                    filename=filename_stem,
                    error=None,
                    transcript=transcript,
                )
            )
            logger.debug("Audio processed: {} → {}/{}", audio_path.name, folder_name, filename_stem)

        except (OSError, ValueError, KeyError, RuntimeError, ImportError) as exc:
            logger.warning("Audio metadata extraction failed for {}: {}", audio_path.name, exc)
            processed.append(
                ProcessedFile(
                    file_path=audio_path,
                    description="",
                    folder_name=AUDIO_FALLBACK_FOLDER,
                    filename=audio_path.stem,
                    error=str(exc),
                    confidence=0.0,  # #409: surface in review list
                )
            )

    return processed


def process_video_files(
    files: list[Path],
    *,
    extractor_cls: type[VideoMetadataExtractor] | None = None,
) -> list[ProcessedFile]:
    """Process video files using the metadata pipeline (no AI model required).

    Args:
        files: Video file paths to process.
        extractor_cls: Optional extractor class override so organizer-level
            patch targets continue to intercept metadata extraction in tests.

    Returns:
        List of processed file results.
    """
    from services.video.metadata_extractor import VideoMetadataExtractor
    from services.video.organizer import VideoOrganizer

    extractor_type = extractor_cls or VideoMetadataExtractor
    extractor = extractor_type()
    organizer = VideoOrganizer()
    processed: list[ProcessedFile] = []

    for video_path in files:
        try:
            metadata = extractor.extract(video_path)
            folder_name, filename_stem = organizer.generate_path(metadata)
            description = organizer.generate_description(metadata)

            processed.append(
                ProcessedFile(
                    file_path=video_path,
                    description=description,
                    folder_name=folder_name,
                    filename=filename_stem,
                    error=None,
                )
            )
            logger.debug("Video processed: {} → {}/{}", video_path.name, folder_name, filename_stem)

        except FileNotFoundError as exc:
            logger.warning("Video file not found: {}: {}", video_path.name, exc)
            processed.append(
                ProcessedFile(
                    file_path=video_path,
                    description="",
                    folder_name=VIDEO_FALLBACK_FOLDER,
                    filename=video_path.stem,
                    error=str(exc),
                    confidence=0.0,  # #409: surface in review list
                )
            )
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            logger.warning("Video metadata extraction failed for {}: {}", video_path.name, exc)
            processed.append(
                ProcessedFile(
                    file_path=video_path,
                    description="",
                    folder_name=VIDEO_FALLBACK_FOLDER,
                    filename=video_path.stem,
                    error=str(exc),
                    confidence=0.0,  # #409: surface in review list
                )
            )

    return processed
