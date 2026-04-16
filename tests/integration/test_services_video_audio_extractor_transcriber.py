"""Integration tests for video scene detector, audio metadata extractor, and transcriber."""

from __future__ import annotations

import csv
import struct
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_wav(path: Path, duration_ms: int = 500, sample_rate: int = 16000) -> Path:
    """Write a minimal valid WAV file."""
    num_samples = int(sample_rate * duration_ms / 1000)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))
    return path


def _make_audio_metadata(file_path: Path, **kwargs: Any) -> Any:
    from services.audio.metadata_extractor import AudioMetadata

    defaults: dict[str, Any] = {
        "file_size": 512 * 1024,
        "format": "MP3",
        "duration": 180.0,
        "bitrate": 128000,
        "sample_rate": 44100,
        "channels": 2,
    }
    defaults.update(kwargs)
    return AudioMetadata(file_path=file_path, **defaults)


def _make_transcription_options(**kwargs: Any) -> Any:
    from services.audio.transcriber import TranscriptionOptions

    return TranscriptionOptions(**kwargs)


# ---------------------------------------------------------------------------
# SceneDetector — init and dependency check
# ---------------------------------------------------------------------------


def _mock_cv2_module() -> MagicMock:
    """Return a minimal mock cv2 module (no real cv2 import)."""
    m = MagicMock()
    m.CAP_PROP_FPS = 1
    m.CAP_PROP_FRAME_COUNT = 7
    m.COLOR_BGR2GRAY = 6
    return m


class TestSceneDetectorInit:
    """Tests for SceneDetector initialisation."""

    def test_default_params_stored(self) -> None:
        from services.video.scene_detector import DetectionMethod, SceneDetector

        with patch.dict("sys.modules", {"cv2": _mock_cv2_module(), "scenedetect": None}):
            detector = SceneDetector()

        assert detector.method == DetectionMethod.CONTENT
        assert detector.threshold == pytest.approx(27.0)
        assert detector.min_scene_length == pytest.approx(1.0)

    def test_custom_params_stored(self) -> None:
        from services.video.scene_detector import DetectionMethod, SceneDetector

        with patch.dict("sys.modules", {"cv2": _mock_cv2_module(), "scenedetect": None}):
            detector = SceneDetector(
                method=DetectionMethod.THRESHOLD, threshold=15.0, min_scene_length=2.5
            )
        assert detector.method == DetectionMethod.THRESHOLD
        assert detector.threshold == pytest.approx(15.0)
        assert detector.min_scene_length == pytest.approx(2.5)

    def test_missing_cv2_logs_warning(self) -> None:
        from services.video.scene_detector import SceneDetector

        with patch.dict("sys.modules", {"cv2": None, "scenedetect": None}):
            # Should not raise
            detector = SceneDetector()
        assert detector is not None

    def test_missing_scenedetect_does_not_raise(self) -> None:
        from services.video.scene_detector import SceneDetector

        with patch.dict("sys.modules", {"cv2": _mock_cv2_module(), "scenedetect": None}):
            detector = SceneDetector()
        assert detector is not None


class TestDetectionMethod:
    """Tests for DetectionMethod enum."""

    def test_all_methods_present(self) -> None:
        from services.video.scene_detector import DetectionMethod

        values = {m.value for m in DetectionMethod}
        assert "content" in values
        assert "threshold" in values
        assert "adaptive" in values
        assert "histogram" in values


class TestSceneDetectorRaisesForMissingFile:
    """Test that detect_scenes raises FileNotFoundError for missing video."""

    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        from services.video.scene_detector import SceneDetector

        with patch.dict("sys.modules", {"cv2": _mock_cv2_module(), "scenedetect": None}):
            detector = SceneDetector()
            with pytest.raises(FileNotFoundError, match="Video file not found"):
                detector.detect_scenes(tmp_path / "nonexistent.mp4")


def _build_mock_cv2(fps: float = 25.0, total_frames: int = 50) -> MagicMock:
    """Build a complete mock cv2 module with a VideoCapture that returns black frames."""

    mock_cv2 = MagicMock()
    mock_cv2.CAP_PROP_FPS = 1
    mock_cv2.CAP_PROP_FRAME_COUNT = 7
    mock_cv2.COLOR_BGR2GRAY = 6

    # black frame as a simple object that numpy-like operations can work on
    black_frame = MagicMock()
    black_frame.__len__ = MagicMock(return_value=120)

    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.get.side_effect = lambda prop: fps if prop == 1 else total_frames
    cap.read.side_effect = [(True, black_frame)] * total_frames + [(False, None)]
    mock_cv2.VideoCapture.return_value = cap

    return mock_cv2


def _build_mock_numpy_grayscale(mean_diff: float = 0.0) -> MagicMock:
    """Build a mock numpy module whose mean() returns the given value."""
    mock_np = MagicMock()
    mock_np.mean.return_value = mean_diff
    # absdiff returns a mock that mean() will be called on
    mock_np.zeros = MagicMock(return_value=MagicMock())
    return mock_np


