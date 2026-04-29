"""Unit tests for audio transcription in the organize/dispatcher path.

Step 2B threads `--transcribe-audio` from the CLI through `FileOrganizer`
into `core.dispatcher.process_audio_files`. This file pins the
dispatcher-level contract: transcriber call args, duration cap, and
graceful degradation when the transcriber raises.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.dispatcher import _maybe_transcribe, process_audio_files


def _stub_extractor_cls(*, duration: float) -> type:
    """Return an extractor_cls that produces a real AudioMetadata with the given duration.

    The classifier and organizer downstream of metadata extraction inspect
    real fields (``genre``, ``title``, ``duration``, ``extra_tags``), so a
    bare ``MagicMock`` triggers attribute-error spaghetti. Returning a real
    ``AudioMetadata`` keeps the test scope on the transcription gating
    contract while letting the rest of the pipeline run unchanged.
    """
    from services.audio.metadata_extractor import AudioMetadata

    # The metadata's file_path is opaque to the dispatcher logic under test
    # — only `duration` gates the transcription decision. Use Path() with a
    # bare filename rather than `/tmp/` (G2 rail blocks hardcoded /tmp paths
    # in tests; tmp_path here would require threading the fixture through a
    # module-level helper, which buys nothing).
    metadata = AudioMetadata(
        file_path=Path("stub.mp3"),
        file_size=1024,
        format="MP3",
        duration=duration,
        bitrate=128_000,
        sample_rate=44_100,
        channels=2,
    )
    extractor = MagicMock()
    extractor.extract.return_value = metadata
    extractor_cls = MagicMock(return_value=extractor)
    return extractor_cls


@pytest.mark.unit
@pytest.mark.ci
class TestMaybeTranscribe:
    """Direct coverage of the `_maybe_transcribe` helper."""

    def test_returns_none_when_transcriber_absent(self, tmp_path: Path) -> None:
        # Default path: no transcriber, no transcript. The metadata-only
        # categorization that organize has always done must remain
        # untouched when --transcribe-audio is off.
        metadata = MagicMock(duration=10.0)
        result = _maybe_transcribe(
            tmp_path / "a.mp3",
            metadata=metadata,
            transcriber=None,
            max_transcribe_seconds=600,
        )
        assert result is None

    def test_returns_transcript_when_within_cap(self, tmp_path: Path) -> None:
        metadata = MagicMock(duration=30.0)
        transcriber = MagicMock()
        transcriber.generate.return_value = "hello world"
        result = _maybe_transcribe(
            tmp_path / "a.mp3",
            metadata=metadata,
            transcriber=transcriber,
            max_transcribe_seconds=600,
        )
        assert result == "hello world"
        transcriber.generate.assert_called_once_with(str(tmp_path / "a.mp3"))

    def test_skips_when_duration_exceeds_cap(self, tmp_path: Path) -> None:
        # 20-minute file with a 10-minute cap. Whisper is ~5-10x realtime,
        # so this would be 1-2 minutes of CPU per file. Cap protects the
        # organize wall-clock from runaway transcription jobs.
        metadata = MagicMock(duration=1200.0)
        transcriber = MagicMock()
        result = _maybe_transcribe(
            tmp_path / "long.mp3",
            metadata=metadata,
            transcriber=transcriber,
            max_transcribe_seconds=600,
        )
        assert result is None
        transcriber.generate.assert_not_called()

    def test_uncapped_when_max_is_none(self, tmp_path: Path) -> None:
        metadata = MagicMock(duration=99999.0)
        transcriber = MagicMock()
        transcriber.generate.return_value = "long transcript"
        result = _maybe_transcribe(
            tmp_path / "long.mp3",
            metadata=metadata,
            transcriber=transcriber,
            max_transcribe_seconds=None,
        )
        assert result == "long transcript"

    def test_recovers_from_transcriber_error(self, tmp_path: Path) -> None:
        # Per-file failure must not abort the whole organize batch — it
        # falls back to metadata-only categorization for that one file.
        metadata = MagicMock(duration=30.0)
        transcriber = MagicMock()
        transcriber.generate.side_effect = RuntimeError("model crashed")
        result = _maybe_transcribe(
            tmp_path / "a.mp3",
            metadata=metadata,
            transcriber=transcriber,
            max_transcribe_seconds=600,
        )
        assert result is None

    def test_recovers_from_import_error(self, tmp_path: Path) -> None:
        # ImportError happens when the user passes --transcribe-audio but
        # [media] isn't installed. Dispatcher swallows it and proceeds —
        # the CLI layer is responsible for the upfront warning.
        metadata = MagicMock(duration=30.0)
        transcriber = MagicMock()
        transcriber.generate.side_effect = ImportError("faster-whisper not installed")
        result = _maybe_transcribe(
            tmp_path / "a.mp3",
            metadata=metadata,
            transcriber=transcriber,
            max_transcribe_seconds=600,
        )
        assert result is None


@pytest.mark.unit
@pytest.mark.ci
class TestProcessAudioFilesTranscript:
    """Integration of `_maybe_transcribe` into `process_audio_files`."""

    def test_no_transcriber_means_no_transcript_attached(self, tmp_path: Path) -> None:
        audio = tmp_path / "a.mp3"
        audio.touch()
        results = process_audio_files(
            [audio],
            extractor_cls=_stub_extractor_cls(duration=30.0),
            transcriber=None,
        )
        assert len(results) == 1
        assert results[0].transcript is None

    def test_transcriber_attaches_transcript_to_processed_file(self, tmp_path: Path) -> None:
        audio = tmp_path / "a.mp3"
        audio.touch()
        transcriber = MagicMock()
        transcriber.generate.return_value = "hello world"
        results = process_audio_files(
            [audio],
            extractor_cls=_stub_extractor_cls(duration=30.0),
            transcriber=transcriber,
            max_transcribe_seconds=600,
        )
        assert len(results) == 1
        assert results[0].transcript == "hello world"
        transcriber.generate.assert_called_once_with(str(audio))

    def test_transcribe_respects_duration_cap(self, tmp_path: Path) -> None:
        audio = tmp_path / "long.mp3"
        audio.touch()
        transcriber = MagicMock()
        results = process_audio_files(
            [audio],
            extractor_cls=_stub_extractor_cls(duration=1200.0),
            transcriber=transcriber,
            max_transcribe_seconds=600,
        )
        assert len(results) == 1
        assert results[0].transcript is None
        transcriber.generate.assert_not_called()


@pytest.mark.unit
@pytest.mark.ci
class TestFileOrganizerTranscribeFlag:
    """`FileOrganizer.transcribe_audio` threads through to dispatcher.

    Pins the wiring contract — without this, the CLI flag is wired but the
    dispatcher never sees the transcriber, so `--transcribe-audio` would
    be a silent no-op in production.
    """

    def test_transcribe_audio_flag_threads_transcriber_to_dispatcher(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.organizer import FileOrganizer

        captured: dict = {}

        def _spy(
            files: list[Path],
            *,
            extractor_cls: type | None = None,
            transcriber: object | None = None,
            max_transcribe_seconds: float | None = None,
        ) -> list:
            captured["transcriber"] = transcriber
            captured["max_seconds"] = max_transcribe_seconds
            return []

        monkeypatch.setattr("core.dispatcher.process_audio_files", _spy)
        # AudioModel lazy-init will run when transcribe_audio=True; mock
        # both the class and the model so we don't pay the import cost or
        # the initialize() cost during a unit test.
        fake_audio_model = MagicMock()
        monkeypatch.setattr(
            "models.audio_model.AudioModel", MagicMock(return_value=fake_audio_model)
        )

        organizer = FileOrganizer(
            dry_run=True,
            transcribe_audio=True,
            max_transcribe_seconds=300.0,
        )
        organizer._process_audio_files([tmp_path / "x.mp3"])

        assert captured["transcriber"] is fake_audio_model
        assert captured["max_seconds"] == 300.0
        fake_audio_model.initialize.assert_called_once()

    def test_transcribe_audio_disabled_passes_no_transcriber(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.organizer import FileOrganizer

        captured: dict = {}

        def _spy(
            files: list[Path],
            *,
            extractor_cls: type | None = None,
            transcriber: object | None = None,
            max_transcribe_seconds: float | None = None,
        ) -> list:
            captured["transcriber"] = transcriber
            return []

        monkeypatch.setattr("core.dispatcher.process_audio_files", _spy)

        organizer = FileOrganizer(dry_run=True, transcribe_audio=False)
        organizer._process_audio_files([tmp_path / "x.mp3"])

        assert captured["transcriber"] is None

    def test_import_error_falls_back_to_metadata_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When --transcribe-audio is requested but [media] isn't installed,
        # the organize batch must still complete — degrade to metadata-only
        # rather than aborting. The organizer surfaces a yellow warning so
        # the user knows why content-aware categorization didn't run.
        from core.organizer import FileOrganizer

        captured: dict = {}

        def _spy(
            files: list[Path],
            *,
            extractor_cls: type | None = None,
            transcriber: object | None = None,
            max_transcribe_seconds: float | None = None,
        ) -> list:
            captured["transcriber"] = transcriber
            return []

        monkeypatch.setattr("core.dispatcher.process_audio_files", _spy)
        # Replace the module-level AudioModel class itself so the local
        # `from models.audio_model import AudioModel` raises before the
        # dispatcher is even called.
        import models.audio_model as audio_model_module

        def _raise_import_error(*args: object, **kwargs: object) -> None:
            raise ImportError("faster-whisper is not installed")

        monkeypatch.setattr(audio_model_module, "AudioModel", _raise_import_error)

        organizer = FileOrganizer(dry_run=True, transcribe_audio=True)
        organizer._process_audio_files([tmp_path / "x.mp3"])

        assert captured["transcriber"] is None
