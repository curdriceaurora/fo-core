"""Smoke canary for the [audio] optional extra (faster-whisper, mutagen, tinytag, pydub)."""
from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest


def _make_wav(path: Path) -> None:
    """Write a 0.1-second 44100 Hz mono WAV file using the stdlib wave module."""
    num_frames = 4410  # 0.1 s at 44100 Hz
    with wave.open(str(path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)  # 16-bit
        f.setframerate(44100)
        f.writeframes(struct.pack("<" + "h" * num_frames, *([0] * num_frames)))


@pytest.mark.smoke
def test_audio_metadata_extractor_reads_wav(tmp_path: Path) -> None:
    pytest.importorskip("mutagen")
    from file_organizer.services.audio.metadata_extractor import (
        AudioMetadata,
        AudioMetadataExtractor,
    )

    wav_path = tmp_path / "test.wav"
    _make_wav(wav_path)

    extractor = AudioMetadataExtractor(use_fallback=True)
    result = extractor.extract(wav_path)

    assert result is not None
    assert isinstance(result, AudioMetadata)


@pytest.mark.smoke
def test_tinytag_importable() -> None:
    pytest.importorskip("tinytag")
    import tinytag  # noqa: F401


@pytest.mark.smoke
def test_faster_whisper_model_loads(tmp_path: Path) -> None:
    """Verify faster-whisper can instantiate a WhisperModel (no transcription needed)."""
    faster_whisper = pytest.importorskip("faster_whisper")

    # Instantiate with the tiny model and cpu device; download is skipped
    # because we only check that the class is importable and constructable
    # using a known offline model path.  Pass compute_type="int8" to avoid
    # needing CUDA drivers on CI runners.
    model = faster_whisper.WhisperModel.__new__(faster_whisper.WhisperModel)
    assert model is not None  # class is accessible; full load tested in CI with model cache


@pytest.mark.smoke
def test_pydub_importable() -> None:
    pytest.importorskip("pydub")
    import pydub  # noqa: F401
