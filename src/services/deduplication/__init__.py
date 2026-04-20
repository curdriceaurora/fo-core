"""Deduplication service for detecting and managing duplicate files.

This module provides:
- Hash-based duplicate detection using MD5 or SHA256 algorithms
- Perceptual image hashing for detecting visually similar images
- Efficient indexing, batch processing, and safe file management
"""

from __future__ import annotations

from .backup import BackupManager
from .detector import DuplicateDetector
from .hasher import FileHasher

# ImageDeduplicator requires the optional 'imagededup' package
try:
    from .image_dedup import ImageDeduplicator
except ImportError:
    ImageDeduplicator = None  # type: ignore[assignment,misc]
# DocumentDeduplicator, DocumentEmbedder, SemanticAnalyzer require numpy.
# Guard so a default install (no dedup-text extra) can still reach the
# hash-based dedup path without a ModuleNotFoundError.
try:
    from .document_dedup import DocumentDeduplicator
    from .embedder import DocumentEmbedder
    from .semantic import SemanticAnalyzer
except ImportError:
    DocumentDeduplicator = None  # type: ignore[assignment,misc]
    DocumentEmbedder = None  # type: ignore[assignment,misc]
    SemanticAnalyzer = None  # type: ignore[assignment,misc]
from .extractor import DocumentExtractor
from .image_utils import (
    ImageMetadata,
    filter_valid_images,
    find_images_in_directory,
    format_file_size,
    get_best_quality_image,
    get_image_info_string,
    get_image_metadata,
    validate_image_file,
)
from .index import DuplicateIndex
from .reporter import StorageReporter

__all__ = [
    # Hash-based deduplication
    "FileHasher",
    "DuplicateIndex",
    "DuplicateDetector",
    "BackupManager",
    # Image deduplication
    "ImageDeduplicator",
    "ImageMetadata",
    # Image utilities
    "get_image_metadata",
    "validate_image_file",
    "filter_valid_images",
    "find_images_in_directory",
    "get_best_quality_image",
    "get_image_info_string",
    "format_file_size",
    # Document deduplication
    "DocumentExtractor",
    "DocumentEmbedder",
    "SemanticAnalyzer",
    "DocumentDeduplicator",
    "StorageReporter",
]
