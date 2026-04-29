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

from core.dispatcher import (
    _maybe_transcribe,
    _to_transcription_result,
    process_audio_files,
)


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
@pytest.mark.integration
class TestMaybeTranscribe:
    """Direct coverage of the `_maybe_transcribe` helper.

    Marked both unit and integration so the dispatcher's per-module
    integration-coverage floor sees these branches — real-Whisper E2E
    tests can't exercise the duration-cap or recoverable-error paths
    without expensive setup, and the floor would otherwise regress.
    """

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

    def test_recovers_from_oserror(self, tmp_path: Path) -> None:
        # Codex P1 (PR #237 review): faster-whisper / ctranslate2 surface
        # malformed-audio decode failures via OSError. Without explicit
        # handling, the exception escapes the outer handler and marks
        # the file as failed in AUDIO_FALLBACK_FOLDER, regressing a file
        # that's otherwise classifiable from metadata alone.
        metadata = MagicMock(duration=30.0)
        transcriber = MagicMock()
        transcriber.generate.side_effect = OSError("decode error: malformed header")
        result = _maybe_transcribe(
            tmp_path / "a.mp3",
            metadata=metadata,
            transcriber=transcriber,
            max_transcribe_seconds=600,
        )
        assert result is None

    def test_recovers_from_valueerror(self, tmp_path: Path) -> None:
        # Same rationale as `test_recovers_from_oserror` — ValueError is
        # the second decode-failure shape (e.g. "unsupported audio format").
        metadata = MagicMock(duration=30.0)
        transcriber = MagicMock()
        transcriber.generate.side_effect = ValueError("unsupported audio codec")
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
@pytest.mark.integration
class TestToTranscriptionResult:
    """Direct coverage of the str → TranscriptionResult wrapper.

    The classifier's transcription-aware path expects a TranscriptionResult
    dataclass (not a raw str). This helper bridges AudioModel's str
    output to the classifier's input contract while degrading to None
    on missing/empty transcripts so the classifier's existing
    `if transcription is not None` guard stays the gating check.
    """

    def _stub_metadata(self, duration: float = 30.0) -> object:
        from services.audio.metadata_extractor import AudioMetadata

        return AudioMetadata(
            file_path=Path("stub.mp3"),
            file_size=1024,
            format="MP3",
            duration=duration,
            bitrate=128_000,
            sample_rate=44_100,
            channels=2,
        )

    def test_returns_none_for_none_transcript(self) -> None:
        assert _to_transcription_result(None, self._stub_metadata()) is None

    def test_returns_none_for_empty_string(self) -> None:
        # Empty transcripts must NOT pass through — the classifier would
        # otherwise score 'few words → music' on a silent failure case
        # and override the metadata-derived audio_type incorrectly.
        assert _to_transcription_result("", self._stub_metadata()) is None

    def test_wraps_text_into_transcription_result(self) -> None:
        from services.audio.transcriber import TranscriptionResult

        result = _to_transcription_result("hello world", self._stub_metadata(duration=120.0))
        assert isinstance(result, TranscriptionResult)
        assert result.text == "hello world"
        assert result.duration == 120.0
        # Empty segments — we don't have word-level timestamps from the
        # plain-string transcriber output, so the classifier's
        # speaker-count heuristic is intentionally inactive.
        assert result.segments == []