class TestSceneDetectorWithOpenCV:
    """Tests for SceneDetector._detect_with_opencv using fully mocked cv2/numpy."""

    def test_single_scene_detected_for_uniform_video(self, tmp_path: Path) -> None:
        from services.video.scene_detector import SceneDetector

        video_file = tmp_path / "video.mp4"
        video_file.touch()

        mock_cv2 = _build_mock_cv2(fps=25.0, total_frames=50)
        mock_np = MagicMock()
        mock_np.mean.return_value = 0.0  # uniform → no scene changes

        with patch.dict("sys.modules", {"cv2": mock_cv2, "numpy": mock_np, "scenedetect": None}):
            detector = SceneDetector(threshold=27.0)
            result = detector._detect_with_opencv(video_file, threshold=27.0)

        assert len(result.scenes) >= 1
        assert result.fps == pytest.approx(25.0)
        assert result.total_frames == 50

    def test_scene_change_detected_on_large_diff(self, tmp_path: Path) -> None:
        from services.video.scene_detector import SceneDetector

        video_file = tmp_path / "video.mp4"
        video_file.touch()

        # Simulate a large mean diff on the 31st frame read (after min_scene_frames)
        call_count = 0

        def _mean(arr: Any) -> float:
            nonlocal call_count
            call_count += 1
            # After 10 frames, return large diff to trigger scene change
            return 200.0 if call_count > 10 else 0.0

        mock_cv2 = _build_mock_cv2(fps=10.0, total_frames=30)
        mock_np = MagicMock()
        mock_np.mean.side_effect = _mean

        with patch.dict("sys.modules", {"cv2": mock_cv2, "numpy": mock_np, "scenedetect": None}):
            detector = SceneDetector(threshold=10.0, min_scene_length=1.0)
            result = detector._detect_with_opencv(video_file, threshold=10.0)

        assert len(result.scenes) >= 2

    def test_detect_scenes_falls_back_to_opencv_when_scenedetect_missing(
        self, tmp_path: Path
    ) -> None:
        from services.video.scene_detector import SceneDetector

        video_file = tmp_path / "clip.mp4"
        video_file.touch()

        mock_cv2 = _build_mock_cv2(fps=10.0, total_frames=10)
        mock_np = MagicMock()
        mock_np.mean.return_value = 0.0

        with patch.dict("sys.modules", {"cv2": mock_cv2, "numpy": mock_np, "scenedetect": None}):
            detector = SceneDetector()
            result = detector.detect_scenes(video_file)

        assert result.video_path == video_file
        assert len(result.scenes) >= 1

    def test_opencv_raises_if_cap_cannot_open(self, tmp_path: Path) -> None:
        from services.video.scene_detector import SceneDetector

        video_file = tmp_path / "bad.mp4"
        video_file.touch()

        mock_cv2 = MagicMock()
        mock_cv2.CAP_PROP_FPS = 1
        mock_cv2.CAP_PROP_FRAME_COUNT = 7
        cap = MagicMock()
        cap.isOpened.return_value = False
        mock_cv2.VideoCapture.return_value = cap

        mock_np = MagicMock()

        with patch.dict("sys.modules", {"cv2": mock_cv2, "numpy": mock_np, "scenedetect": None}):
            detector = SceneDetector()
            with pytest.raises(ValueError, match="Failed to open video"):
                detector._detect_with_opencv(video_file, threshold=27.0)


class TestSceneDetectorSaveSceneList:
    """Tests for SceneDetector.save_scene_list."""

    def test_saves_csv_with_correct_headers(self, tmp_path: Path) -> None:
        from services.video.scene_detector import (
            DetectionMethod,
            Scene,
            SceneDetectionResult,
            SceneDetector,
        )

        scene = Scene(
            scene_number=1,
            start_time=0.0,
            end_time=5.0,
            start_frame=0,
            end_frame=125,
            duration=5.0,
            score=0.8,
            frame_count=125,
        )
        result = SceneDetectionResult(
            video_path=tmp_path / "v.mp4",
            scenes=[scene],
            total_duration=5.0,
            fps=25.0,
            total_frames=125,
            method=DetectionMethod.CONTENT,
        )

        out = tmp_path / "scenes.csv"
        SceneDetector.save_scene_list(result, out)

        assert out.exists()
        rows = list(csv.reader(out.open()))
        assert rows[0] == [
            "Scene",
            "Start Time",
            "End Time",
            "Duration",
            "Start Frame",
            "End Frame",
            "Frame Count",
            "Score",
        ]
        assert rows[1][0] == "1"
        assert rows[1][1] == "0.00"

    def test_saves_empty_scene_list(self, tmp_path: Path) -> None:
        from services.video.scene_detector import (
            DetectionMethod,
            SceneDetectionResult,
            SceneDetector,
        )

        result = SceneDetectionResult(
            video_path=tmp_path / "v.mp4",
            scenes=[],
            total_duration=0.0,
            fps=25.0,
            total_frames=0,
            method=DetectionMethod.THRESHOLD,
        )
        out = tmp_path / "empty.csv"
        SceneDetector.save_scene_list(result, out)

        rows = list(csv.reader(out.open()))
        assert len(rows) == 1  # header only


