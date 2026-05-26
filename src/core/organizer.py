"""Main file organizer orchestrator (facade).

``FileOrganizer`` is the public API for the organize workflow.  It
delegates to extracted modules for specific concerns:

- ``core.types``: ``OrganizationResult`` and extension constants
- ``core.initializer``: Processor startup and dependency wiring
- ``core.dispatcher``: Per-type file processing pipelines
- ``core.file_ops``: File collection, copy/link, simulation, cleanup
- ``core.display``: Rich UI output (progress, summary, tables)
"""

# pyre-ignore-all-errors[35]: Pyre 0.9.25 mis-flags dataclass/ClassVar field
# annotations when `from __future__ import annotations` is in use.
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger
from rich.console import Console

from core import dispatcher, display, file_ops, initializer
from core.error_taxonomy import classify_error
from core.types import (
    AUDIO_EXTENSIONS,
    CAD_EXTENSIONS,
    IMAGE_EXTENSIONS,
    NO_EXTENSION_SENTINEL,
    OFFICE_TEMP_SENTINEL,
    TEXT_EXTENSIONS,
    TEXT_FALLBACK_MAP,
    VIDEO_EXTENSIONS,
    OrganizationResult,
)
from models.base import ModelConfig
from parallel.config import ExecutorType, ParallelConfig
from parallel.processor import ParallelProcessor
from services import ProcessedFile, ProcessedImage, TextProcessor, VisionProcessor
from services.audio.metadata_extractor import AudioMetadataExtractor
from services.video.metadata_extractor import VideoMetadataExtractor
from undo import UndoManager
from utils.safedir import SafeDir, SymlinkRejected


