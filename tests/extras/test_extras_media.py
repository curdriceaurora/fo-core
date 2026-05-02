"""Smoke canary for the media extra (faster-whisper, torch, pydub, opencv, scenedetect)."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def _require_media() -> None:
    # Skip cleanly when the [media] extra is absent rather than surfacing
    # cryptic AttributeError / ImportError noise from partially-installed envs.
    # The ci-extras [media] job runs `pip install -e ".[dev,media]"` followed
    # by an explicit `python -c "import faster_whisper; import cv2; print('OK')"`
    # verify-imports step before this canary executes — that step is the
    # authoritative "extra is broken" signal, so importorskip here doesn't
    # weaken the canary contract.
    # See: .claude/rules/test-generation-patterns.md T8 (MISSING_IMPORT_GUARD).
    pytest.importorskip("cv2")
    pytest.importorskip("faster_whisper")
    pytest.importorskip("pydub")
    pytest.importorskip("scenedetect")
    pytest.importorskip("torch")


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(struct.pack("<" + "h" * 4410, *([0] * 4410)))


def test_pydub_reads_wav(tmp_path: Path) -> None:
    from pydub import AudioSegment

    wav_path = tmp_path / "clip.wav"
    _make_wav(wav_path)
    seg = AudioSegment.from_wav(str(wav_path))
    assert seg.frame_rate == 44100


def test_opencv_read_write_cycle(tmp_path: Path) -> None:
    import cv2
    import numpy as np

    video_path = str(tmp_path / "clip.mp4")
    out = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 64))
    assert out.isOpened(), "VideoWriter failed to open — mp4v codec unavailable"
    for _ in range(5):
        out.write(np.zeros((64, 64, 3), dtype=np.uint8))
    out.release()

    cap = cv2.VideoCapture(video_path)
    frames = []
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)
    cap.release()
    assert len(frames) == 5


def test_faster_whisper_importable() -> None:
    from faster_whisper import WhisperModel  # noqa: F401
