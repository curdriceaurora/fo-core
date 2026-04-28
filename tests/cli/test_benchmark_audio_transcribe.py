"""Test that fo benchmark run --transcribe-smoke exercises AudioModel.generate()."""

from __future__ import annotations

import struct
import wave
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.main import app


def _generate_silence_wav(path: Path, seconds: float = 0.5) -> None:
    """Write a mono 16-bit 16kHz silent WAV file at ``path``."""
    sample_rate = 16000
    n_frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<h", 0) * n_frames)


@pytest.mark.integration
class TestBenchmarkTranscribeSmoke:
    def test_transcribe_smoke_invokes_audio_model_once(
        self, tmp_path: Path
    ) -> None:
        """With --transcribe-smoke, exactly one AudioModel.generate call
        happens regardless of the candidate count."""
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        _generate_silence_wav(audio_dir / "a.wav")
        _generate_silence_wav(audio_dir / "b.wav")

        runner = CliRunner()
        with patch("cli.benchmark.AudioModel") as mock_model_cls:
            instance = mock_model_cls.return_value
            instance.generate.return_value = ""
            result = runner.invoke(
                app,
                [
                    "benchmark", "run",
                    str(audio_dir),
                    "--suite", "audio",
                    "--transcribe-smoke",
                    "--iterations", "1",
                    "--warmup", "0",
                ],
            )

        assert result.exit_code == 0, result.output
        # Smoke contract: exactly one transcription per benchmark run, even
        # when multiple candidate audio files are present. Counts may exceed
        # 1 across iterations — for this test iterations=1 so the one call
        # is unambiguous.
        assert instance.generate.call_count == 1

    def test_no_transcribe_smoke_means_no_audio_model_call(
        self, tmp_path: Path
    ) -> None:
        """Default benchmark (no flag) must not instantiate AudioModel."""
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        _generate_silence_wav(audio_dir / "a.wav")

        runner = CliRunner()
        with patch("cli.benchmark.AudioModel") as mock_model_cls:
            result = runner.invoke(
                app,
                [
                    "benchmark", "run",
                    str(audio_dir),
                    "--suite", "audio",
                    "--iterations", "1",
                    "--warmup", "0",
                ],
            )

        assert result.exit_code == 0, result.output
        mock_model_cls.assert_not_called()
