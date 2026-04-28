"""Audio model — wraps services.audio.transcriber for the BaseModel interface."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from models.base import BaseModel, ModelConfig, ModelType

if TYPE_CHECKING:
    from services.audio.transcriber import AudioTranscriber, ModelSize, TranscriptionOptions


# F9-allowed lazy-import helper: services.audio.transcriber lives under
# services/, and services/__init__.py eagerly pulls in text_processor which
# in turn imports models.TextModel. Because models/__init__.py imports
# AudioModel at package init, a top-level import of services.audio.transcriber
# here closes the cycle and crashes models package init with
# "cannot import name 'TextModel' from partially initialized module 'models'".
# Deferring the import to first call breaks the cycle: by the time AudioModel
# is constructed, both packages are fully loaded.
def _audio_transcriber_classes() -> tuple[
    type[AudioTranscriber],
    type[ModelSize],
    type[TranscriptionOptions],
]:
    """Return ``(AudioTranscriber, ModelSize, TranscriptionOptions)`` lazily."""
    from services.audio.transcriber import (
        AudioTranscriber as _AudioTranscriber,
    )
    from services.audio.transcriber import (
        ModelSize as _ModelSize,
    )
    from services.audio.transcriber import (
        TranscriptionOptions as _TranscriptionOptions,
    )

    return _AudioTranscriber, _ModelSize, _TranscriptionOptions


def _resolve_model_size(name: str) -> ModelSize:
    """Map a ``ModelConfig.name`` to a faster-whisper ``ModelSize``.

    Accepts forms like ``"base"``, ``"whisper-base"``, ``"Whisper_Large-V3"``.
    Falls back to ``ModelSize.BASE`` for unknown names — keeps the call live
    rather than crashing on a config typo.
    """
    _, ModelSizeCls, _ = _audio_transcriber_classes()
    normalized = name.lower().replace("whisper-", "").replace("_", "-")
    for size in ModelSizeCls:
        if size.value == normalized:
            return size
    return ModelSizeCls.BASE


class AudioModel(BaseModel):
    """Audio transcription model backed by faster-whisper."""

    def __init__(self, config: ModelConfig):
        """Initialize the audio model and instantiate the lazy transcriber.

        Raises:
            ValueError: If ``config.model_type`` is not ``ModelType.AUDIO``.
        """
        if config.model_type != ModelType.AUDIO:
            raise ValueError(f"Expected AUDIO model type, got {config.model_type}")
        super().__init__(config)
        AudioTranscriber, _, _ = _audio_transcriber_classes()
        # `config.device` is always a `DeviceType` (defaults to AUTO in
        # ModelConfig), so we can read `.value` directly. The previous
        # `if config.device else "auto"` form had an unreachable else arm
        # under strict typing.
        self._transcriber = AudioTranscriber(
            model_size=_resolve_model_size(config.name),
            device=config.device.value,
        )

    def initialize(self) -> None:
        """Initialize the model. Whisper weights load lazily on first generate()."""
        super().initialize()

    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Transcribe an audio file.

        Args:
            prompt: Path to the audio file (treated as a filesystem path).
            **kwargs: Reserved for future per-call options. Currently ignored.

        Returns:
            Transcribed text. May be the empty string for silent or
            unintelligible audio.

        Raises:
            FileNotFoundError: If ``prompt`` does not name an existing file.
            ImportError: If the ``[media]`` extra is not installed
                (faster-whisper missing).
            RuntimeError: If the model is shutting down or not initialized.
        """
        self._enter_generate()
        try:
            _, _, TranscriptionOptions = _audio_transcriber_classes()
            options = TranscriptionOptions()
            result = self._transcriber.transcribe(Path(prompt), options=options)
            return result.text
        finally:
            self._exit_generate()

    def cleanup(self) -> None:
        """Cleanup model resources. Unloads the underlying Whisper model."""
        logger.debug("Cleaning up audio model")
        with self._lifecycle_lock:
            self._transcriber.unload_model()
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
