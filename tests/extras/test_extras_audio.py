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
    assert result.sample_rate == 44100
    assert result.duration == pytest.approx(0.1, abs=0.05)


@pytest.mark.smoke
def test_tinytag_importable() -> None:
    pytest.importorskip("tinytag")


@pytest.mark.smoke
def test_faster_whisper_importable() -> None:
    """Verify faster-whisper is installed and the WhisperModel class is accessible."""
    faster_whisper = pytest.importorskip("faster_whisper")
    assert hasattr(faster_whisper, "WhisperModel")


@pytest.mark.smoke
def test_pydub_importable() -> None:
    import pydub  # noqa: F401 — assert the dep is present in the [audio] extra
