"""Vision file processing service."""

from __future__ import annotations

import re
import threading
import time
import types as _t
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from config.schema import ProcessingSettings
from models import VisionModel
from models.base import BaseModel, ModelConfig, ModelType
from models.provider_factory import get_vision_model
from models.vision_schema import StructuredParseError
from services.inference_timer import time_inference

_BYTES_PER_MB = 1024 * 1024


def compute_vision_timeout(
    file_size_bytes: int,
    settings: ProcessingSettings | None = None,
) -> float:
    """Compute the adaptive vision timeout for a single image (#407).

    Formula:
        ``timeout = clamp(base + size_mb * per_mb_factor, base, max)``

    Where ``base``, ``per_mb_factor``, and ``max`` come from
    ``ProcessingSettings.vision_base_timeout_s`` / ``vision_per_mb_factor_s``
    / ``vision_max_timeout_s``.

    Args:
        file_size_bytes: Image size in bytes. Negative values are treated as 0.
        settings: Source of the three tunable parameters. When ``None``,
            a fresh ``ProcessingSettings()`` with defaults is used.

    Returns:
        Timeout in seconds, always within ``[base, max]``.

    Examples:
        With defaults (base=30, per_mb=15, max=300):
        - 0-byte file → 30s (base)
        - 100KB file (~0.1MB) → 30 + 0.1*15 = 31.5s
        - 10MB file → 30 + 10*15 = 180s
        - 100MB file → min(30 + 100*15, 300) = 300s (clamped to max)
    """
    if settings is None:
        settings = ProcessingSettings()
    size_mb = max(0, file_size_bytes) / _BYTES_PER_MB
    raw = settings.vision_base_timeout_s + size_mb * settings.vision_per_mb_factor_s
    return min(raw, settings.vision_max_timeout_s)


@dataclass
class ProcessedImage:
    """Result of image processing.

    The ``source`` field indicates how the categorization was produced:
    ``"vision"`` is the normal AI-model path; ``"fallback_exif"`` and
    ``"fallback_filename"`` mark low-confidence placements assigned by
    the metadata-only fallback (#406) when the vision call timed out.
    """

    file_path: Path
    description: str
    folder_name: str
    filename: str
    has_text: bool = False
    extracted_text: str | None = None
    processing_time: float = 0.0
    error: str | None = None
    source: str = "vision"
    # Wall-clock duration of the inference path measured in milliseconds
    # (#410). Populated even on the error / fallback paths so summary
    # aggregation (p50/p95/p99) reflects every per-file attempt, not just
    # the happy path. None on results assembled without going through
    # process_file (e.g. metadata-only fallback constructed by the
    # dispatcher).
    inference_ms: float | None = None
    # Categorization confidence in [0.0, 1.0] (#409). 1.0 = happy-path
    # vision inference, 0.5 = EXIF-based fallback, 0.3 = filename-only
    # fallback (#406 metadata path), 0.0 = error / no usable result.
    # Files below `AppConfig.processing.low_confidence_threshold` are
    # surfaced in the summary's "Review recommended" section.
    confidence: float = 1.0