class TestSceneDetectorBatch:
    """Tests for SceneDetector.detect_scenes_batch."""

    def test_batch_skips_missing_files(self, tmp_path: Path) -> None:
        from services.video.scene_detector import SceneDetector

        with patch.dict("sys.modules", {"cv2": _mock_cv2_module(), "scenedetect": None}):
            detector = SceneDetector()
            results = detector.detect_scenes_batch(
                [tmp_path / "missing1.mp4", tmp_path / "missing2.mp4"]
            )
        # Both raise FileNotFoundError → caught and skipped
        assert results == []

    def test_batch_returns_results_for_successful_detections(self, tmp_path: Path) -> None:
        from services.video.scene_detector import SceneDetector

        video1 = tmp_path / "v1.mp4"
        video2 = tmp_path / "v2.mp4"
        video1.touch()
        video2.touch()

        black = MagicMock()

        def _make_cap(total: int = 10) -> MagicMock:
            cap = MagicMock()
            cap.isOpened.return_value = True
            # prop 1 = CAP_PROP_FPS, anything else = total_frames
            cap.get.side_effect = lambda prop: 10.0 if prop == 1 else total
            cap.read.side_effect = [(True, black)] * total + [(False, None)]
            return cap

        mock_cv2 = MagicMock()
        mock_cv2.CAP_PROP_FPS = 1
        mock_cv2.CAP_PROP_FRAME_COUNT = 7
        mock_cv2.VideoCapture.side_effect = [_make_cap(), _make_cap()]
        mock_np = MagicMock()
        mock_np.mean.return_value = 0.0

        with patch.dict("sys.modules", {"cv2": mock_cv2, "numpy": mock_np, "scenedetect": None}):
            detector = SceneDetector()
            results = detector.detect_scenes_batch([video1, video2])

        assert len(results) == 2


class TestSceneDetectorExtractThumbnails:
    """Tests for SceneDetector.extract_scene_thumbnails (fully mocked cv2)."""

    def test_thumbnails_extracted_for_each_scene(self, tmp_path: Path) -> None:
        from services.video.scene_detector import (
            DetectionMethod,
            Scene,
            SceneDetectionResult,
            SceneDetector,
        )

        video_file = tmp_path / "v.mp4"
        video_file.touch()
        out_dir = tmp_path / "thumbs"

        scene = Scene(
            scene_number=1,
            start_time=0.0,
            end_time=5.0,
            start_frame=0,
            end_frame=125,
            duration=5.0,
            score=1.0,
            frame_count=125,
        )
        result = SceneDetectionResult(
            video_path=video_file,
            scenes=[scene],
            total_duration=5.0,
            fps=25.0,
            total_frames=125,
            method=DetectionMethod.CONTENT,
        )

        cap = MagicMock()
        cap.get.return_value = 25.0
        cap.read.return_value = (True, MagicMock())

        mock_cv2 = MagicMock()
        mock_cv2.VideoCapture.return_value = cap

        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            SceneDetector.extract_scene_thumbnails(video_file, result, out_dir)

        mock_cv2.imwrite.assert_called_once()
        args = mock_cv2.imwrite.call_args[0]
        assert "scene_001.jpg" in args[0]

    def test_no_thumbnails_for_empty_scene_list(self, tmp_path: Path) -> None:
        from services.video.scene_detector import (
            DetectionMethod,
            SceneDetectionResult,
            SceneDetector,
        )

        video_file = tmp_path / "v.mp4"
        video_file.touch()

        result = SceneDetectionResult(
            video_path=video_file,
            scenes=[],
            total_duration=0.0,
            fps=25.0,
            total_frames=0,
            method=DetectionMethod.CONTENT,
        )

        cap = MagicMock()
        cap.get.return_value = 25.0
        mock_cv2 = MagicMock()
        mock_cv2.VideoCapture.return_value = cap

        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            SceneDetector.extract_scene_thumbnails(video_file, result, tmp_path / "thumbs")

        mock_cv2.imwrite.assert_not_called()


# ---------------------------------------------------------------------------
# AudioMetadataExtractor tests
# ---------------------------------------------------------------------------


