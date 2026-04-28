"""Test that fo benchmark run --transcribe-smoke exercises AudioModel.generate()."""

from __future__ import annotations

import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from cli.benchmark import (
    _bind_transcribe_smoke,
    _exit_if_transcribe_smoke_failed,
    _run_audio_suite,
)
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


@pytest.mark.unit
@pytest.mark.ci
class TestBindTranscribeSmoke:
    """Direct coverage of the _bind_transcribe_smoke helper. Cheap unit tests
    so the CI diff-coverage gate sees the new branches without needing the
    full Typer/Click stack."""

    def test_returns_unchanged_runner_when_smoke_off(self) -> None:
        sentinel = MagicMock(name="default_runner")
        result = _bind_transcribe_smoke(sentinel, suite="audio", transcribe_smoke=False)
        assert result is sentinel

    def test_returns_unchanged_runner_when_non_audio_and_smoke_off(self) -> None:
        sentinel = MagicMock(name="io_runner")
        result = _bind_transcribe_smoke(sentinel, suite="io", transcribe_smoke=False)
        assert result is sentinel

    def test_raises_bad_parameter_for_smoke_on_non_audio(self) -> None:
        sentinel = MagicMock(name="io_runner")
        with pytest.raises(typer.BadParameter, match="audio"):
            _bind_transcribe_smoke(sentinel, suite="io", transcribe_smoke=True)

    def test_binds_transcribe_smoke_for_audio_suite(self) -> None:
        sentinel = MagicMock(name="default_audio_runner")
        result = _bind_transcribe_smoke(sentinel, suite="audio", transcribe_smoke=True)
        # Returns a functools.partial pre-bound to _run_audio_suite — not the
        # sentinel default audio runner.
        assert result is not sentinel
        assert getattr(result, "func", None) is _run_audio_suite
        assert result.keywords == {"transcribe_smoke": True}


@pytest.mark.unit
@pytest.mark.ci
class TestExitIfTranscribeSmokeFailed:
    """Direct coverage of the _exit_if_transcribe_smoke_failed helper."""

    def test_no_exit_when_reason_absent(self) -> None:
        console = MagicMock()
        # Should NOT raise when the smoke-skipped reason isn't present.
        _exit_if_transcribe_smoke_failed(console, ["audio-synthesized-metadata-fallback"])
        console.print.assert_not_called()

    def test_exits_nonzero_when_smoke_skipped_reason_present(self) -> None:
        console = MagicMock()
        with pytest.raises(typer.Exit) as exc_info:
            _exit_if_transcribe_smoke_failed(console, ["audio-transcribe-smoke-skipped"])
        assert exc_info.value.exit_code == 1
        # Error message goes to console so the human reader sees the cause.
        console.print.assert_called_once()
        msg = console.print.call_args.args[0]
        assert "transcribe-smoke" in msg.lower()
        assert "media" in msg.lower()


@pytest.mark.integration
@pytest.mark.ci
class TestBenchmarkTranscribeSmoke:
    def test_transcribe_smoke_invokes_audio_model_once(self, tmp_path: Path) -> None:
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
                    "benchmark",
                    "run",
                    str(audio_dir),
                    "--suite",
                    "audio",
                    "--transcribe-smoke",
                    "--iterations",
                    "1",
                    "--warmup",
                    "0",
                ],
            )

        assert result.exit_code == 0, result.output
        # Smoke contract: exactly one transcription per benchmark run, even
        # when multiple candidate audio files are present. Counts may exceed
        # 1 across iterations — for this test iterations=1 so the one call
        # is unambiguous.
        assert instance.generate.call_count == 1

    def test_no_transcribe_smoke_means_no_audio_model_call(self, tmp_path: Path) -> None:
        """Default benchmark (no flag) must not instantiate AudioModel."""
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        _generate_silence_wav(audio_dir / "a.wav")

        runner = CliRunner()
        with patch("cli.benchmark.AudioModel") as mock_model_cls:
            result = runner.invoke(
                app,
                [
                    "benchmark",
                    "run",
                    str(audio_dir),
                    "--suite",
                    "audio",
                    "--iterations",
                    "1",
                    "--warmup",
                    "0",
                ],
            )

        assert result.exit_code == 0, result.output
        mock_model_cls.assert_not_called()

    def test_transcribe_smoke_with_non_audio_suite_fails_fast(self, tmp_path: Path) -> None:
        """--transcribe-smoke without --suite audio must error, not silently
        no-op. Otherwise CI scripts that combine the wrong flags get a false
        positive (exit 0 with no smoke verification done)."""
        # Arrange a non-empty input dir so the early "no files" path doesn't
        # short-circuit before we hit the validation.
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "x.txt").write_text("seed", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "benchmark",
                "run",
                str(input_dir),
                "--suite",
                "io",
                "--transcribe-smoke",
                "--iterations",
                "1",
                "--warmup",
                "0",
            ],
        )

        assert result.exit_code != 0
        # Rich panels at the CI runner's narrower default width wrap
        # `--transcribe-smoke` mid-token in unpredictable spots (after the
        # dashes, between syllables). Asserting on individual word
        # components is wrap-immune.
        normalized = " ".join(result.output.lower().split())
        assert "transcribe" in normalized
        assert "smoke" in normalized
        assert "audio" in normalized

    def test_transcribe_smoke_exits_nonzero_when_audio_model_unavailable(
        self, tmp_path: Path
    ) -> None:
        """When --transcribe-smoke is requested but AudioModel raises
        ImportError (e.g. [media] missing), the run must exit non-zero so
        CI doesn't treat the silently-skipped smoke as a passing audio
        benchmark."""
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        _generate_silence_wav(audio_dir / "a.wav")

        runner = CliRunner()
        with patch("cli.benchmark.AudioModel") as mock_model_cls:
            mock_model_cls.side_effect = ImportError("faster-whisper is not installed")
            result = runner.invoke(
                app,
                [
                    "benchmark",
                    "run",
                    str(audio_dir),
                    "--suite",
                    "audio",
                    "--transcribe-smoke",
                    "--iterations",
                    "1",
                    "--warmup",
                    "0",
                ],
            )

        assert result.exit_code != 0
        # Both the warning (from _run_audio_suite) and the failure error
        # (from run()) should appear so the user sees the cause and the
        # consequence. Normalize whitespace and use word-component
        # assertions to survive Rich panel wrap on narrow terminals.
        normalized = " ".join(result.output.lower().split())
        assert "transcribe" in normalized
        assert "smoke" in normalized
        assert "media" in normalized
