"""Unit tests for AudioModel — wires services.audio.transcriber."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.audio_model import AudioModel
from models.base import ModelConfig, ModelType


@pytest.mark.unit
class TestAudioModelInit:
    def test_init_creates_transcriber_attribute(self) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        assert model._transcriber is not None
        assert hasattr(model._transcriber, "transcribe")


@pytest.mark.unit
@pytest.mark.parametrize(
    "name,expected_value",
    [
        ("base", "base"),
        ("whisper-base", "base"),
        ("tiny", "tiny"),
        ("Whisper-Large-V3", "large-v3"),
        ("nonsense-model-name", "base"),  # falls back to BASE
    ],
)
def test_resolve_model_size_maps_to_valid_size(
    name: str, expected_value: str
) -> None:
    from models.audio_model import _resolve_model_size

    assert _resolve_model_size(name).value == expected_value
