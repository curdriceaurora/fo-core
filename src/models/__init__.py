"""AI model interfaces and implementations."""

from __future__ import annotations

from models.analytics import (
    AnalyticsDashboard,
    DuplicateStats,
    FileDistribution,
    FileInfo,
    MetricsSnapshot,
    QualityMetrics,
    StorageStats,
    TimeSavings,
    TrendData,
)
from models.audio_model import AudioModel
from models.base import (
    BaseModel,
    DeviceType,
    ModelConfig,
    ModelType,
    TokenExhaustionError,
)
from models.provider_factory import get_text_model, get_vision_model
from models.suggestion_types import (
    ConfidenceFactors,
    ConfidenceLevel,
    Suggestion,
    SuggestionBatch,
    SuggestionType,
)
from models.text_model import TextModel
from models.vision_model import VisionModel

__all__ = [
    "BaseModel",
    "ModelConfig",
    "ModelType",
    "DeviceType",
    "TokenExhaustionError",
    "TextModel",
    "VisionModel",
    "get_text_model",
    "get_vision_model",
    "AudioModel",
    "Suggestion",
    "SuggestionType",
    "SuggestionBatch",
    "ConfidenceFactors",
    "ConfidenceLevel",
    "FileInfo",
    "StorageStats",
    "FileDistribution",
    "DuplicateStats",
    "QualityMetrics",
    "TimeSavings",
    "MetricsSnapshot",
    "TrendData",
    "AnalyticsDashboard",
]