class TestAudioMetadataExtractorInit:
    """Tests for AudioMetadataExtractor initialisation."""

    def test_default_uses_fallback(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        extractor = AudioMetadataExtractor()
        assert extractor.use_fallback is True

    def test_disable_fallback(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        extractor = AudioMetadataExtractor(use_fallback=False)
        assert extractor.use_fallback is False


class TestAudioMetadataExtractorRaisesForMissingFile:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        extractor = AudioMetadataExtractor()
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            extractor.extract(tmp_path / "ghost.mp3")


class TestAudioMetadataExtractorWithMutagen:
    """Tests for _extract_with_mutagen using mocked mutagen."""

    def _mock_mutagen_file(
        self,
        suffix: str = "MP3",
        duration: float = 180.0,
        bitrate: int = 128000,
        sample_rate: int = 44100,
        channels: int = 2,
        tags: dict[str, Any] | None = None,
    ) -> MagicMock:
        audio = MagicMock()
        audio.info.length = duration
        audio.info.bitrate = bitrate
        audio.info.sample_rate = sample_rate
        audio.info.channels = channels
        del audio.info.codec  # ensure hasattr returns False
        audio.info.codec_name = "mp3"
        if tags is not None:
            audio.tags = MagicMock()
            audio.tags.items.return_value = tags.items()
            audio.tags.keys.return_value = list(tags.keys())
            # For artwork checks
            audio.tags.pictures = []
        else:
            audio.tags = None
        return audio

    def test_extract_returns_audio_metadata(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import (
            AudioMetadata,
            AudioMetadataExtractor,
        )

        wav = _write_wav(tmp_path / "track.wav")
        mock_audio = self._mock_mutagen_file()

        mock_mutagen = MagicMock()
        mock_mutagen.File.return_value = mock_audio

        with patch.dict("sys.modules", {"mutagen": mock_mutagen}):
            with patch("mutagen.File", mock_mutagen.File):
                extractor = AudioMetadataExtractor()
                metadata = extractor._extract_with_mutagen(wav)

        assert isinstance(metadata, AudioMetadata)
        assert metadata.duration == pytest.approx(180.0)
        assert metadata.channels == 2

    def test_extract_with_id3_title_tag(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import (
            AudioMetadataExtractor,
        )

        wav = _write_wav(tmp_path / "track.wav")
        tags = {"TIT2": "My Song", "TPE1": "My Artist", "TALB": "My Album"}
        mock_audio = self._mock_mutagen_file(tags=tags)

        mock_mutagen = MagicMock()
        mock_mutagen.File.return_value = mock_audio

        with patch.dict("sys.modules", {"mutagen": mock_mutagen}):
            with patch("mutagen.File", mock_mutagen.File):
                extractor = AudioMetadataExtractor()
                metadata = extractor._extract_with_mutagen(wav)

        assert metadata.title == "My Song"
        assert metadata.artist == "My Artist"
        assert metadata.album == "My Album"

    def test_extract_raises_when_mutagen_returns_none(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav = _write_wav(tmp_path / "track.wav")
        mock_mutagen = MagicMock()
        mock_mutagen.File.return_value = None

        with patch.dict("sys.modules", {"mutagen": mock_mutagen}):
            with patch("mutagen.File", mock_mutagen.File):
                extractor = AudioMetadataExtractor()
                with pytest.raises(ValueError, match="Unsupported audio format"):
                    extractor._extract_with_mutagen(wav)

    def test_extract_raises_import_error_when_mutagen_missing(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav = _write_wav(tmp_path / "track.wav")
        with patch.dict("sys.modules", {"mutagen": None}):
            extractor = AudioMetadataExtractor()
            with pytest.raises(ImportError, match="mutagen"):
                extractor._extract_with_mutagen(wav)

    def test_extract_falls_back_to_tinytag_on_mutagen_failure(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import (
            AudioMetadata,
            AudioMetadataExtractor,
        )

        wav = _write_wav(tmp_path / "track.wav")

        mock_tag = MagicMock()
        mock_tag.duration = 60.0
        mock_tag.bitrate = 128
        mock_tag.samplerate = 16000
        mock_tag.channels = 1
        mock_tag.title = "Fallback Title"
        mock_tag.artist = "Fallback Artist"
        mock_tag.album = "Fallback Album"
        mock_tag.albumartist = None
        mock_tag.genre = None
        mock_tag.year = "2024"
        mock_tag.track = "3"
        mock_tag.disc = None
        mock_tag.comment = None

        mock_tinytag = MagicMock()
        mock_tinytag.TinyTag.get.return_value = mock_tag

        mock_mutagen = MagicMock()
        mock_mutagen.File.side_effect = Exception("mutagen failed")

        with patch.dict("sys.modules", {"mutagen": mock_mutagen, "tinytag": mock_tinytag}):
            with patch("mutagen.File", mock_mutagen.File):
                with patch("tinytag.TinyTag", mock_tinytag.TinyTag):
                    extractor = AudioMetadataExtractor(use_fallback=True)
                    metadata = extractor.extract(wav)

        assert isinstance(metadata, AudioMetadata)

    def test_extract_raises_when_fallback_disabled(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav = _write_wav(tmp_path / "track.wav")
        mock_mutagen = MagicMock()
        mock_mutagen.File.side_effect = RuntimeError("mutagen broken")

        with patch.dict("sys.modules", {"mutagen": mock_mutagen}):
            with patch("mutagen.File", mock_mutagen.File):
                extractor = AudioMetadataExtractor(use_fallback=False)
                with pytest.raises(RuntimeError, match="mutagen broken"):
                    extractor.extract(wav)


class TestAudioMetadataExtractorTagParsing:
    """Tests for tag edge-case handling in _extract_tags_mutagen."""

    def test_year_parsed_from_yyyy_mm_dd(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav = _write_wav(tmp_path / "track.wav")
        tags = {"TDRC": "2023-05-15"}

        mock_audio = MagicMock()
        mock_audio.info.length = 60.0
        mock_audio.info.bitrate = 128000
        mock_audio.info.sample_rate = 44100
        mock_audio.info.channels = 2
        mock_audio.tags = MagicMock()
        mock_audio.tags.items.return_value = tags.items()
        mock_audio.tags.keys.return_value = list(tags.keys())
        mock_audio.tags.pictures = []
        del mock_audio.info.codec

        mock_mutagen = MagicMock()
        mock_mutagen.File.return_value = mock_audio

        with patch.dict("sys.modules", {"mutagen": mock_mutagen}):
            with patch("mutagen.File", mock_mutagen.File):
                extractor = AudioMetadataExtractor()
                metadata = extractor._extract_with_mutagen(wav)

        assert metadata.year == 2023

    def test_track_number_parsed_from_slash_format(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav = _write_wav(tmp_path / "track.wav")
        tags = {"TRCK": "3/12"}

        mock_audio = MagicMock()
        mock_audio.info.length = 60.0
        mock_audio.info.bitrate = 128000
        mock_audio.info.sample_rate = 44100
        mock_audio.info.channels = 2
        mock_audio.tags = MagicMock()
        mock_audio.tags.items.return_value = tags.items()
        mock_audio.tags.keys.return_value = list(tags.keys())
        mock_audio.tags.pictures = []
        del mock_audio.info.codec

        mock_mutagen = MagicMock()
        mock_mutagen.File.return_value = mock_audio

        with patch.dict("sys.modules", {"mutagen": mock_mutagen}):
            with patch("mutagen.File", mock_mutagen.File):
                extractor = AudioMetadataExtractor()
                metadata = extractor._extract_with_mutagen(wav)

        assert metadata.track_number == 3

    def test_extra_tags_stored_in_extra_tags_dict(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav = _write_wav(tmp_path / "track.wav")
        tags = {"CUSTOM_TAG": "custom_value"}

        mock_audio = MagicMock()
        mock_audio.info.length = 60.0
        mock_audio.info.bitrate = 128000
        mock_audio.info.sample_rate = 44100
        mock_audio.info.channels = 2
        mock_audio.tags = MagicMock()
        mock_audio.tags.items.return_value = tags.items()
        mock_audio.tags.keys.return_value = list(tags.keys())
        mock_audio.tags.pictures = []
        del mock_audio.info.codec

        mock_mutagen = MagicMock()
        mock_mutagen.File.return_value = mock_audio

        with patch.dict("sys.modules", {"mutagen": mock_mutagen}):
            with patch("mutagen.File", mock_mutagen.File):
                extractor = AudioMetadataExtractor()
                metadata = extractor._extract_with_mutagen(wav)

        assert "CUSTOM_TAG" in metadata.extra_tags


class TestAudioMetadataExtractorTinytag:
    """Tests for _extract_with_tinytag fallback."""

    def test_raises_import_error_when_tinytag_missing(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav = _write_wav(tmp_path / "track.wav")
        with patch.dict("sys.modules", {"tinytag": None}):
            extractor = AudioMetadataExtractor()
            with pytest.raises(ImportError, match="tinytag"):
                extractor._extract_with_tinytag(wav)

    def test_tinytag_parses_track_slash_format(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav = _write_wav(tmp_path / "track.wav")

        mock_tag = MagicMock()
        mock_tag.duration = 120.0
        mock_tag.bitrate = 256
        mock_tag.samplerate = 44100
        mock_tag.channels = 2
        mock_tag.title = "Title"
        mock_tag.artist = "Artist"
        mock_tag.album = "Album"
        mock_tag.albumartist = "Album Artist"
        mock_tag.genre = "Rock"
        mock_tag.year = "2022"
        mock_tag.track = "5/10"
        mock_tag.disc = "1/2"
        mock_tag.comment = "Nice song"

        mock_tinytag = MagicMock()
        mock_tinytag.TinyTag.get.return_value = mock_tag

        with patch.dict("sys.modules", {"tinytag": mock_tinytag}):
            with patch("tinytag.TinyTag", mock_tinytag.TinyTag):
                extractor = AudioMetadataExtractor()
                metadata = extractor._extract_with_tinytag(wav)

        assert metadata.track_number == 5
        assert metadata.disc_number == 1
        assert metadata.year == 2022

    def test_tinytag_handles_null_year(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav = _write_wav(tmp_path / "track.wav")

        mock_tag = MagicMock()
        mock_tag.duration = 60.0
        mock_tag.bitrate = 128
        mock_tag.samplerate = 16000
        mock_tag.channels = 1
        mock_tag.title = None
        mock_tag.artist = None
        mock_tag.album = None
        mock_tag.albumartist = None
        mock_tag.genre = None
        mock_tag.year = None
        mock_tag.track = None
        mock_tag.disc = None
        mock_tag.comment = None

        mock_tinytag = MagicMock()
        mock_tinytag.TinyTag.get.return_value = mock_tag

        with patch.dict("sys.modules", {"tinytag": mock_tinytag}):
            with patch("tinytag.TinyTag", mock_tinytag.TinyTag):
                extractor = AudioMetadataExtractor()
                metadata = extractor._extract_with_tinytag(wav)

        assert metadata.year is None
        assert metadata.track_number is None


class TestAudioMetadataExtractorBatch:
    """Tests for AudioMetadataExtractor.extract_batch."""

    def test_batch_skips_failed_files(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav1 = _write_wav(tmp_path / "a.wav")
        missing = tmp_path / "missing.wav"  # does not exist

        extractor = AudioMetadataExtractor()

        with patch.object(extractor, "extract") as mock_extract:
            mock_extract.side_effect = [
                _make_audio_metadata(wav1),
                FileNotFoundError("not found"),
            ]
            results = extractor.extract_batch([wav1, missing])

        assert len(results) == 1
        assert results[0].file_path == wav1

    def test_batch_returns_all_results_when_successful(self, tmp_path: Path) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        wav1 = _write_wav(tmp_path / "a.wav")
        wav2 = _write_wav(tmp_path / "b.wav")

        extractor = AudioMetadataExtractor()

        with patch.object(extractor, "extract") as mock_extract:
            mock_extract.side_effect = [
                _make_audio_metadata(wav1),
                _make_audio_metadata(wav2),
            ]
            results = extractor.extract_batch([wav1, wav2])

        assert len(results) == 2


class TestAudioMetadataExtractorFormatHelpers:
    """Tests for static formatting helpers."""

    def test_format_duration_seconds_only(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        assert AudioMetadataExtractor.format_duration(90.0) == "01:30"

    def test_format_duration_with_hours(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        assert AudioMetadataExtractor.format_duration(3661.0) == "01:01:01"

    def test_format_bitrate_kbps(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        assert AudioMetadataExtractor.format_bitrate(128000) == "128 kbps"

    def test_format_bitrate_mbps(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        assert AudioMetadataExtractor.format_bitrate(2_000_000) == "2.0 Mbps"

    def test_format_bitrate_bps(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        assert AudioMetadataExtractor.format_bitrate(500) == "500 bps"


class TestCheckArtworkMutagen:
    """Tests for _check_artwork_mutagen."""

    def test_no_artwork_when_tags_none(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        extractor = AudioMetadataExtractor()
        audio = MagicMock()
        audio.tags = None
        has_art, count = extractor._check_artwork_mutagen(audio)
        assert has_art is False
        assert count == 0

    def test_flac_pictures_counted(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        extractor = AudioMetadataExtractor()
        audio = MagicMock()
        audio.tags = MagicMock()
        audio.tags.pictures = [MagicMock(), MagicMock()]
        audio.tags.keys.return_value = []
        has_art, count = extractor._check_artwork_mutagen(audio)
        assert has_art is True
        assert count == 2

    def test_mp3_apic_frames_counted(self) -> None:
        from services.audio.metadata_extractor import AudioMetadataExtractor

        extractor = AudioMetadataExtractor()
        audio = MagicMock()
        audio.tags = MagicMock()
        del audio.tags.pictures  # not FLAC
        del audio.pictures  # not OGG
        audio.tags.keys.return_value = ["APIC:Cover Art", "APIC:"]
        has_art, count = extractor._check_artwork_mutagen(audio)
        assert has_art is True
        assert count == 2


# ---------------------------------------------------------------------------
# AudioTranscriber tests
# ---------------------------------------------------------------------------


class TestAudioTranscriberInit:
    """Tests for AudioTranscriber initialisation."""

    def test_default_params_stored(self) -> None:
        from services.audio.transcriber import (
            AudioTranscriber,
            ComputeType,
            ModelSize,
        )

        transcriber = AudioTranscriber()
        assert transcriber.model_size == ModelSize.BASE
        assert transcriber.compute_type == ComputeType.FLOAT16
        assert transcriber.num_workers == 1
        assert transcriber._model is None

    def test_custom_model_size_stored(self) -> None:
        from services.audio.transcriber import AudioTranscriber, ModelSize

        transcriber = AudioTranscriber(model_size=ModelSize.SMALL)
        assert transcriber.model_size == ModelSize.SMALL

    def test_device_auto_resolves_to_cpu_when_torch_missing(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        with patch.dict("sys.modules", {"torch": None}):
            transcriber = AudioTranscriber(device="auto")
        assert transcriber.device == "cpu"

    def test_explicit_device_stored_as_is(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        transcriber = AudioTranscriber(device="cpu")
        assert transcriber.device == "cpu"


class TestAudioTranscriberDetectDevice:
    """Tests for _detect_device."""

    def test_non_auto_device_returned_unchanged(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        transcriber = AudioTranscriber(device="cpu")
        assert transcriber._detect_device("cpu") == "cpu"

    def test_auto_returns_cuda_when_available(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True

        with patch.dict("sys.modules", {"torch": mock_torch}):
            transcriber = AudioTranscriber(device="cpu")
            result = transcriber._detect_device("auto")

        assert result == "cuda"

    def test_auto_returns_mps_when_cuda_unavailable(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = True

        with patch.dict("sys.modules", {"torch": mock_torch}):
            transcriber = AudioTranscriber(device="cpu")
            result = transcriber._detect_device("auto")

        assert result == "mps"

    def test_auto_returns_cpu_when_neither_available(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = False

        with patch.dict("sys.modules", {"torch": mock_torch}):
            transcriber = AudioTranscriber(device="cpu")
            result = transcriber._detect_device("auto")

        assert result == "cpu"


class TestAudioTranscriberLoadModel:
    """Tests for AudioTranscriber._load_model."""

    def test_returns_cached_model_on_second_call(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        transcriber = AudioTranscriber(device="cpu")
        mock_model = MagicMock()
        transcriber._model = mock_model

        result = transcriber._load_model()
        assert result is mock_model

    def test_loads_model_when_faster_whisper_available(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        transcriber = AudioTranscriber(device="cpu")
        mock_model_instance = MagicMock()
        mock_whisper = MagicMock(return_value=mock_model_instance)

        with patch("services.audio.transcriber._FASTER_WHISPER_AVAILABLE", True):
            with patch("services.audio.transcriber.WhisperModel", mock_whisper):
                result = transcriber._load_model()

        assert result is mock_model_instance
        assert transcriber._model is mock_model_instance


class TestAudioTranscriberTranscribe:
    """Tests for AudioTranscriber.transcribe."""

    def test_raises_file_not_found_for_missing_audio(self, tmp_path: Path) -> None:
        from services.audio.transcriber import AudioTranscriber

        transcriber = AudioTranscriber(device="cpu")
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            transcriber.transcribe(tmp_path / "missing.wav")

    def test_transcribes_audio_with_mocked_model(self, tmp_path: Path) -> None:
        from services.audio.transcriber import (
            AudioTranscriber,
            TranscriptionResult,
        )

        wav = _write_wav(tmp_path / "audio.wav")

        # Build mock segment
        mock_seg = MagicMock()
        mock_seg.id = 0
        mock_seg.start = 0.0
        mock_seg.end = 3.0
        mock_seg.text = " Hello world"
        mock_seg.avg_logprob = -0.3
        mock_seg.no_speech_prob = 0.01
        del mock_seg.words  # no word timestamps

        mock_info = MagicMock()
        mock_info.language = "en"
        mock_info.language_probability = 0.99
        mock_info.duration = 3.0

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_seg], mock_info)

        transcriber = AudioTranscriber(device="cpu")
        transcriber._model = mock_model

        with patch("services.audio.transcriber._FASTER_WHISPER_AVAILABLE", True):
            result = transcriber.transcribe(wav)

        assert isinstance(result, TranscriptionResult)
        assert result.text == "Hello world"
        assert result.language == "en"
        assert len(result.segments) == 1
        assert result.segments[0].start == pytest.approx(0.0)
        assert result.segments[0].end == pytest.approx(3.0)

    def test_transcribes_with_language_option(self, tmp_path: Path) -> None:
        from services.audio.transcriber import (
            AudioTranscriber,
            TranscriptionOptions,
        )

        wav = _write_wav(tmp_path / "audio.wav")

        mock_seg = MagicMock()
        mock_seg.id = 0
        mock_seg.start = 0.0
        mock_seg.end = 2.0
        mock_seg.text = " Bonjour"
        mock_seg.avg_logprob = -0.2
        mock_seg.no_speech_prob = 0.0
        del mock_seg.words

        mock_info = MagicMock()
        mock_info.language = "fr"
        mock_info.language_probability = 0.95
        mock_info.duration = 2.0

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_seg], mock_info)

        transcriber = AudioTranscriber(device="cpu")
        transcriber._model = mock_model

        opts = TranscriptionOptions(language="fr")
        with patch("services.audio.transcriber._FASTER_WHISPER_AVAILABLE", True):
            result = transcriber.transcribe(wav, options=opts)

        assert result.language == "fr"
        # Verify language was passed to model.transcribe
        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs.get("language") == "fr"

    def test_transcribes_with_word_timestamps(self, tmp_path: Path) -> None:
        from services.audio.transcriber import (
            AudioTranscriber,
            TranscriptionOptions,
        )

        wav = _write_wav(tmp_path / "audio.wav")

        mock_word = MagicMock()
        mock_word.word = "hello"
        mock_word.start = 0.1
        mock_word.end = 0.5
        mock_word.probability = 0.98

        mock_seg = MagicMock()
        mock_seg.id = 0
        mock_seg.start = 0.0
        mock_seg.end = 1.0
        mock_seg.text = " hello"
        mock_seg.avg_logprob = -0.1
        mock_seg.no_speech_prob = 0.0
        mock_seg.words = [mock_word]

        mock_info = MagicMock()
        mock_info.language = "en"
        mock_info.language_probability = 0.99
        mock_info.duration = 1.0

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_seg], mock_info)

        transcriber = AudioTranscriber(device="cpu")
        transcriber._model = mock_model

        opts = TranscriptionOptions(word_timestamps=True)
        with patch("services.audio.transcriber._FASTER_WHISPER_AVAILABLE", True):
            result = transcriber.transcribe(wav, options=opts)

        assert len(result.segments[0].words) == 1
        assert result.segments[0].words[0].word == "hello"

    def test_transcribes_with_initial_prompt(self, tmp_path: Path) -> None:
        from services.audio.transcriber import (
            AudioTranscriber,
            TranscriptionOptions,
        )

        wav = _write_wav(tmp_path / "audio.wav")

        mock_seg = MagicMock()
        mock_seg.id = 0
        mock_seg.start = 0.0
        mock_seg.end = 1.0
        mock_seg.text = " Transcript"
        mock_seg.avg_logprob = -0.1
        mock_seg.no_speech_prob = 0.0
        del mock_seg.words

        mock_info = MagicMock()
        mock_info.language = "en"
        mock_info.language_probability = 0.99
        mock_info.duration = 1.0

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_seg], mock_info)

        transcriber = AudioTranscriber(device="cpu")
        transcriber._model = mock_model

        opts = TranscriptionOptions(initial_prompt="Meeting transcript:")
        with patch("services.audio.transcriber._FASTER_WHISPER_AVAILABLE", True):
            transcriber.transcribe(wav, options=opts)

        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs.get("initial_prompt") == "Meeting transcript:"

    def test_transcribe_with_vad_parameters(self, tmp_path: Path) -> None:
        from services.audio.transcriber import (
            AudioTranscriber,
            TranscriptionOptions,
        )

        wav = _write_wav(tmp_path / "audio.wav")

        mock_seg = MagicMock()
        mock_seg.id = 0
        mock_seg.start = 0.0
        mock_seg.end = 1.0
        mock_seg.text = " Hello"
        mock_seg.avg_logprob = -0.1
        mock_seg.no_speech_prob = 0.0
        del mock_seg.words

        mock_info = MagicMock()
        mock_info.language = "en"
        mock_info.language_probability = 0.99
        mock_info.duration = 1.0

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_seg], mock_info)

        transcriber = AudioTranscriber(device="cpu")
        transcriber._model = mock_model

        opts = TranscriptionOptions(vad_filter=True, vad_parameters={"threshold": 0.5})
        with patch("services.audio.transcriber._FASTER_WHISPER_AVAILABLE", True):
            transcriber.transcribe(wav, options=opts)

        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs.get("vad_filter") is True
        assert call_kwargs.get("vad_parameters") == {"threshold": 0.5}


class TestAudioTranscriberBatch:
    """Tests for AudioTranscriber.transcribe_batch."""

    def test_batch_skips_failed_files(self, tmp_path: Path) -> None:
        from services.audio.transcriber import (
            AudioTranscriber,
            TranscriptionResult,
        )

        wav1 = _write_wav(tmp_path / "a.wav")
        missing = tmp_path / "missing.wav"

        transcriber = AudioTranscriber(device="cpu")

        mock_result = MagicMock(spec=TranscriptionResult)
        with patch.object(transcriber, "transcribe") as mock_transcribe:
            mock_transcribe.side_effect = [
                mock_result,
                FileNotFoundError("not found"),
            ]
            results = transcriber.transcribe_batch([wav1, missing])

        assert len(results) == 1

    def test_batch_returns_all_results_on_success(self, tmp_path: Path) -> None:
        from services.audio.transcriber import (
            AudioTranscriber,
            TranscriptionResult,
        )

        wav1 = _write_wav(tmp_path / "a.wav")
        wav2 = _write_wav(tmp_path / "b.wav")

        transcriber = AudioTranscriber(device="cpu")

        mock_result1 = MagicMock(spec=TranscriptionResult)
        mock_result2 = MagicMock(spec=TranscriptionResult)
        with patch.object(transcriber, "transcribe") as mock_transcribe:
            mock_transcribe.side_effect = [mock_result1, mock_result2]
            results = transcriber.transcribe_batch([wav1, wav2])

        assert len(results) == 2


class TestAudioTranscriberUnloadModel:
    """Tests for AudioTranscriber.unload_model."""

    def test_unload_sets_model_to_none(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        transcriber = AudioTranscriber(device="cpu")
        transcriber._model = MagicMock()
        transcriber.unload_model()
        assert transcriber._model is None

    def test_unload_when_no_model_loaded_is_noop(self) -> None:
        from services.audio.transcriber import AudioTranscriber

        transcriber = AudioTranscriber(device="cpu")
        transcriber.unload_model()  # should not raise
        assert transcriber._model is None


class TestTranscriptionDataclasses:
    """Tests for transcriber dataclasses."""

    def test_transcription_options_defaults(self) -> None:
        from services.audio.transcriber import TranscriptionOptions

        opts = TranscriptionOptions()
        assert opts.language is None
        assert opts.word_timestamps is False
        assert opts.beam_size == 5
        assert opts.vad_filter is True

    def test_word_timing_fields(self) -> None:
        from services.audio.transcriber import WordTiming

        wt = WordTiming(word="hello", start=0.1, end=0.5, probability=0.98)
        assert wt.word == "hello"
        assert wt.start == pytest.approx(0.1)
        assert wt.probability == pytest.approx(0.98)

    def test_segment_fields(self) -> None:
        from services.audio.transcriber import Segment

        seg = Segment(id=1, start=0.0, end=5.0, text="hello world")
        assert seg.id == 1
        assert seg.text == "hello world"
        assert seg.words == []

    def test_model_size_enum_values(self) -> None:
        from services.audio.transcriber import ModelSize

        assert ModelSize.TINY.value == "tiny"
        assert ModelSize.LARGE_V3.value == "large-v3"

    def test_compute_type_enum_values(self) -> None:
        from services.audio.transcriber import ComputeType

        assert ComputeType.INT8.value == "int8"
        assert ComputeType.FLOAT32.value == "float32"