@pytest.mark.unit
@pytest.mark.ci
@pytest.mark.integration
class TestProcessAudioFilesTranscript:
    """Integration of `_maybe_transcribe` into `process_audio_files`.

    Same dual-marker rationale as `TestMaybeTranscribe` — covers the
    transcriber-not-set, attached-transcript, and cap-skip wiring branches
    inside `process_audio_files` for the integration coverage floor.
    """

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

    def test_transcript_influences_audio_classification(self, tmp_path: Path) -> None:
        # Codex P1 (PR #237 review): the dispatcher attached the transcript
        # but the classifier was called as `classify(metadata)` without it,
        # so transcription paid CPU cost without affecting folder routing.
        # This test pins the contract end-to-end: the dispatcher MUST pass
        # transcription= to classify() so transcript-derived audio_type
        # actually influences description and folder_name.
        from unittest.mock import patch

        audio = tmp_path / "a.mp3"
        audio.touch()

        transcriber = MagicMock()
        transcriber.generate.return_value = (
            "Welcome to the podcast. In this episode our guests discuss "
            "the latest interview series and listeners can subscribe."
        )
        # Spy on the classifier so we can assert it received the
        # transcription kwarg (not just metadata) — the missing wire was
        # the actual bug Codex caught.
        with patch(
            "services.audio.classifier.AudioClassifier.classify",
            autospec=True,
        ) as spy_classify:
            from services.audio.classifier import (
                AudioType,
                ClassificationResult,
            )

            spy_classify.return_value = ClassificationResult(
                audio_type=AudioType.PODCAST,
                confidence=0.9,
                reasoning="stubbed",
            )
            results = process_audio_files(
                [audio],
                extractor_cls=_stub_extractor_cls(duration=300.0),
                transcriber=transcriber,
                max_transcribe_seconds=600,
            )

        assert len(results) == 1
        assert results[0].transcript == transcriber.generate.return_value
        # The contract under test: the transcription kwarg was passed and
        # was not None — i.e. the transcript actually influenced the
        # classifier call.
        assert spy_classify.call_count == 1
        passed_transcription = spy_classify.call_args.kwargs.get("transcription")
        assert passed_transcription is not None
        assert passed_transcription.text == transcriber.generate.return_value


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

    def test_audio_model_reset_to_none_after_cleanup(self, tmp_path: Path) -> None:
        # Codex P2 + CodeRabbit Major (PR #237 review): the cleanup block
        # called `safe_cleanup()` but didn't reset the reference to None.
        # On a second `organize()` call the lazy-init `if is None` check
        # would skip re-initialize and `generate()` would raise per-file
        # — silently degrading transcription to metadata-only. The reset
        # lives in `_process_all_file_types`'s finally block; testing it
        # via the public `organize()` is brittle because the empty-input
        # short-circuit returns before the finally runs. Test the helper
        # directly with a pre-populated `_audio_model`.
        from core.organizer import FileOrganizer

        fake_audio_model = MagicMock()
        organizer = FileOrganizer(
            dry_run=True,
            transcribe_audio=True,
            max_transcribe_seconds=300.0,
        )
        organizer._audio_model = fake_audio_model

        # Empty file lists for every category so no file dispatch runs
        # but the finally block still fires.
        organizer._process_all_file_types([], [], [], [], [])

        fake_audio_model.safe_cleanup.assert_called_once()
        # The crucial assertion: the slot is reset so a second
        # organize() on the same FileOrganizer would lazy-init fresh.
        assert organizer._audio_model is None

    def test_preflight_skips_transcriber_when_faster_whisper_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex P2 (PR #237 review): `services.audio.transcriber` swallows
        # the faster_whisper ImportError at module-load and exposes
        # `_FASTER_WHISPER_AVAILABLE=False`. Without a pre-flight check,
        # `AudioModel(...)` construction succeeds and ImportError fires
        # per-file inside generate() — flooding the user with one warning
        # per file instead of the single organizer-level fallback warning.
        # This test pins the pre-flight: when the flag is False, the
        # organizer hits the ImportError branch BEFORE constructing
        # AudioModel and passes None to the dispatcher.
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
        # Force the pre-flight gate to fail. We patch the attribute on
        # services.audio.transcriber so the import inside the organizer
        # method picks up the False value.
        import services.audio.transcriber as transcriber_module

        monkeypatch.setattr(transcriber_module, "_FASTER_WHISPER_AVAILABLE", False)

        # AudioModel must NOT be constructed when the pre-flight fails.
        # Patch it to a sentinel that would raise if called.
        construction_calls: list[None] = []

        def _audio_model_should_not_be_constructed(*args: object, **kwargs: object) -> None:
            construction_calls.append(None)
            raise AssertionError(
                "AudioModel must not be constructed when faster_whisper is unavailable"
            )

        import models.audio_model as audio_model_module

        monkeypatch.setattr(
            audio_model_module, "AudioModel", _audio_model_should_not_be_constructed
        )

        organizer = FileOrganizer(dry_run=True, transcribe_audio=True)
        organizer._process_audio_files([tmp_path / "x.mp3"])

        assert captured["transcriber"] is None
        assert construction_calls == []  # pre-flight blocked construction

    def test_import_error_during_construction_falls_back_to_metadata_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Defense-in-depth: even if `_FASTER_WHISPER_AVAILABLE` is True
        # (e.g. faster_whisper imports but transitively fails), an
        # ImportError raised during AudioModel construction must still
        # degrade gracefully to metadata-only.
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

        # Force pre-flight to pass, then make AudioModel construction
        # raise to exercise the second fallback path.
        import services.audio.transcriber as transcriber_module

        monkeypatch.setattr(transcriber_module, "_FASTER_WHISPER_AVAILABLE", True)

        import models.audio_model as audio_model_module

        def _raise_import_error(*args: object, **kwargs: object) -> None:
            raise ImportError("transitive dep failed")

        monkeypatch.setattr(audio_model_module, "AudioModel", _raise_import_error)

        organizer = FileOrganizer(dry_run=True, transcribe_audio=True)
        organizer._process_audio_files([tmp_path / "x.mp3"])

        assert captured["transcriber"] is None