class FileOrganizer:
    """Main file organizer that orchestrates the entire process.

    This class scans directories for files, routes them to the
    appropriate processor (text, image, audio, video), organizes
    results into a folder structure, and provides progress feedback.

    Attributes:
        TEXT_EXTENSIONS: Supported text file extensions
        IMAGE_EXTENSIONS: Supported image file extensions
        VIDEO_EXTENSIONS: Supported video file extensions
        AUDIO_EXTENSIONS: Supported audio file extensions
        CAD_EXTENSIONS: Supported CAD file extensions
    """

    # ClassVars re-exported from core.types for backward compatibility
    TEXT_EXTENSIONS: ClassVar[set[str]] = set(TEXT_EXTENSIONS)
    IMAGE_EXTENSIONS: ClassVar[set[str]] = set(IMAGE_EXTENSIONS)
    VIDEO_EXTENSIONS: ClassVar[set[str]] = set(VIDEO_EXTENSIONS)
    AUDIO_EXTENSIONS: ClassVar[set[str]] = set(AUDIO_EXTENSIONS)
    CAD_EXTENSIONS: ClassVar[set[str]] = set(CAD_EXTENSIONS)
    _TEXT_FALLBACK_MAP: ClassVar[dict[str, str]] = TEXT_FALLBACK_MAP

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Verify fallback map covers all text and CAD extensions.

        Fires only for subclasses, not for FileOrganizer itself.
        Raises ``TypeError`` at class-definition time if any extension
        in TEXT_EXTENSIONS or CAD_EXTENSIONS is missing from
        ``_TEXT_FALLBACK_MAP``.
        """
        super().__init_subclass__(**kwargs)
        missing = (cls.TEXT_EXTENSIONS | cls.CAD_EXTENSIONS) - cls._TEXT_FALLBACK_MAP.keys()
        if missing:
            raise TypeError(
                f"{cls.__name__}._TEXT_FALLBACK_MAP is missing entries for: {missing}. "
                "Add them to keep fallback organization consistent with extension routing."
            )

    def __init__(
        self,
        text_model_config: ModelConfig | None = None,
        vision_model_config: ModelConfig | None = None,
        dry_run: bool = True,
        use_hardlinks: bool = True,
        parallel_workers: int | None = None,
        no_prefetch: bool = False,
        *,
        prefetch_depth: int = 2,
        enable_vision: bool = True,
        transcribe_audio: bool = False,
        max_transcribe_seconds: float | None = 600.0,
        timeout_per_file: float = 300.0,
    ) -> None:
        """Initialize file organizer.

        Args:
            text_model_config: Configuration for text model (optional)
            vision_model_config: Configuration for vision model (optional)
            dry_run: If True, only simulate operations
            use_hardlinks: If True, create hardlinks instead of copying
            parallel_workers: Number of parallel workers (default: None = auto)
            prefetch_depth: Queue-ahead depth for parallel task scheduling.
                ``0`` disables prefetch and forces sequential submission.
            enable_vision: If False, skip vision model initialization and
                organize images with extension-based fallbacks.
            no_prefetch: Backward-compatible alias for ``prefetch_depth=0``.
            transcribe_audio: If True, run audio files through Whisper for
                content-aware categorization. Requires the ``[media]``
                extra; degrades gracefully (warning + metadata-only path)
                when the dependency is missing. Default False because
                transcription is the expensive operation in the audio
                pipeline.
            max_transcribe_seconds: Per-file duration cap for
                ``transcribe_audio``. Files longer than this skip
                transcription and use metadata-only categorization.
                Whisper "tiny" is roughly 5-10x realtime on CPU; the 600s
                default keeps a single file under ~2 minutes of CPU work.
                ``None`` disables the cap.
            timeout_per_file: Per-file dispatcher timeout in seconds.
                Default 300 (5 min). Issue #396 — set this lower
                (e.g. 90) when your model returns quickly for the
                workload; raise it (e.g. 600) when working with large
                images and a slow vision model. The dispatcher cannot
                cancel a blocking Ollama call, so a too-low value
                abandons in-flight work that keeps holding the model's
                generation slot.
        """
        if timeout_per_file <= 0:
            raise ValueError(f"timeout_per_file must be > 0, got {timeout_per_file}")
        if text_model_config is None or vision_model_config is None:
            from config.provider_env import get_model_configs

            resolved_text, resolved_vision = get_model_configs()
            self.text_model_config = text_model_config or resolved_text
            self.vision_model_config = vision_model_config or resolved_vision
        else:
            self.text_model_config = text_model_config
            self.vision_model_config = vision_model_config
        self.dry_run = dry_run
        self.use_hardlinks = use_hardlinks
        self.enable_vision = enable_vision
        self.no_prefetch = no_prefetch
        self.prefetch_depth = prefetch_depth
        if no_prefetch and prefetch_depth != 0:
            logger.warning(
                "no_prefetch=True overrides prefetch_depth={} to 0 for backward compatibility",
                prefetch_depth,
            )
            self.prefetch_depth = 0
        if no_prefetch:
            logger.info("Prefetch disabled (no_prefetch=True)")
        self.console = Console()

        self.timeout_per_file = timeout_per_file
        self.parallel_config = ParallelConfig(
            max_workers=parallel_workers,
            executor_type=ExecutorType.THREAD,
            prefetch_depth=self.prefetch_depth,
            timeout_per_file=timeout_per_file,
            retry_count=1,
        )
        self.parallel_processor = ParallelProcessor(self.parallel_config)

        self.text_processor: TextProcessor | None = None
        self.vision_processor: VisionProcessor | None = None
        self.transcribe_audio = transcribe_audio
        # Reject negative caps at construction. Without the guard a
        # negative value silently skips every audio file (every duration
        # exceeds it), giving the user a confusing "no transcripts but
        # no warning" outcome. The CLI's `min=0.0` already rejects this
        # at the Typer layer, but library callers can still pass it.
        if max_transcribe_seconds is not None and max_transcribe_seconds < 0:
            raise ValueError(
                f"max_transcribe_seconds must be >= 0 or None (got {max_transcribe_seconds!r})"
            )
        self.max_transcribe_seconds = max_transcribe_seconds
        # Lazy-init in `_process_audio_files`; only constructed when the
        # caller passes `transcribe_audio=True`. `None` keeps the legacy
        # metadata-only path zero-overhead for the common case.
        self._audio_model: Any = None
        self._undo_manager: UndoManager | None = None
        self._last_transaction_id: str | None = None
        self._last_output_path: Path | None = None

        logger.info(
            "FileOrganizer initialized (dry_run={}, parallel_workers={}, prefetch_depth={}, "
            "enable_vision={})",
            dry_run,
            parallel_workers,
            self.prefetch_depth,
            enable_vision,
        )

    # ------------------------------------------------------------------
    # Main orchestration
    # ------------------------------------------------------------------

    def organize(
        self,
        input_path: str | Path,
        output_path: str | Path,
        skip_existing: bool = True,
        *,
        show_skipped: bool = False,
    ) -> OrganizationResult:
        """Organize files from input directory to output directory.

        Args:
            input_path: Path to directory with files to organize
            output_path: Path to output directory
            skip_existing: Skip files that already exist in output
            show_skipped: When True, the summary renderer prints every
                skipped-extension entry instead of capping at the top-N
                preview. Wired to ``--show-skipped`` on ``fo organize``.

        Returns:
            OrganizationResult with statistics and structure

        Raises:
            ValueError: If input path does not exist
        """
        start_time = time.time()
        input_path = Path(input_path)
        output_path = Path(output_path)

        if not input_path.exists():
            raise ValueError(f"Input path does not exist: {input_path}")

        self.console.print(f"\n[bold blue]Scanning:[/bold blue] {input_path}")
        files = file_ops.collect_files(input_path, self.console)

        result = OrganizationResult(total_files=len(files))
        if not files:
            self.console.print("[yellow]No files found to organize[/yellow]")
            return result

        text_files, image_files, video_files, audio_files, cad_files, other_files = (
            self._categorize_files(files)
        )

        display.show_file_breakdown(
            self.console,
            text_files=text_files,
            image_files=image_files,
            video_files=video_files,
            audio_files=audio_files,
            cad_files=cad_files,
            other_files=other_files,
        )

        all_processed = self._process_all_file_types(
            text_files, image_files, video_files, audio_files, cad_files
        )

        if all_processed:
            # Per-file inference duration samples (#410). Collect BEFORE
            # dedup so the summary's `n` matches the per-file
            # text/vision_inference_ms log stream — `_deduplicate_processed`
            # drops content-duplicate entries from `all_processed`, but
            # those inferences already ran and were logged, so they must
            # contribute to mean/p50/p95/p99 (CodeRabbit P2 catch on PR #424).
            # Skip entries that never went through process_file (e.g.
            # dispatcher-built fallbacks) which carry inference_ms == None.
            #
            # Per-file confidence (#409) is emitted in the same pass so
            # the audit log and the "Review recommended" summary list
            # stay synchronised. Threshold comes from ProcessingSettings,
            # loaded once outside the loop.
            try:
                from config.manager import ConfigManager

                _confidence_threshold = ConfigManager().load().processing.low_confidence_threshold
            except Exception:
                from config.schema import ProcessingSettings

                _confidence_threshold = ProcessingSettings().low_confidence_threshold

            for p in all_processed:
                ms = getattr(p, "inference_ms", None)
                if isinstance(ms, (int, float)):
                    # ProcessedImage carries the `source` field (#406) so we
                    # treat the image side; ProcessedFile is the text side.
                    if hasattr(p, "source"):
                        result.vision_inference_ms_samples.append(float(ms))
                    else:
                        result.text_inference_ms_samples.append(float(ms))
                # Confidence sample for the audit trail + summary list
                # (#409). Confidence is always populated (defaults to
                # 1.0 on the result dataclasses), so no None guard.
                _confidence = float(getattr(p, "confidence", 1.0))
                logger.debug(
                    "confidence={:.2f} file={} source={}",
                    _confidence,
                    p.file_path.name,
                    getattr(p, "source", "text"),
                )
                # Two conditions:
                # 1. confidence < 1.0  — happy-path inferences (score
                #    1.0) are NEVER flagged regardless of threshold,
                #    so setting `low_confidence_threshold=1.0` doesn't
                #    flood the review list with every file (Codex P2
                #    catch on PR #426).
                # 2. confidence <= threshold — inclusive on the
                #    threshold so the canonical borderline case (EXIF
                #    fallback at 0.5 against the 0.5 default) lands
                #    in the review (Codex P1 catch on PR #426).
                if _confidence < 1.0 and _confidence <= _confidence_threshold:
                    result.low_confidence_files.append(p.file_path.name)
                # Structured error breakdown (#411). Bucket via the
                # shared taxonomy so the summary renderer and any JSON
                # consumer see the same category labels. Only the
                # first encountered file per bucket gets stored as an
                # example to keep the breakdown dict small.
                _category = classify_error(p)
                if _category is not None:
                    result.error_breakdown[_category] += 1
                    result.error_examples.setdefault(_category, p.file_path.name)

            all_processed = self._deduplicate_processed(all_processed, result)
            failed_cnt = sum(1 for p in all_processed if p.error)
            # Vision-timeout fallbacks (#406) count as processed (they
            # landed in a folder) but are marked low-confidence for the
            # review section. ProcessedFile carries no `source` field, so
            # the check is guarded by `hasattr`.
            fallback_cnt = sum(
                1
                for p in all_processed
                if hasattr(p, "source") and str(getattr(p, "source", "")).startswith("fallback_")
            )
            result.processed_files = len(all_processed) - failed_cnt
            result.failed_files = failed_cnt
            result.fallback_files = fallback_cnt
            self._execute_organization(
                all_processed, input_path, output_path, skip_existing, result
            )

        # Skipped files
        if other_files:
            result.skipped_files = len(other_files)
            # Tally the breakdown by extension (issue #412). Stored on the
            # result so the summary renderer and --json output can surface
            # actionable signal about which formats would reduce the skip
            # rate the most.
            for f in other_files:
                result.skipped_by_extension[self._skipped_extension_key(f)] += 1

        result.processing_time = time.time() - start_time
        display.show_summary(
            self.console,
            result,
            output_path,
            dry_run=self.dry_run,
            show_skipped=show_skipped,
        )

        return result

    # ------------------------------------------------------------------
    # Private helpers extracted from organize()
    # ------------------------------------------------------------------

    def _categorize_files(
        self, files: list[Path]
    ) -> tuple[list[Path], list[Path], list[Path], list[Path], list[Path], list[Path]]:
        """Categorize files by type using extension sets.

        Returns:
            Six lists: (text, image, video, audio, cad, other)
        """
        text_files: list[Path] = []
        image_files: list[Path] = []
        video_files: list[Path] = []
        audio_files: list[Path] = []
        cad_files: list[Path] = []
        other_files: list[Path] = []

        for f in files:
            if f.name.startswith("~$"):
                logger.debug("skipped: office_temp_file {}", f)
                other_files.append(f)
                continue
            ext = f.suffix.lower()
            if ext in self.TEXT_EXTENSIONS:
                text_files.append(f)
            elif ext in self.IMAGE_EXTENSIONS:
                image_files.append(f)
            elif ext in self.VIDEO_EXTENSIONS:
                video_files.append(f)
            elif ext in self.AUDIO_EXTENSIONS:
                audio_files.append(f)
            elif ext in self.CAD_EXTENSIONS:
                cad_files.append(f)
            else:
                other_files.append(f)

        return text_files, image_files, video_files, audio_files, cad_files, other_files

    @staticmethod
    def _skipped_extension_key(f: Path) -> str:
        """Compute the skipped-extension bucket key for *f*.

        Returns:
            - ``OFFICE_TEMP_SENTINEL`` for Office lock files (``~$*``); the
              .docx/.xlsx suffix would otherwise misleadingly suggest a
              supported type was being skipped.
            - ``NO_EXTENSION_SENTINEL`` for files with no suffix (``README``,
              ``LICENSE``, …) so they don't collapse to ``""`` in the
              breakdown.
            - The lower-cased ``Path.suffix`` (e.g. ``.nib``) otherwise.
        """
        if f.name.startswith("~$"):
            return OFFICE_TEMP_SENTINEL
        ext = f.suffix.lower()
        if not ext:
            return NO_EXTENSION_SENTINEL
        return ext

    def _process_image_type(self, image_files: list[Path]) -> list[ProcessedFile | ProcessedImage]:
        """Process image files, using vision model if available and enabled."""
        self.console.print(f"\n[bold blue]Processing {len(image_files)} images...[/bold blue]")
        if self.enable_vision:
            self._init_vision_processor()
            vision_ready = (
                self.vision_processor is not None
                and self.vision_processor.vision_model.is_initialized
            )
            if vision_ready:
                return list(self._process_image_files(image_files))
            return list(self._fallback_by_extension(image_files))
        self.console.print(
            "[yellow]⚠ Vision processing disabled (--no-vision/--text-only): "
            "using extension-based organization for images[/yellow]"
        )
        return list(self._fallback_by_extension(image_files))

    def _process_all_file_types(
        self,
        text_files: list[Path],
        image_files: list[Path],
        video_files: list[Path],
        audio_files: list[Path],
        cad_files: list[Path],
    ) -> list[ProcessedFile | ProcessedImage]:
        """Initialize processors and process all file type groups.

        Manages VRAM hand-off between text and vision models and ensures
        cleanup on success or failure.
        """
        all_processed: list[ProcessedFile | ProcessedImage] = []
        self.text_processor = None
        self.vision_processor = None

        try:
            if text_files or cad_files:
                self._init_text_processor()

            text_ready = (
                self.text_processor is not None and self.text_processor.text_model.is_initialized
            )

            if text_files:
                self.console.print(
                    f"\n[bold blue]Processing {len(text_files)} text files...[/bold blue]"
                )
                if text_ready:
                    all_processed.extend(self._process_text_files(text_files))
                else:
                    all_processed.extend(self._fallback_by_extension(text_files))

            if cad_files:
                self.console.print(
                    f"\n[bold blue]Processing {len(cad_files)} CAD files...[/bold blue]"
                )
                if text_ready:
                    all_processed.extend(self._process_text_files(cad_files))
                else:
                    all_processed.extend(self._fallback_by_extension(cad_files))

            # Release text model VRAM before loading vision model
            if image_files and self.text_processor:
                self.text_processor.cleanup()
                self.text_processor = None

            if image_files:
                all_processed.extend(self._process_image_type(image_files))

            if audio_files:
                self.console.print(
                    f"\n[bold blue]Processing {len(audio_files)} audio files...[/bold blue]"
                )
                all_processed.extend(self._process_audio_files(audio_files))

            if video_files:
                self.console.print(
                    f"\n[bold blue]Processing {len(video_files)} videos...[/bold blue]"
                )
                all_processed.extend(self._process_video_files(video_files))

        finally:
            if self.text_processor:
                self.text_processor.cleanup()
            if self.vision_processor:
                self.vision_processor.cleanup()
            if self._audio_model is not None:
                self._audio_model.safe_cleanup()
                # Reset to None so a subsequent organize() call on the
                # same FileOrganizer instance re-initializes a fresh
                # model. Without this, the lazy-init `is None` check in
                # `_process_audio_files` would keep the disposed handle
                # and `generate()` would raise RuntimeError per-file,
                # silently degrading transcription to metadata-only.
                self._audio_model = None

        return all_processed

    def _deduplicate_processed(
        self,
        all_processed: list[ProcessedFile | ProcessedImage],
        result: OrganizationResult,
    ) -> list[ProcessedFile | ProcessedImage]:
        """Remove duplicate files based on SHA-256 content hash.

        Mutates ``result.deduplicated_files``. Returns the deduplicated list.
        """
        seen_hashes: set[str] = set()
        deduped: list[ProcessedFile | ProcessedImage] = []
        for pf in all_processed:
            file_hash = self._sha256_via_safedir(pf.file_path)
            if file_hash is None:
                # I/O error or refused symlink — keep the file in the
                # deduped output (we can't determine whether it's a
                # duplicate without hashing).
                deduped.append(pf)
                continue
            if file_hash not in seen_hashes:
                seen_hashes.add(file_hash)
                deduped.append(pf)
            else:
                logger.info("Duplicate file detected by content: {}, skipping.", pf.file_path.name)
                result.deduplicated_files += 1
        return deduped

    @staticmethod
    def _sha256_via_safedir(file_path: Path) -> str | None:
        """Compute the SHA-256 hex digest of *file_path*, via SafeDir.

        Opens through :class:`utils.safedir.SafeDir` on POSIX so a
        symlink swapped in between organize-time enumeration and the
        hash read is refused (closes the LLM-exfiltration vector in
        #264). Windows / non-POSIX falls back to the legacy path-based
        open until the SafeDir Windows port lands.

        Returns ``None`` when the file is unreadable or the read is
        refused — the caller treats that as "unknown hash" and keeps
        the file in the deduplicated output rather than dropping it.
        """
        hasher = hashlib.sha256()
        if sys.platform != "win32":
            try:
                with SafeDir.open_root(file_path.parent) as safe_dir:
                    fd = safe_dir.open_for_reader(file_path.name)
                    try:
                        fileobj = os.fdopen(fd, "rb", closefd=True)
                    except OSError:
                        os.close(fd)
                        raise
                    with fileobj:
                        for chunk in iter(lambda: fileobj.read(65536), b""):
                            hasher.update(chunk)
                return hasher.hexdigest()
            except SymlinkRejected as exc:
                logger.warning("Refused to hash symlinked file {}: {}", file_path, exc)
                return None
            except NotImplementedError:
                logger.debug(
                    "SafeDir unavailable; hashing {} via legacy reader",
                    file_path.name,
                )
            except (OSError, ValueError):
                # ValueError covers SafeDir's name-validation rejection
                # (filenames with backslash / NUL / path separators);
                # OSError covers normal I/O failures. Both are
                # "file unreadable" outcomes — return None so the
                # caller keeps the file in the dedup output.
                return None
        # Legacy path-based fallback (Windows / NotImplementedError).
        try:
            with file_path.open("rb") as f:  # safedir: ok — Windows / NotImplementedError fallback
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except OSError:
            return None

    def _execute_organization(
        self,
        all_processed: list[ProcessedFile | ProcessedImage],
        input_path: Path,
        output_path: Path,
        skip_existing: bool,
        result: OrganizationResult,
    ) -> None:
        """Execute file organization or dry-run simulation.

        Mutates ``result.organized_structure``.
        """
        if not self.dry_run:
            self.console.print("\n[bold blue]Organizing files...[/bold blue]")
            if self._undo_manager is None:
                self._undo_manager = UndoManager()
            self._last_transaction_id = self._undo_manager.history.start_transaction(
                metadata={"input_path": str(input_path), "output_path": str(output_path)}
            )
            self._last_output_path = output_path

            try:
                organized = file_ops.organize_files(
                    all_processed,
                    output_path,
                    skip_existing,
                    use_hardlinks=self.use_hardlinks,
                    undo_manager=self._undo_manager,
                    transaction_id=self._last_transaction_id,
                )
            except (OSError, RuntimeError):
                logger.exception(
                    "Error while organizing files; leaving transaction {} uncommitted",
                    self._last_transaction_id,
                )
                raise
            else:
                undo_manager = self._undo_manager
                assert undo_manager is not None
                history = undo_manager.history
                history.commit_transaction(self._last_transaction_id)
                result.organized_structure = organized
        else:
            self.console.print("\n[bold yellow]DRY RUN - Simulating organization...[/bold yellow]")
            result.organized_structure = file_ops.simulate_organization(all_processed, output_path)

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def undo(self) -> bool:
        """Undo the last organize session."""
        if self._undo_manager is None or self._last_transaction_id is None:
            logger.warning("No organize session to undo")
            return False

        success = self._undo_manager.undo_transaction(self._last_transaction_id)
        if success and self._last_output_path is not None:
            file_ops.cleanup_empty_dirs(self._last_output_path)
        return success

    def redo(self) -> bool:
        """Redo the last undone organize session."""
        if self._undo_manager is None or self._last_transaction_id is None:
            logger.warning("No organize session to redo")
            return False

        return self._undo_manager.redo_transaction(self._last_transaction_id)

    # ------------------------------------------------------------------
    # Backward-compatible delegation (used by existing tests)
    # ------------------------------------------------------------------

    def _collect_files(self, path: Path) -> list[Path]:
        """Collect all files under *path* recursively (delegates to file_ops)."""
        return file_ops.collect_files(path, self.console)

    def _fallback_by_extension(self, files: list[Path]) -> list[ProcessedFile]:
        """Classify *files* by extension when AI processing is unavailable."""
        return file_ops.fallback_by_extension(files)

    def _organize_files(
        self,
        processed: list[ProcessedFile | ProcessedImage],
        output_path: Path,
        skip_existing: bool,
    ) -> dict[str, list[str]]:
        """Copy/move processed files into *output_path*, respecting undo history."""
        return file_ops.organize_files(
            processed,
            output_path,
            skip_existing,
            use_hardlinks=self.use_hardlinks,
            undo_manager=self._undo_manager,
            transaction_id=self._last_transaction_id,
        )

    def _simulate_organization(
        self,
        processed: list[ProcessedFile | ProcessedImage],
        output_path: Path,
    ) -> dict[str, list[str]]:
        """Simulate organization without writing any files (dry-run helper)."""
        return file_ops.simulate_organization(processed, output_path)

    def _cleanup_empty_dirs(self, root: Path) -> None:
        """Remove empty directories left behind after organization under *root*."""
        file_ops.cleanup_empty_dirs(root)

    def _init_text_processor(self) -> None:
        """Initialize the text processor; sets ``self.text_processor`` or leaves it None."""
        self.text_processor = initializer.init_text_processor(
            self.text_model_config,
            self.console,
            processor_cls=TextProcessor,
        )

    def _init_vision_processor(self) -> None:
        """Initialize the vision processor; sets ``self.vision_processor`` or leaves it None."""
        self.vision_processor = initializer.init_vision_processor(
            self.vision_model_config,
            self.console,
            processor_cls=VisionProcessor,
        )

    def _process_text_files(self, files: list[Path]) -> list[ProcessedFile]:
        """Dispatch *files* to the initialized text processor."""
        assert self.text_processor is not None
        return dispatcher.process_text_files(
            files, self.text_processor, self.parallel_processor, self.console
        )

    def _process_image_files(self, files: list[Path]) -> list[ProcessedImage]:
        """Dispatch *files* to the initialized vision processor."""
        assert self.vision_processor is not None
        return dispatcher.process_image_files(
            files, self.vision_processor, self.parallel_processor, self.console
        )

    def _process_audio_files(self, files: list[Path]) -> list[ProcessedFile]:
        """Extract metadata from audio *files* and return processed results.

        When ``transcribe_audio=True`` was passed to ``__init__``, lazy-init
        an ``AudioModel`` here and forward it to the dispatcher so each
        file within ``max_transcribe_seconds`` gets a transcript attached
        for downstream content-aware categorization. Falls back to
        metadata-only with a warning if the ``[media]`` extra is missing.
        """
        transcriber: Any = None
        if self.transcribe_audio:
            try:
                # Pre-flight check: `services.audio.transcriber` swallows the
                # `faster_whisper` ImportError at module load and exposes
                # `_FASTER_WHISPER_AVAILABLE=False` instead. Without this
                # gate, `AudioModel(...)` construction succeeds and the
                # ImportError fires later inside `generate()` for every
                # single file — flooding the user with per-file warnings
                # instead of the single organizer-level fallback warning
                # this branch promised. Detecting availability here keeps
                # the warning to one event per organize batch.
                from services.audio.transcriber import _FASTER_WHISPER_AVAILABLE

                if not _FASTER_WHISPER_AVAILABLE:
                    raise ImportError("faster_whisper is not installed (the [media] extra)")

                from models.audio_model import AudioModel
                from models.base import ModelConfig, ModelType

                if self._audio_model is None:
                    self._audio_model = AudioModel(
                        ModelConfig(name="tiny", model_type=ModelType.AUDIO)
                    )
                    self._audio_model.initialize()
                transcriber = self._audio_model
            except ImportError as exc:
                self.console.print(
                    f"[yellow]--transcribe-audio requires the [media] extra: {exc}. "
                    "Falling back to metadata-only categorization.[/yellow]"
                )

        return dispatcher.process_audio_files(
            files,
            extractor_cls=AudioMetadataExtractor,
            transcriber=transcriber,
            max_transcribe_seconds=self.max_transcribe_seconds,
        )

    def _process_video_files(self, files: list[Path]) -> list[ProcessedFile]:
        """Extract metadata from video *files* and return processed results."""
        return dispatcher.process_video_files(files, extractor_cls=VideoMetadataExtractor)
