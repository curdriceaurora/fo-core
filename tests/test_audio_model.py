"""Unit tests for AudioModel — wires services.audio.transcriber."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.audio_model import AudioModel
from models.base import ModelConfig, ModelType


@pytest.mark.unit
@pytest.mark.ci
class TestAudioModelInit:
    def test_init_creates_transcriber_attribute(self) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        assert model._transcriber is not None
        assert hasattr(model._transcriber, "transcribe")

    def test_init_rejects_non_audio_model_type(self) -> None:
        # AudioModel.__init__ guards against being constructed with a non-
        # AUDIO ModelConfig — covers the ValueError raise. Without this
        # test the diff-coverage gate fails because every other call site
        # uses ModelType.AUDIO.
        bad_config = ModelConfig(name="base", model_type=ModelType.TEXT)
        with pytest.raises(ValueError, match="Expected AUDIO model type"):
            AudioModel(bad_config)


@pytest.mark.unit
@pytest.mark.ci
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
def test_resolve_model_size_maps_to_valid_size(name: str, expected_value: str) -> None:
    from models.audio_model import _resolve_model_size

    assert _resolve_model_size(name).value == expected_value


@pytest.mark.unit
@pytest.mark.ci
def test_default_config_resolves_to_valid_model_size() -> None:
    from models.audio_model import _resolve_model_size

    config = AudioModel.get_default_config()
    size = _resolve_model_size(config.name)
    # Must be one of the real ModelSize values (not the silent BASE fallback
    # that hides unrecognized names).
    assert size.value == config.name or size.value == config.name.replace("whisper-", "")


@pytest.mark.unit
@pytest.mark.ci
class TestAudioModelLifecycle:
    def test_initialize_sets_initialized_flag(self) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        assert model.is_initialized is False
        model.initialize()
        assert model.is_initialized is True

    def test_cleanup_unloads_transcriber(self) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()
        with patch.object(model._transcriber, "unload_model") as mock_unload:
            model.cleanup()
        mock_unload.assert_called_once()
        assert model.is_initialized is False


@pytest.mark.unit
@pytest.mark.ci
class TestAudioModelGenerate:
    def test_generate_returns_transcription_text(self, tmp_path: Path) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()

        fake_audio = tmp_path / "sample.wav"
        # No need to actually create the file: patching
        # `model._transcriber.transcribe` replaces AudioTranscriber.transcribe
        # entirely, including its `audio_path.exists()` check at
        # src/services/audio/transcriber.py:212. We pass the path through to
        # verify generate() forwards it correctly.

        fake_result = MagicMock()
        fake_result.text = "hello world"
        with patch.object(
            model._transcriber, "transcribe", return_value=fake_result
        ) as mock_transcribe:
            output = model.generate(str(fake_audio))

        assert output == "hello world"
        mock_transcribe.assert_called_once()
        # First positional arg is the audio path
        called_path = mock_transcribe.call_args.args[0]
        assert Path(called_path) == fake_audio


@pytest.mark.unit
@pytest.mark.ci
class TestAudioModelGenerateErrors:
    def test_generate_before_initialize_raises_runtime_error(self, tmp_path: Path) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        # Note: not calling initialize()
        fake_audio = tmp_path / "sample.wav"
        fake_audio.touch()
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate(str(fake_audio))

    def test_generate_after_shutdown_raises_runtime_error(self, tmp_path: Path) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()
        model.safe_cleanup()  # marks _shutting_down
        fake_audio = tmp_path / "sample.wav"
        fake_audio.touch()
        with pytest.raises(RuntimeError, match="shutting down"):
            model.generate(str(fake_audio))

    def test_generate_propagates_filenotfound(self, tmp_path: Path) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()
        missing = tmp_path / "does-not-exist.wav"
        with pytest.raises(FileNotFoundError):
            model.generate(str(missing))
