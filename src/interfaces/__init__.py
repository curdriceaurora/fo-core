"""Protocol-based interface definitions for file organizer components.

This package provides ``typing.Protocol`` contracts that define structural
interfaces for all major service boundaries.  Existing ABC-based
implementations satisfy these protocols without inheritance changes.

Modules:
    model: TextModelProtocol, VisionModelProtocol, AudioModelProtocol
    processor: FileProcessorProtocol, BatchProcessorProtocol
    storage: StorageProtocol, CacheProtocol
    intelligence: LearnerProtocol, ScorerProtocol
    pipeline: PipelineStage, StageContext
"""

from __future__ import annotations

from interfaces.intelligence import LearnerProtocol, ScorerProtocol
from interfaces.model import (
    AudioModelProtocol,
    TextModelProtocol,
    VisionModelProtocol,
)
from interfaces.pipeline import PipelineStage, StageContext
from interfaces.processor import (
    BatchProcessorProtocol,
    FileProcessorProtocol,
)
from interfaces.storage import MISSING, CacheProtocol, StorageProtocol

__all__ = [
    "AudioModelProtocol",
    "BatchProcessorProtocol",
    "CacheProtocol",
    "FileProcessorProtocol",
    "LearnerProtocol",
    "MISSING",
    "PipelineStage",
    "ScorerProtocol",
    "StageContext",
    "StorageProtocol",
    "TextModelProtocol",
    "VisionModelProtocol",
]
