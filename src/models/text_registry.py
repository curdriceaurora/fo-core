"""Text model registry - domain-specific metadata for text models.

Extends :class:`~models.registry.ModelInfo` with
text-specific fields like context window and max tokens.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.registry import ModelInfo


@dataclass
class TextModelInfo(ModelInfo):
    """Metadata for a text-processing model.

    Extends :class:`ModelInfo` with fields that matter for text
    generation and summarization workloads.

    Attributes:
        context_window: Maximum input context in tokens.
        max_tokens: Maximum output tokens per generation.
    """

    context_window: int = 4096
    max_tokens: int = 3000


TEXT_MODELS: list[TextModelInfo] = [
    TextModelInfo(
        name="gemma3:4b",
        model_type="text",
        size="3.3 GB",
        quantization="",
        description="Default text+vision model - multimodal, fast, fits 8 GB RAM.",
        context_window=128000,
        max_tokens=3000,
    ),
    TextModelInfo(
        name="gemma3:12b",
        model_type="text",
        size="8.1 GB",
        quantization="",
        description="Larger multimodal model - higher quality, requires ≥16 GB RAM.",
        context_window=128000,
        max_tokens=4096,
    ),
    TextModelInfo(
        name="qwen2.5:3b-instruct-q4_K_M",
        model_type="text",
        size="1.9 GB",
        quantization="q4_K_M",
        description="Legacy text model - fast file description & naming.",
        context_window=4096,
        max_tokens=3000,
    ),
    TextModelInfo(
        name="qwen2.5:7b-instruct-q4_K_M",
        model_type="text",
        size="4.4 GB",
        quantization="q4_K_M",
        description="Legacy larger text model - higher quality, slower.",
        context_window=8192,
        max_tokens=4096,
    ),
]
