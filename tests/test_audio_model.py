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
