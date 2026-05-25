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
    skipped_by_extension: Counter[str] = field(default_factory=Counter)
    fallback_files: int = 0


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
