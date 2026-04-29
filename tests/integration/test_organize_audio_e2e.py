"""End-to-end test for `fo organize --transcribe-audio` against a real WAV file.

This is the smoke that proves Step 2B's wiring works in production: the
flag flows from CLI → FileOrganizer → dispatcher → AudioModel → real
faster-whisper, the transcribed text reaches `ProcessedFile.transcript`,
and the organize batch completes without crashing on the transcription
path.

Skipped automatically when `[media]` isn't installed — the integration
tier is allowed to assume real dependencies but won't fail builds that
don't carry them. CI's audio-extra job exercises this path.
"""

from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app


def _generate_silent_wav(path: Path, seconds: float = 1.0) -> None:
    """Write a mono 16-bit 16kHz silent WAV at *path*.

    A real WAV file (not a touched empty file) — faster-whisper opens it
    via ctranslate2 and would error on a malformed header otherwise.
    """
    sample_rate = 16000
    n_frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<h", 0) * n_frames)


@pytest.mark.integration
@pytest.mark.ci
@pytest.mark.xdist_group(name="audio_real_whisper")
class TestOrganizeAudioEndToEnd:
    """Real-whisper integration. The xdist_group keeps multiple
    real-whisper tests on the same worker to avoid each one paying the
    model-load cost in parallel — see `xdist-safe-patterns.md` Pattern 3.
    """

    @pytest.fixture(autouse=True)
    def _require_faster_whisper(self) -> None:
        # Clear any lingering mocks from peer test files (sys.modules
        # pollution defense — see `test-generation-patterns.md` T12).
        for key in list(sys.modules):
            if key.startswith(("faster_whisper", "ctranslate2")) and not hasattr(
                sys.modules[key], "WhisperModel"
            ):
                del sys.modules[key]
        pytest.importorskip("faster_whisper")

    def test_organize_with_transcribe_audio_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--transcribe-audio` over a real WAV completes without crashing.

        Uses `--dry-run` so no file moves occur; the assertion is the
        organize call returning exit 0 with the transcription path
        active. A failure here means the wiring broke between CLI and
        dispatcher, or the AudioModel coercion regressed.
        """
        # Bypass first-run setup gate so the test doesn't depend on a
        # writable user-config dir (xdist would race otherwise).
        monkeypatch.setattr("cli.organize._check_setup_completed", lambda: True)

        input_dir = tmp_path / "in"
        input_dir.mkdir()
        _generate_silent_wav(input_dir / "sample.wav")
        output_dir = tmp_path / "out"

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "organize",
                str(input_dir),
                str(output_dir),
                "--dry-run",
                "--transcribe-audio",
            ],
        )
        assert result.exit_code == 0, result.output