class VisionProcessor:
    """Process image and video files using AI to generate metadata.

    This service:
    - Analyzes images with vision-language models
    - Generates descriptions and summaries
    - Creates folder names and filenames
    - Performs OCR when needed
    - Handles video frames
    """

    _FATAL_BACKEND_MARKERS: tuple[str, ...] = (
        "connection refused",
        "actively refused",
        "dial tcp",
        "health resp",
        "runner has unexpectedly stopped",
        "failed to connect",
        # Ollama 500 when the model cannot be loaded into memory (e.g. OOM
        # on machines where the text model is already occupying most RAM).
        # Retrying would just produce the same error on every image file.
        "model failed to load",
        "resource limitations",
    )

    def __init__(
        self,
        vision_model: BaseModel | None = None,
        config: ModelConfig | None = None,
        *,
        backend_cooldown_seconds: float = 20.0,
        max_image_long_edge: int = 1024,
    ) -> None:
        """Initialize vision processor.

        Args:
            vision_model: Pre-initialized vision model (optional). Any
                ``BaseModel`` subclass is accepted, allowing Ollama and
                OpenAI-compatible models to be passed interchangeably.
            config: Model configuration (used if ``vision_model`` not provided).
                The ``config.provider`` field controls which backend is used.
                If omitted, the Ollama default configuration is applied
                regardless of any global provider setting.
            backend_cooldown_seconds: Cooldown period for fatal backend
                failures before retrying model calls.
            max_image_long_edge: Maximum length of longest image edge before
                downscaling. Large images are resized to this dimension
                (preserving aspect ratio) before being sent to the vision model.
                Default: 1024 px. Min: 256, Max: 4096.
        """
        if vision_model is not None:
            if vision_model.config.model_type not in (ModelType.VISION, ModelType.VIDEO):
                raise ValueError(
                    f"VisionProcessor requires a VISION or VIDEO model, "
                    f"got {vision_model.config.model_type}"
                )
            self.vision_model = vision_model
            self._owns_model = False
        else:
            config = config or VisionModel.get_default_config()
            self.vision_model = get_vision_model(config)
            self._owns_model = True

        self._backend_cooldown_seconds = backend_cooldown_seconds
        self._max_image_long_edge = max(256, min(4096, max_image_long_edge))
        self._circuit_lock = threading.Lock()
        self._circuit_opened_at: float | None = None
        self._circuit_reason: str | None = None

        logger.info("VisionProcessor initialized")

    def initialize(self) -> None:
        """Initialize the vision model if not already initialized."""
        if not self.vision_model.is_initialized:
            self.vision_model.initialize()
            logger.info("Vision model initialized")

    def process_file(
        self,
        file_path: str | Path,
        generate_description: bool = True,
        generate_folder: bool = True,
        generate_filename: bool = True,
        perform_ocr: bool = True,
    ) -> ProcessedImage:
        """Process a single image file.

        Args:
            file_path: Path to image file
            generate_description: Whether to generate description
            generate_folder: Whether to generate folder name
            generate_filename: Whether to generate filename
            perform_ocr: Whether to extract text (OCR)

        Returns:
            ProcessedImage with metadata
        """
        file_path = Path(file_path)
        start_time = time.time()

        # Adaptive per-image timeout (#407) — currently informative; the
        # dispatcher's static `timeout_per_file` still governs cancellation,
        # but logging the computed value lets operators correlate slow
        # inferences with image size before any enforcement wiring lands.
        try:
            _adaptive_timeout = compute_vision_timeout(
                file_path.stat().st_size,
            )
            logger.debug(
                "Adaptive vision timeout for {}: {:.1f}s",
                file_path.name,
                _adaptive_timeout,
            )
        except OSError:
            # stat() can fail on permission-denied / disappeared files;
            # don't let the informational log break the real work below.
            pass

        # Per-file inference timer (#410). The context manager logs
        # `vision_inference_ms=<N>` on exit — success and exception
        # paths both fire — and exposes the duration on `_timer.elapsed_ms`.
        # The inner method returns a (result, model_invoked) tuple so we
        # can attribute the timer correctly even on mid-flight failures:
        # an attempted-but-failed inference DOES contribute to p95/p99
        # (operators need accurate tail latency during degraded backend
        # periods), while pre-inference early returns (circuit-open,
        # file-not-found) DO NOT (CodeRabbit P2 round-trip on PR #424).
        with time_inference("vision", file_path) as _timer:
            result, model_invoked = self._process_file_inner(
                file_path,
                start_time=start_time,
                generate_description=generate_description,
                generate_folder=generate_folder,
                generate_filename=generate_filename,
                perform_ocr=perform_ocr,
            )
            if model_invoked:
                # Both gates the log line emission AND signals "include
                # in samples" for the in-process aggregator below.
                _timer.mark_invoked()
        if model_invoked:
            result.inference_ms = _timer.elapsed_ms
        return result

    def _process_file_inner(
        self,
        file_path: Path,
        *,
        start_time: float,
        generate_description: bool,
        generate_folder: bool,
        generate_filename: bool,
        perform_ocr: bool,
    ) -> tuple[ProcessedImage, bool]:
        """Inner body of :meth:`process_file`.

        Returns ``(result, model_invoked)``. ``model_invoked`` is True
        iff at least one model call (description / OCR / folder /
        filename) was attempted — regardless of whether it succeeded
        or raised. Failed-but-attempted inferences must contribute to
        the #410 p95/p99 summary so operators see real tail latency
        during degraded-backend periods; pre-inference early returns
        (circuit-open before any call, file-not-found) must not.
        """
        # Tracked across the try block so the broad except below can
        # tell apart pre-inference exceptions (filesystem / decode)
        # from in-flight inference failures.
        model_invoked = False
        try:
            if self._is_circuit_open():
                logger.warning(
                    "Vision backend circuit open; skipping model calls for {}",
                    file_path.name,
                )
                error_message = self._circuit_open_error()
                logger.debug(
                    "Circuit-open fallback for {} with error={}",
                    file_path.name,
                    error_message,
                )
                return (
                    ProcessedImage(
                        file_path=file_path,
                        description=f"Image from {file_path.name}",
                        folder_name="images",
                        filename=file_path.stem,
                        error=error_message,
                        confidence=0.0,
                    ),
                    False,  # no model call attempted
                )

            # Validate file exists
            if not file_path.exists():
                return (
                    ProcessedImage(
                        file_path=file_path,
                        description="",
                        folder_name="errors",
                        filename=file_path.stem,
                        error="File not found",
                        confidence=0.0,
                    ),
                    False,  # no model call attempted
                )

            # Single structured call (#433): request exactly the enabled
            # fields in one shot, with a strict-mode retry and a fallback to
            # the legacy per-field path when the JSON is unparsable twice.
            fields: list[str] = []
            if generate_description:
                fields.append("description")
            if perform_ocr:
                fields.append("extracted_text")
            if generate_folder:
                fields.append("folder_name")
            if generate_filename:
                fields.append("filename")

            if not fields:
                return (
                    ProcessedImage(
                        file_path=file_path,
                        description="",
                        folder_name="",
                        filename="",
                    ),
                    False,  # no model call attempted
                )

            model_invoked = True
            try:
                try:
                    parsed = self._guarded_generate_structured(file_path, fields, strict=False)
                except StructuredParseError:
                    # First attempt produced bad/incomplete JSON — retry once
                    # with the strict JSON-only prompt before falling back.
                    parsed = self._guarded_generate_structured(file_path, fields, strict=True)
            except StructuredParseError:
                # Both structured attempts failed to parse — fall back to the
                # retained legacy 4-call path.
                return self._legacy_process(
                    file_path,
                    start_time,
                    generate_description,
                    perform_ocr,
                    generate_folder,
                    generate_filename,
                )
            except Exception:
                # Backend error (possibly fatal). If the guarded wrapper tripped
                # the circuit, emit the same degradation shape as the legacy
                # post-description circuit check; otherwise re-raise into the
                # broad handler below.
                if self._is_circuit_open():
                    return (
                        ProcessedImage(
                            file_path=file_path,
                            description=f"Image from {file_path.name}",
                            folder_name="images",
                            filename=file_path.stem,
                            error=self._circuit_open_error(),
                            confidence=0.0,
                        ),
                        True,  # the call that tripped the circuit DID happen
                    )
                # Non-fatal backend error (empty-response ValueError, transient
                # 5xx, etc.). Fall back to the per-field path so its typed
                # handlers absorb it gracefully (matches pre-#433 behavior)
                # instead of dumping the file into "errors/".
                return self._legacy_process(
                    file_path,
                    start_time,
                    generate_description,
                    perform_ocr,
                    generate_folder,
                    generate_filename,
                )

            # Mirror legacy _extract_text safeguards: sentinel/too-short OCR
            # responses become None instead of persisting verbatim.
            _ocr_text = (parsed.get("extracted_text") or "").strip()
            extracted: str | None = (
                None
                if _ocr_text.upper() in ["NO_TEXT", "NO TEXT", "NONE", "N/A"] or len(_ocr_text) < 10
                else _ocr_text
            )
            has_text = bool(extracted and len(extracted) > 10)
            # Mirror the legacy "description is never empty for a model call"
            # invariant (only when description was requested).
            description = (
                (parsed.get("description", "").strip() or f"Image from {file_path.name}")
                if generate_description
                else ""
            )
            processing_time = time.time() - start_time
            return (
                ProcessedImage(
                    file_path=file_path,
                    description=description,
                    folder_name=(
                        self._finalize_folder_name(parsed.get("folder_name", ""))
                        if generate_folder
                        else ""
                    ),
                    filename=(
                        self._finalize_filename(parsed.get("filename", ""), file_path)
                        if generate_filename
                        else ""
                    ),
                    has_text=has_text,
                    extracted_text=extracted[:500] if extracted else None,
                    processing_time=processing_time,
                ),
                model_invoked,
            )

        except Exception as e:
            # B2: broad catch is load-bearing (organizer iterates many
            # images; one bad file must not crash the pipeline) but the
            # log message now includes the exception class name so
            # operators can bucket failures by category (filesystem vs
            # model-inference vs decode) without reparsing stack traces.
            # ``logger.exception`` attaches the full traceback on top.
            logger.exception(
                "Failed to process {} (type={}): {}",
                file_path.name,
                type(e).__name__,
                e,
            )
            # Forward `model_invoked` so a pre-inference exception
            # (filesystem error, image-decode failure) is reported as
            # NOT an inference attempt, while an in-flight inference
            # failure (after we'd flipped the flag above) still counts.
            return (
                ProcessedImage(
                    file_path=file_path,
                    description="",
                    folder_name="errors",
                    filename=file_path.stem,
                    # Preserve the existing ``error=str(e)`` contract — B2
                    # scope is log-message categorisation, not the public
                    # ``ProcessedImage.error`` field.
                    error=str(e),
                    confidence=0.0,
                ),
                model_invoked,
            )

    def _legacy_process(
        self,
        file_path: Path,
        start_time: float,
        generate_description: bool,
        perform_ocr: bool,
        generate_folder: bool,
        generate_filename: bool,
    ) -> tuple[ProcessedImage, bool]:
        """Retained per-field 4-call path used as a structured-output fallback.

        Issues one model call per enabled field (description, OCR, folder,
        filename) via the legacy ``_generate_*`` helpers. Returns
        ``(result, model_invoked)`` where ``model_invoked`` is True iff at
        least one field call was attempted. The post-description circuit
        check short-circuits the remaining calls when the backend trips.

        ``start_time`` is the caller's processing-start anchor, threaded in
        so ``processing_time`` covers the whole request (not just the
        fallback) when the structured path defers here.
        """
        model_invoked = False

        # Generate description
        description = ""
        if generate_description:
            logger.debug(f"Analyzing image: {file_path.name}")
            model_invoked = True  # _generate_description issues the model call
            description = self._generate_description(file_path)
            logger.debug(f"Generated description ({len(description)} chars)")
            if self._is_circuit_open():
                error_message = self._circuit_open_error()
                return (
                    ProcessedImage(
                        file_path=file_path,
                        description=description or f"Image from {file_path.name}",
                        folder_name="images",
                        filename=file_path.stem,
                        error=error_message,
                        confidence=0.0,
                    ),
                    True,  # the call that tripped the circuit DID happen
                )

        # Extract text if needed
        extracted_text = None
        has_text = False
        if perform_ocr:
            model_invoked = True  # _extract_text issues the model call
            extracted_text = self._extract_text(file_path)
            has_text = bool(extracted_text and len(extracted_text.strip()) > 10)
            if has_text and extracted_text is not None:
                logger.debug(f"Extracted {len(extracted_text)} chars of text")

        # Generate folder name
        folder_name = ""
        if generate_folder:
            model_invoked = True  # _generate_folder_name issues the model call
            # Use extracted text if available, otherwise use description
            context: str = (extracted_text or description) if has_text else description
            folder_name = self._generate_folder_name(file_path, context)
            logger.debug(f"Generated folder name: {folder_name}")

        # Generate filename
        filename = ""
        if generate_filename:
            model_invoked = True  # _generate_filename issues the model call
            # Use extracted text if available, otherwise use description
            context = (extracted_text or description) if has_text else description
            filename = self._generate_filename(file_path, context)
            logger.debug(f"Generated filename: {filename}")

        processing_time = time.time() - start_time

        return (
            ProcessedImage(
                file_path=file_path,
                description=description,
                folder_name=folder_name,
                filename=filename,
                has_text=has_text,
                extracted_text=extracted_text[:500] if extracted_text else None,
                processing_time=processing_time,
            ),
            model_invoked,
        )

    def _guarded_generate_structured(
        self, image_path: Path, fields: list[str], *, strict: bool
    ) -> dict[str, str]:
        """Run model.generate_structured behind the fatal-error circuit-breaker.

        Mirrors ``_guarded_generate``: short-circuits when the circuit is open,
        and on a fatal backend error trips the circuit and re-raises.
        ``StructuredParseError`` is NOT a backend error — it propagates so the
        caller can retry / fall back.
        """
        if self._is_circuit_open():
            reason = self._circuit_reason or "backend unavailable"
            raise RuntimeError(f"Vision backend circuit open: {reason}")
        try:
            result = self.vision_model.generate_structured(
                fields,
                image_path=image_path,
                strict_json_only=strict,
                max_image_long_edge=self._max_image_long_edge,
            )
        except StructuredParseError:
            raise
        except Exception as exc:  # circuit-breaker for any backend error
            if self._is_fatal_backend_error(exc):
                self._trip_backend_circuit(exc)
            raise
        if not isinstance(result, Mapping):
            raise StructuredParseError(
                f"generate_structured returned {type(result).__name__}, expected mapping"
            )

        parsed = dict(result)
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
        ):
            raise StructuredParseError("generate_structured returned non-string keys or values")
        return parsed

    def _clean_ai_generated_name(self, name: str, max_words: int = 3) -> str:
        """Clean AI-generated folder/file names with lighter filtering.

        Args:
            name: AI-generated name
            max_words: Maximum number of words

        Returns:
            Cleaned name
        """
        # Convert underscores and hyphens to spaces
        name = name.replace("_", " ").replace("-", " ")

        # Remove special characters and numbers (keep letters and spaces)
        name = re.sub(r"[^a-z\s]", "", name.lower())

        # Split into words
        words = name.split()

        # Only filter out truly problematic words
        bad_words = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "is",
            "are",
            "was",
            "were",
            "be",
            "image",
            "picture",
            "photo",
            "untitled",
            "unknown",
        }

        # Filter and deduplicate
        filtered = []
        seen = set()
        for word in words:
            if word and word not in bad_words and word not in seen and len(word) > 1:
                filtered.append(word)
                seen.add(word)

        # Limit to max words
        filtered = filtered[:max_words]

        # Join with underscores
        return "_".join(filtered) if filtered else ""

    def _generate_description(self, image_path: Path) -> str:
        """Generate a description of the image.

        Args:
            image_path: Path to image file

        Returns:
            Image description
        """
        prompt = """Describe this image in detail. Include:
1. Main subject or focus
2. Important objects, people, or elements
3. Setting or environment
4. Colors, mood, or atmosphere
5. Any visible text or labels

Provide a clear, descriptive paragraph (100-150 words)."""

        try:
            response = self._guarded_generate(
                prompt=prompt,
                image_path=image_path,
                temperature=0.5,
                max_tokens=250,
            )
            return response.strip()
        except (RuntimeError, ValueError, OSError, AttributeError) as e:
            logger.error(f"Failed to generate description: {e}")
            return f"Image from {image_path.name}"

    def _extract_text(self, image_path: Path) -> str | None:
        """Extract text from image using OCR.

        Args:
            image_path: Path to image file

        Returns:
            Extracted text or None
        """
        prompt = """Extract ALL visible text from this image.
Include any text you see, whether it's:
- Titles, headings, or labels
- Body text or paragraphs
- Numbers, dates, or codes
- Signs, captions, or watermarks

Provide ONLY the text, preserving the order but not necessarily the formatting.
If there's no readable text, respond with "NO_TEXT"."""

        try:
            response = self._guarded_generate(
                prompt=prompt,
                image_path=image_path,
                temperature=0.1,
                max_tokens=500,
            )

            response = response.strip()

            # Check if no text was found
            if response.upper() in ["NO_TEXT", "NO TEXT", "NONE", "N/A"]:
                return None

            # Check if response is too short to be meaningful
            if len(response) < 10:
                return None

            return response

        except (RuntimeError, ValueError, OSError, AttributeError) as e:
            logger.error(f"Failed to extract text: {e}")
            return None

    def _generate_folder_name(self, image_path: Path, context: str) -> str:
        """Generate a folder name from image context.

        Args:
            image_path: Path to image file
            context: Description or extracted text

        Returns:
            Folder name (max 2 words)
        """
        prompt = f"""Based on the image analysis below, generate a general category or theme.

RULES:
1. Maximum 2 words (e.g., "nature_photography", "architecture", "food")
2. Use ONLY nouns, no verbs
3. Be general, not specific
4. Use lowercase with underscores between words
5. NO generic terms like 'image', 'photo', 'picture', 'untitled'
6. Output ONLY the category, NO explanation

EXAMPLES:
- Image of mountains and forest → "nature_landscapes"
- Image of city buildings → "urban_architecture"
- Image of food dish → "food"
- Image of people at meeting → "business_meetings"

IMAGE ANALYSIS:
{context[:1000]}

CATEGORY:"""

        try:
            response = self._guarded_generate(
                prompt=prompt,
                image_path=image_path,
                temperature=0.3,
                max_tokens=30,
            )

            logger.debug(f"AI folder response (raw): '{response}'")
            return self._finalize_folder_name(response)

        except (RuntimeError, ValueError, OSError, AttributeError) as e:
            logger.error(f"Failed to generate folder name: {e}")
            return "images"

    def _finalize_folder_name(self, raw: str) -> str:
        """Clean a raw folder-name string into a filesystem-safe category.

        Shared by the legacy ``_generate_folder_name`` and the structured path
        (#433) so model output is never trusted for filesystem safety.

        Args:
            raw: Unprocessed model output for the folder/category name.

        Returns:
            Filesystem-safe folder name (max 2 words, ``"images"`` fallback).
        """
        # Clean the response
        folder_name = raw.strip().lower()

        # Remove common prefixes and quotes
        for prefix in ["category:", "folder:", "the category is", "the folder is"]:
            folder_name = folder_name.replace(prefix, "").strip()
        folder_name = folder_name.strip("\"'")

        # Remove newlines and extra spaces
        folder_name = " ".join(folder_name.split())

        logger.debug(f"AI folder response (cleaned): '{folder_name}'")

        # Use lighter cleaning for AI-generated names
        folder_name = self._clean_ai_generated_name(folder_name, max_words=2)

        logger.debug(f"AI folder response (after filter): '{folder_name}'")

        if not folder_name or len(folder_name) < 3:
            logger.warning(f"Folder name empty or too short ('{folder_name}'), using fallback")
            folder_name = "images"

        # Final safety check
        folder_name = re.sub(r"[^\w_]", "_", folder_name)
        folder_name = re.sub(r"_+", "_", folder_name).strip("_")
        result = folder_name[:50] if folder_name else "images"
        logger.info(f"Final folder name: '{result}'")
        return result

    def _generate_filename(self, image_path: Path, context: str) -> str:
        """Generate a filename from image context.

        Args:
            image_path: Path to image file
            context: Description or extracted text

        Returns:
            Filename (max 3 words, no extension)
        """
        prompt = f"""Based on the image analysis below, generate a specific descriptive filename.

RULES:
1. Maximum 3 words (e.g., "sunset_mountain_view", "coffee_cup_closeup")
2. Use meaningful nouns (NO verbs like 'shows', 'depicts', 'presents')
3. NO generic words like 'image', 'photo', 'picture', 'jpg', 'untitled'
4. Use lowercase with underscores between words
5. Be specific about the content, not generic
6. Output ONLY the filename, NO explanation

EXAMPLES:
- Image of sunset over mountains → "mountain_sunset_view"
- Image of coffee cup on table → "coffee_cup_table"
- Image of laptop with code → "laptop_coding_setup"
- Image of golden retriever → "golden_retriever_dog"

IMAGE ANALYSIS:
{context[:1000]}

FILENAME:"""

        try:
            response = self._guarded_generate(
                prompt=prompt,
                image_path=image_path,
                temperature=0.3,
                max_tokens=30,
            )

            logger.debug(f"AI filename response (raw): '{response}'")
            return self._finalize_filename(response, image_path)

        except (RuntimeError, ValueError, OSError, AttributeError) as e:
            logger.error(f"Failed to generate filename: {e}")
            return image_path.stem

    def _finalize_filename(self, raw: str, image_path: Path) -> str:
        """Clean a raw filename string into a filesystem-safe stem.

        Shared by the legacy ``_generate_filename`` and the structured path
        (#433). Falls back to ``image_path.stem`` when empty/too short, and to
        the literal ``"image"`` if the safety pass strips it to nothing.

        Args:
            raw: Unprocessed model output for the filename.
            image_path: Source image path, used for the stem fallback.

        Returns:
            Filesystem-safe filename stem (max 3 words, no extension).
        """
        # Clean the response
        filename = raw.strip().lower()

        # Remove common prefixes and quotes
        for prefix in ["filename:", "file:", "name:", "the filename is", "the name is"]:
            filename = filename.replace(prefix, "").strip()
        filename = filename.strip("\"'")

        # Remove file extensions if AI added them
        filename = re.sub(r"\.(txt|pdf|jpg|jpeg|png|gif|bmp)$", "", filename)

        # Remove newlines and extra spaces
        filename = " ".join(filename.split())

        logger.debug(f"AI filename response (cleaned): '{filename}'")

        # Use lighter cleaning for AI-generated names
        filename = self._clean_ai_generated_name(filename, max_words=3)

        logger.debug(f"AI filename response (after filter): '{filename}'")

        if not filename or len(filename) < 3:
            logger.warning(f"Filename empty or too short ('{filename}'), using fallback")
            filename = image_path.stem

        # Final safety check
        filename = re.sub(r"[^\w_]", "_", filename)
        filename = re.sub(r"_+", "_", filename).strip("_")
        result = filename[:50] if filename else "image"
        logger.info(f"Final filename: '{result}'")
        return result

    def _guarded_generate(self, **kwargs: Any) -> str:
        """Run model.generate behind a fatal-error circuit-breaker.

        The circuit opens only for known backend fatal failures (connection
        refused, runner stopped, health endpoint refusal). While open, calls
        are short-circuited so we do not keep hammering an unhealthy backend.
        """
        if self._is_circuit_open():
            reason = self._circuit_reason or "backend unavailable"
            raise RuntimeError(f"Vision backend circuit open: {reason}")

        try:
            # Pass max_image_long_edge to the vision model
            kwargs["max_image_long_edge"] = self._max_image_long_edge
            return self.vision_model.generate(**kwargs)
        except Exception as exc:  # Intentional catch-all: circuit-breaker for any backend error
            if self._is_fatal_backend_error(exc):
                self._trip_backend_circuit(exc)
            raise

    def _is_fatal_backend_error(self, exc: Exception) -> bool:
        """Return True when an exception indicates backend process failure."""
        text = str(exc).lower()
        return any(marker in text for marker in self._FATAL_BACKEND_MARKERS)

    def _trip_backend_circuit(self, exc: Exception) -> None:
        """Open the backend circuit for a cooldown window."""
        with self._circuit_lock:
            self._circuit_opened_at = time.monotonic()
            self._circuit_reason = str(exc)
        logger.warning("Vision backend circuit opened: {}", exc)

    def _is_circuit_open(self) -> bool:
        """Return True while backend circuit cooldown is active."""
        with self._circuit_lock:
            opened_at = self._circuit_opened_at
            if opened_at is None:
                return False
            if (time.monotonic() - opened_at) < self._backend_cooldown_seconds:
                return True
            self._circuit_opened_at = None
            self._circuit_reason = None
            return False

    def _circuit_open_error(self) -> str:
        """Return a stable, user-visible degradation message."""
        reason = self._circuit_reason or "vision backend unavailable"
        return f"Vision backend unavailable: {reason}"

    def cleanup(self) -> None:
        """Cleanup resources.

        Uses ``safe_cleanup()`` to wait for any in-flight generations
        before tearing down the model client.
        """
        if self._owns_model:
            self.vision_model.safe_cleanup()
            logger.info("Vision model cleaned up")

    def __enter__(self) -> VisionProcessor:
        """Context manager entry."""
        self.initialize()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: _t.TracebackType | None,
    ) -> None:
        """Context manager exit."""
        self.cleanup()
