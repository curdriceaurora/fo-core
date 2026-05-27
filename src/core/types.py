"""Shared type definitions for the file organizer core.

Contains the ``OrganizationResult`` dataclass and extension-set constants
used across core modules.
"""

# pyre-ignore-all-errors[35]: Pyre 0.9.25 mis-flags dataclass/ClassVar field
# annotations when `from __future__ import annotations` is in use.
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# Sentinel keys for the skipped-extension tally when the file's suffix
# either doesn't map cleanly to an extension or would be misleading.
# Bucketing under these stable strings keeps the breakdown actionable
# (users see "<office-temp>: 12" instead of ".docx: 12" — the latter would
# suggest .docx is unsupported, which is the opposite of the truth).
OFFICE_TEMP_SENTINEL: str = "<office-temp>"
NO_EXTENSION_SENTINEL: str = "<no-extension>"


@dataclass
class OrganizationResult:
    """Result of organizing files.

    Attributes:
        total_files: Total number of files found
        processed_files: Number of files successfully processed
        skipped_files: Number of files skipped (unsupported types)
        failed_files: Number of files that failed processing
        deduplicated_files: Number of duplicate files removed by content-hash dedup
        processing_time: Total time taken in seconds
        organized_structure: Dictionary mapping folder names to file lists
        errors: List of (file_path, error_message) tuples
        skipped_by_extension: Counter mapping lower-cased extension (e.g.
            ``.nib``) to the number of skipped files with that suffix. Office
            temp lock files (``~$*``) and extensionless files use the
            ``<office-temp>`` and ``<no-extension>`` sentinel keys.
        fallback_files: Number of images that timed out in the vision model
            and were placed via the metadata fallback (#406). These count
            toward ``processed_files`` (they reached a folder) but are
            low-confidence and should be reviewed.

    Invariant:
        processed_files + skipped_files + failed_files + deduplicated_files == total_files
    """

    total_files: int = 0
    processed_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    deduplicated_files: int = 0
    processing_time: float = 0.0
    organized_structure: dict[str, list[str]] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (file, error)
    skipped_by_extension: Counter[str] = field(default_factory=Counter)  # pyre-ignore[35]
    fallback_files: int = 0  # pyre-ignore[35]: Pyre 0.9.25 mis-flags dataclass field annotations under `from __future__ import annotations`. Same pre-existing pattern as the other counters above (alerts #39-46 on main).
    # Per-file inference durations in milliseconds (#410). Populated by
    # the organizer from ProcessedFile.inference_ms / ProcessedImage.inference_ms.
    # The summary renderer derives mean / p50 / p95 / p99 from these samples;
    # storing the raw list keeps stats decoupled from the dataclass and
    # lets future code compute additional percentiles without a schema
    # change.
    vision_inference_ms_samples: list[float] = field(default_factory=list)  # pyre-ignore[35]
    text_inference_ms_samples: list[float] = field(default_factory=list)  # pyre-ignore[35]
    # Files placed at confidence below
    # ``AppConfig.processing.low_confidence_threshold`` (#409). Surfaced
    # in the summary's "Review recommended" section so operators can
    # audit borderline categorizations (EXIF fallbacks, filename-only
    # placements, error-bucket landings) before any destructive moves.
    # Entries are basenames so the summary stays readable even on deep
    # input hierarchies.
    low_confidence_files: list[str] = field(default_factory=list)  # pyre-ignore[35]
    # Structured error breakdown (#411). Maps each
    # ``core.error_taxonomy.ErrorCategory`` to the number of files that
    # bucketed there, plus one representative basename per bucket so
    # the summary line can show "203 vision_timeout (e.g. logo.png)".
    # Recommendation lines fire when a single bucket exceeds 10% of
    # ``total_files`` — see ``error_taxonomy.RECOMMENDATIONS``.
    error_breakdown: Counter[str] = field(default_factory=Counter)  # pyre-ignore[35]
    error_examples: dict[str, str] = field(default_factory=dict)  # pyre-ignore[35]


# ---------------------------------------------------------------------------
# Extension constants
# ---------------------------------------------------------------------------

TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt",
        ".md",
        ".docx",
        ".doc",
        ".pdf",
        ".csv",
        ".xlsx",
        ".xls",
        ".ppt",
        ".pptx",
        ".epub",
    }
)
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".webp",
        ".heic",
        ".heif",
        ".svg",
    }
)
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".avi",
        ".mkv",
        ".mov",
        ".wmv",
    }
)
AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp3",
        ".wav",
        ".flac",
        ".m4a",
        ".ogg",
    }
)
CAD_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".dwg",
        ".dxf",
        ".step",
        ".stp",
        ".iges",
        ".igs",
    }
)

# Extension -> folder name mapping used when Ollama is unavailable
TEXT_FALLBACK_MAP: dict[str, str] = {
    ".pdf": "PDFs",
    ".doc": "Documents",
    ".docx": "Documents",
    ".txt": "Documents",
    ".md": "Documents",
    ".csv": "Spreadsheets",
    ".xlsx": "Spreadsheets",
    ".xls": "Spreadsheets",
    ".ppt": "Presentations",
    ".pptx": "Presentations",
    ".epub": "eBooks",
    ".dwg": "CAD",
    ".dxf": "CAD",
    ".step": "CAD",
    ".stp": "CAD",
    ".iges": "CAD",
    ".igs": "CAD",
}

IMAGE_FALLBACK_FOLDER: str = "Images"
AUDIO_FALLBACK_FOLDER: str = "Audio/Unsorted"
VIDEO_FALLBACK_FOLDER: str = "Videos/Unsorted"
ERROR_FALLBACK_FOLDER: str = "errors"
