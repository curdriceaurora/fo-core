"""Audio model — wraps services.audio.transcriber for the BaseModel interface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from models.base import BaseModel, ModelConfig, ModelType

if TYPE_CHECKING:
    from services.audio.transcriber import AudioTranscriber, ModelSize


# F9-allowed lazy-import helper: services.audio.transcriber lives under
# services/, and services/__init__.py eagerly pulls in text_processor which
# in turn imports models.TextModel. Because models/__init__.py imports
# AudioModel at package init, a top-level import of services.audio.transcriber
# here closes the cycle and crashes models package init with
# "cannot import name 'TextModel' from partially initialized module 'models'".
# Deferring the import to first call breaks the cycle: by the time AudioModel
# is constructed, both packages are fully loaded.
def _audio_transcriber_classes() -> tuple[type, type, type]:
    """Return ``(AudioTranscriber, ModelSize, TranscriptionOptions)`` lazily."""
    from services.audio.transcriber import (
        AudioTranscriber,
        ModelSize,
        TranscriptionOptions,
    )
    return AudioTranscriber, ModelSize, TranscriptionOptions


def _resolve_model_size(name: str) -> ModelSize:
    """Map a ``ModelConfig.name`` to a faster-whisper ``ModelSize``.

    Accepts forms like ``"base"``, ``"whisper-base"``, ``"Whisper_Large-V3"``.
    Falls back to ``ModelSize.BASE`` for unknown names — keeps the call live
    rather than crashing on a config typo.
    """
    _, ModelSize, _ = _audio_transcriber_classes()
    normalized = name.lower().replace("whisper-", "").replace("_", "-")
    for size in ModelSize:
        if size.value == normalized:
            return size
    return ModelSize.BASE


class AudioModel(BaseModel):
    """Audio transcription model backed by faster-whisper."""

    def __init__(self, config: ModelConfig):
        if config.model_type != ModelType.AUDIO:
            raise ValueError(f"Expected AUDIO model type, got {config.model_type}")
        super().__init__(config)
        AudioTranscriber, _, _ = _audio_transcriber_classes()
        self._transcriber = AudioTranscriber(
            model_size=_resolve_model_size(config.name),
            device=config.device.value if config.device else "auto",
        )

    def initialize(self) -> None:
        """Initialize the model. Whisper weights load lazily on first generate()."""
        super().initialize()

    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Transcribe audio (not implemented yet).

        Args:
            prompt: Audio file path
            **kwargs: Additional parameters

        Returns:
            Transcribed text

        Raises:
            NotImplementedError: Audio processing not implemented yet
        """
        raise NotImplementedError("Audio processing will be implemented in Phase 3")

    def cleanup(self) -> None:
        """Cleanup model resources."""
        logger.debug("Cleaning up audio model")
        with self._lifecycle_lock:
            self._initialized = False

    @staticmethod
    def get_default_config(model_name: str = "base") -> ModelConfig:
        """Get default configuration for audio model.

        Default is faster-whisper ``"base"`` (~150 MB, multilingual). Override
        via ``model_name`` for ``"tiny"``, ``"small"``, ``"large-v3"``, etc.

        Args:
            model_name: Name of the audio model

        Returns:
            Default model configuration
        """
        return ModelConfig(
            name=model_name,
            model_type=ModelType.AUDIO,
            framework="faster-whisper",
            temperature=0.0,
            max_tokens=1000,
        )
