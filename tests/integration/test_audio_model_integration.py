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
    def _require_real_faster_whisper(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        # The transcriber's underlying Whisper model is lazy-loaded on first
        # transcribe() call, so it should be None right after initialize().
        assert model._transcriber._model is None
        try:
            try:
                output = model.generate(str(audio_path))
            except Exception as exc:
                # First-run lazy load downloads the tiny model from
                # HuggingFace Hub. Skip cleanly on transient network /
                # rate-limit failures (403, connection reset, etc.) —
                # the test purpose is "does the pipeline run end-to-end
                # when the model is available", not "does HuggingFace
                # serve us a model right now". Catching by exception
                # message + module path keeps us decoupled from the
                # huggingface_hub error hierarchy (which changes
                # between versions).
                msg = repr(exc).lower()
                module_path = type(exc).__module__
                hf_indicators = (
                    "huggingface" in msg
                    or "huggingface_hub" in module_path
                    or "rate limit" in msg
                    or "403" in msg
                    or "429" in msg
                    or "connection" in msg
                    or "name resolution" in msg
                )
                if hf_indicators:
                    pytest.skip(f"Whisper tiny model download unavailable: {exc!r}")
                raise
            # Pipeline ran end-to-end without raising. Two non-vacuous
            # assertions cover the contract:
            #   1. generate() returned a string (not None, not an exception).
            #   2. The lazy-load fired: _transcriber._model is now populated.
            # Output text content is non-deterministic across whisper
            # versions for silent audio, so we don't pin its value.
            assert isinstance(output, str)
            assert model._transcriber._model is not None
        finally:
            model.safe_cleanup()
        # cleanup() unloads the model — _transcriber._model is None again.
        assert model._transcriber._model is None
