"""Configuration schema for File Organizer.

Defines the top-level AppConfig and ModelPreset dataclasses that provide
a unified configuration interface across all modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ModelPreset:
    """Preset configuration for AI models.

    Args:
        text_model: Ollama model name for text processing.
        vision_model: Ollama model name for vision processing.
        temperature: Generation temperature (0.0-1.0).
        max_tokens: Maximum tokens for generation.
        device: Device for inference (auto, cpu, cuda, mps, metal).
        framework: Inference framework (ollama, llama_cpp, mlx).
    """

    text_model: str = "qwen2.5:3b-instruct-q4_K_M"
    vision_model: str = "qwen2.5vl:7b-q4_K_M"
    temperature: float = 0.5
    max_tokens: int = 3000
    device: str = "auto"
    framework: str = "ollama"


@dataclass
class AppConfig:
    """Top-level application configuration.

    All fields have defaults so ``AppConfig()`` works standalone.
    Module-specific configs are optional and only loaded when their
    feature is used.

    Args:
        profile_name: Name of this configuration profile.
        version: Configuration schema version.
        default_methodology: Default organization methodology (none, para, jd).
        models: AI model preset configuration.
        watcher: Watcher module config overrides.
        daemon: Daemon module config overrides.
        parallel: Parallel processing config overrides.
        pipeline: Pipeline config overrides.
        events: Event system config overrides.
        deploy: Deployment config overrides.
        para: PARA methodology config overrides.
        johnny_decimal: Johnny Decimal config overrides.
    """

    profile_name: str = "default"
    version: str = "1.0"
    default_methodology: str = "none"
    models: ModelPreset = field(default_factory=ModelPreset)

    # Module-specific config overrides stored as dicts.
    # Delegated to module config constructors by ConfigManager.
    watcher: Optional[dict[str, Any]] = None
    daemon: Optional[dict[str, Any]] = None
    parallel: Optional[dict[str, Any]] = None
    pipeline: Optional[dict[str, Any]] = None
    events: Optional[dict[str, Any]] = None
    deploy: Optional[dict[str, Any]] = None
    para: Optional[dict[str, Any]] = None
    johnny_decimal: Optional[dict[str, Any]] = None
