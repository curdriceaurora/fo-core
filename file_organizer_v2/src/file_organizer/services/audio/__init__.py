"""
Audio Processing Services

This module provides audio file processing capabilities including:
- Audio transcription using Whisper models
- Audio format preprocessing and conversion
- Audio metadata extraction
- Audio type classification
- Content-based organisation
- Audio content analysis
- Audio utility functions
"""

from .transcriber import AudioTranscriber, TranscriptionResult, TranscriptionOptions
from .metadata_extractor import AudioMetadataExtractor, AudioMetadata
from .preprocessor import AudioPreprocessor, AudioFormat
from .classifier import AudioClassifier, AudioType, ClassificationResult
from .organizer import AudioOrganizer, OrganizationRules, OrganizationResult, OrganizationPlan
from .content_analyzer import AudioContentAnalyzer, ContentAnalysis
from .utils import (
    get_audio_duration,
    normalize_audio,
    split_audio,
    convert_audio_format,
    validate_audio_file,
)

__all__ = [
    # Transcription
    "AudioTranscriber",
    "TranscriptionResult",
    "TranscriptionOptions",
    # Metadata
    "AudioMetadataExtractor",
    "AudioMetadata",
    # Preprocessing
    "AudioPreprocessor",
    "AudioFormat",
    # Classification
    "AudioClassifier",
    "AudioType",
    "ClassificationResult",
    # Organisation
    "AudioOrganizer",
    "OrganizationRules",
    "OrganizationResult",
    "OrganizationPlan",
    # Content Analysis
    "AudioContentAnalyzer",
    "ContentAnalysis",
    # Utilities
    "get_audio_duration",
    "normalize_audio",
    "split_audio",
    "convert_audio_format",
    "validate_audio_file",
]
