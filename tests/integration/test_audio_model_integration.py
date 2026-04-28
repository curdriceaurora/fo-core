"""Integration tests for AudioModel — end-to-end with faster-whisper.

Skipped automatically when the [media] extra is not installed.
"""

from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path

import pytest

from models.audio_model import AudioModel
from models.base import ModelConfig, ModelType


def _generate_silence_wav(path: Path, seconds: float = 1.0) -> None:
    """Write a mono 16-bit 16kHz silent WAV file at ``path``."""
    sample_rate = 16000
    n_frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        # 16-bit signed silence = b"\x00\x00" per sample
        w.writeframes(struct.pack("<h", 0) * n_frames)


@pytest.mark.integration
# Real-faster-whisper test — must run isolated from tests that mock
# sys.modules["torch"] / sys.modules["faster_whisper"] (notably in
# tests/integration/test_services_video_audio_extractor_transcriber.py).
# `--dist=loadgroup` (project default per xdist-safe-patterns Pattern 3)
# serializes everything in this group, so the mock fixtures in the other
# file can't pollute this test's view of the real packages.
@pytest.mark.xdist_group(name="audio_real_whisper")
class TestAudioModelEndToEnd:
    @pytest.fixture(autouse=True)
    def _require_real_faster_whisper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Defense against sys.modules pollution from peer tests that mock
        # faster_whisper / torch (T12 fixture-state-leak class). Clear any
        # cached module entries so AudioTranscriber gets the real package.
        for key in list(sys.modules.keys()):
            if (
                key == "faster_whisper"
                or key.startswith("faster_whisper.")
                or key == "ctranslate2"
                or key.startswith("ctranslate2.")
            ):
                monkeypatch.delitem(sys.modules, key, raising=False)
        pytest.importorskip("faster_whisper")

    def test_generate_returns_string_for_silent_wav(self, tmp_path: Path) -> None:
        audio_path = tmp_path / "silence.wav"
        _generate_silence_wav(audio_path, seconds=1.0)

        config = ModelConfig(name="tiny", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()
        try:
            output = model.generate(str(audio_path))
        finally:
            model.safe_cleanup()

        assert isinstance(output, str)
        # Silence may transcribe to empty or to a short artifact; both are
        # valid. The contract we're proving here is: pipeline runs end-to-end
        # without crashing. The isinstance(str) check above covers it; we
        # don't pin an upper-length bound because a) `len(x) <= N` is
        # T9-vacuous (passes for empty), and b) the actual transcription
        # quality of silence is non-deterministic across whisper versions.
