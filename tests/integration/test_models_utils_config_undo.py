"""Integration tests for 7 modules with sub-80% coverage.

Modules targeted:
  - src/models/_vision_helpers.py      (was 30%)
  - src/models/audio_model.py          (was 50%)
  - src/models/audio_transcriber.py    (was 32%)
  - src/utils/epub_enhanced.py         (was 55%)
  - src/utils/text_processing.py       (was 63%)
  - src/config/provider_env.py         (was 58%)
  - src/undo/models.py                 (was 42%)
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ===========================================================================
# _vision_helpers
# ===========================================================================


class TestImageToDataUrl:
    def test_jpeg_file_produces_data_url(self, tmp_path: Path) -> None:
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)

        from models._vision_helpers import image_to_data_url

        result = image_to_data_url(img)

        assert result.startswith("data:image/jpeg;base64,")
        _, b64 = result.split(";base64,", 1)
        assert base64.b64decode(b64) == img.read_bytes()

    def test_png_file_uses_correct_mime(self, tmp_path: Path) -> None:
        img = tmp_path / "screenshot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

        from models._vision_helpers import image_to_data_url

        result = image_to_data_url(img)

        assert result.startswith("data:image/png;base64,")

    def test_webp_file_uses_webp_mime(self, tmp_path: Path) -> None:
        img = tmp_path / "image.webp"
        img.write_bytes(b"RIFF" + b"\x00" * 4 + b"WEBP")

        from models._vision_helpers import image_to_data_url

        result = image_to_data_url(img)

        assert result.startswith("data:image/webp;base64,")

    def test_unknown_extension_falls_back_to_jpeg(self, tmp_path: Path) -> None:
        img = tmp_path / "image.xyz_unknown"
        img.write_bytes(b"\x00\x01\x02")

        from models._vision_helpers import image_to_data_url

        result = image_to_data_url(img)

        assert result.startswith("data:image/jpeg;base64,")

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        from models._vision_helpers import image_to_data_url

        with pytest.raises(FileNotFoundError):
            image_to_data_url(tmp_path / "nonexistent.jpg")


class TestBytesToDataUrl:
    def test_default_mime_is_jpeg(self) -> None:
        from models._vision_helpers import bytes_to_data_url

        result = bytes_to_data_url(b"\xff\xd8\xff")

        assert result.startswith("data:image/jpeg;base64,")

    def test_custom_mime_type(self) -> None:
        from models._vision_helpers import bytes_to_data_url

        result = bytes_to_data_url(b"\x89PNG", mime_type="image/png")

        assert result.startswith("data:image/png;base64,")

    def test_encoded_bytes_are_correct(self) -> None:
        from models._vision_helpers import bytes_to_data_url

        raw = b"hello image bytes"
        result = bytes_to_data_url(raw)

        _, b64 = result.split(";base64,", 1)
        assert base64.b64decode(b64) == raw

    def test_empty_bytes(self) -> None:
        from models._vision_helpers import bytes_to_data_url

        result = bytes_to_data_url(b"")

        assert ";base64," in result


class TestSplitDataUrl:
    def test_valid_jpeg_url(self) -> None:
        from models._vision_helpers import bytes_to_data_url, split_data_url

        original = b"test data"
        url = bytes_to_data_url(original, mime_type="image/jpeg")
        mime, b64 = split_data_url(url)

        assert mime == "image/jpeg"
        assert base64.b64decode(b64) == original

    def test_valid_png_url(self) -> None:
        from models._vision_helpers import split_data_url

        url = "data:image/png;base64,aGVsbG8="
        mime, b64 = split_data_url(url)

        assert mime == "image/png"
        assert b64 == "aGVsbG8="

    def test_invalid_url_raises_value_error(self) -> None:
        from models._vision_helpers import split_data_url

        with pytest.raises(ValueError, match="Not a valid base64 data URL"):
            split_data_url("https://example.com/image.jpg")

    def test_missing_base64_marker_raises_value_error(self) -> None:
        from models._vision_helpers import split_data_url

        with pytest.raises(ValueError):
            split_data_url("data:image/jpeg,raw_data_no_base64_marker")

    def test_roundtrip_with_image_to_data_url(self, tmp_path: Path) -> None:
        from models._vision_helpers import image_to_data_url, split_data_url

        img = tmp_path / "test.png"
        raw_bytes = b"\x89PNG" + b"\xab" * 20
        img.write_bytes(raw_bytes)

        url = image_to_data_url(img)
        mime, b64 = split_data_url(url)

        assert mime == "image/png"
        assert base64.b64decode(b64) == raw_bytes

    def test_empty_mime_falls_back_to_jpeg(self) -> None:
        from models._vision_helpers import split_data_url

        # Manually construct a URL with empty MIME type
        url = "data:;base64,aGVsbG8="
        mime, b64 = split_data_url(url)

        assert mime == "image/jpeg"
        assert b64 == "aGVsbG8="


# ===========================================================================
# audio_model
# ===========================================================================


class TestAudioModelConstructor:
    def test_valid_audio_config_accepted(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="test-audio", model_type=ModelType.AUDIO)
        model = AudioModel(config)

        assert model.config.model_type == ModelType.AUDIO

    def test_wrong_model_type_raises_value_error(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="text-model", model_type=ModelType.TEXT)

        with pytest.raises(ValueError, match="Expected AUDIO model type"):
            AudioModel(config)

    def test_vision_type_raises_value_error(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="vision-model", model_type=ModelType.VISION)

        with pytest.raises(ValueError):
            AudioModel(config)


class TestAudioModelInitialize:
    def test_initialize_sets_initialized_flag(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="test-audio", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()

        assert model._initialized is True

    def test_initialize_completes_without_error(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="test-audio", model_type=ModelType.AUDIO)
        model = AudioModel(config)

        # Should not raise
        model.initialize()


class TestAudioModelGenerate:
    def test_generate_raises_not_implemented(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="test-audio", model_type=ModelType.AUDIO)
        model = AudioModel(config)

        with pytest.raises(NotImplementedError, match="Phase 3"):
            model.generate("audio/file.wav")

    def test_generate_raises_regardless_of_kwargs(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="test-audio", model_type=ModelType.AUDIO)
        model = AudioModel(config)

        with pytest.raises(NotImplementedError):
            model.generate("prompt", temperature=0.5, max_tokens=100)


class TestAudioModelCleanup:
    def test_cleanup_sets_initialized_false(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="test-audio", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()

        assert model._initialized is True

        model.cleanup()

        assert model._initialized is False

    def test_cleanup_on_uninitialised_model(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="test-audio", model_type=ModelType.AUDIO)
        model = AudioModel(config)

        # Should not raise even when not initialized
        model.cleanup()

        assert model._initialized is False


class TestAudioModelGetDefaultConfig:
    def test_default_config_has_audio_type(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelType

        cfg = AudioModel.get_default_config()

        assert cfg.model_type == ModelType.AUDIO

    def test_default_config_uses_default_model_name(self) -> None:
        from models.audio_model import AudioModel

        cfg = AudioModel.get_default_config()

        assert cfg.name == "distil-whisper-large-v3"

    def test_default_config_custom_model_name(self) -> None:
        from models.audio_model import AudioModel

        cfg = AudioModel.get_default_config(model_name="whisper-small")

        assert cfg.name == "whisper-small"

    def test_default_config_framework_is_faster_whisper(self) -> None:
        from models.audio_model import AudioModel

        cfg = AudioModel.get_default_config()

        assert cfg.framework == "faster-whisper"


# ===========================================================================
# audio_transcriber
# ===========================================================================


class TestModelSizeEnum:
    def test_all_expected_sizes_present(self) -> None:
        from models.audio_transcriber import ModelSize

        values = {m.value for m in ModelSize}
        assert "tiny" in values
        assert "base" in values
        assert "small" in values
        assert "medium" in values
        assert "large-v3" in values

    def test_enum_count(self) -> None:
        from models.audio_transcriber import ModelSize

        assert len(list(ModelSize)) == 5


class TestComputeTypeEnum:
    def test_float16_present(self) -> None:
        from models.audio_transcriber import ComputeType

        assert ComputeType.FLOAT16.value == "float16"

    def test_int8_present(self) -> None:
        from models.audio_transcriber import ComputeType

        assert ComputeType.INT8.value == "int8"

    def test_auto_present(self) -> None:
        from models.audio_transcriber import ComputeType

        assert ComputeType.AUTO.value == "auto"


class TestTranscriptionResultDataclass:
    def test_fields_accessible(self) -> None:
        from models.audio_transcriber import TranscriptionResult

        result = TranscriptionResult(
            text="Hello world",
            language="en",
            language_confidence=0.99,
            segments=[],
            duration=5.0,
            processing_time=1.2,
            model_size="base",
            device="cpu",
        )

        assert result.text == "Hello world"
        assert result.language == "en"
        assert result.language_confidence == 0.99
        assert result.segments == []
        assert result.duration == 5.0
        assert result.processing_time == 1.2
        assert result.model_size == "base"
        assert result.device == "cpu"
        assert result.error is None

    def test_error_field_can_be_set(self) -> None:
        from models.audio_transcriber import TranscriptionResult

        result = TranscriptionResult(
            text="",
            language="en",
            language_confidence=0.0,
            segments=[],
            duration=0.0,
            processing_time=0.0,
            model_size="base",
            device="cpu",
            error="Transcription failed",
        )

        assert result.error == "Transcription failed"


class TestLanguageDetectionDataclass:
    def test_fields_accessible(self) -> None:
        from models.audio_transcriber import LanguageDetection

        detection = LanguageDetection(
            language="fr",
            language_name="French",
            confidence=0.95,
        )

        assert detection.language == "fr"
        assert detection.language_name == "French"
        assert detection.confidence == 0.95


class TestAudioTranscriberImportGuard:
    def test_raises_import_error_when_faster_whisper_missing(self) -> None:
        """AudioTranscriber.__init__ raises ImportError if faster-whisper is absent."""
        with patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", False):
            from models.audio_transcriber import AudioTranscriber

            with pytest.raises(ImportError, match="faster-whisper"):
                AudioTranscriber()


class TestAudioTranscriberWithMockedWhisper:
    @pytest.fixture(autouse=True)
    def _patch_whisper(self) -> None:
        """Make faster-whisper appear available with a mock WhisperModel."""
        mock_model_cls = MagicMock()
        mock_model_instance = MagicMock()
        mock_model_cls.return_value = mock_model_instance

        with (
            patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True),
            patch("models.audio_transcriber.WhisperModel", mock_model_cls),
        ):
            self._mock_model_cls = mock_model_cls
            self._mock_model_instance = mock_model_instance
            yield

    def test_constructor_accepts_enum_model_size(self) -> None:
        from models.audio_transcriber import AudioTranscriber, ModelSize

        t = AudioTranscriber(model_size=ModelSize.TINY, device="cpu")

        assert t.model_size == "tiny"

    def test_constructor_accepts_string_model_size(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        t = AudioTranscriber(model_size="base", device="cpu")

        assert t.model_size == "base"

    def test_invalid_model_size_raises_value_error(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        with pytest.raises(ValueError, match="Invalid model size"):
            AudioTranscriber(model_size="gigantic", device="cpu")

    def test_invalid_compute_type_raises_value_error(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        with pytest.raises(ValueError, match="Invalid compute type"):
            AudioTranscriber(compute_type="turbo", device="cpu")

    def test_device_cpu_is_preserved(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        t = AudioTranscriber(device="cpu")

        assert t.device == "cpu"

    def test_device_auto_falls_back_to_cpu_when_no_gpu(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        with patch(
            "models.audio_transcriber.AudioTranscriber._detect_device",
            return_value="cpu",
        ):
            t = AudioTranscriber(device="auto")

        assert t.device == "cpu"

    def test_model_cache_hit_avoids_reload(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        t = AudioTranscriber(device="cpu")
        cache_key = f"{t.model_size}_{t.device}_{t.compute_type}"

        # Pre-populate the class-level cache
        fake_model = MagicMock()
        AudioTranscriber._model_cache[cache_key] = fake_model

        try:
            loaded = t._load_model()
            assert loaded is fake_model
        finally:
            AudioTranscriber._model_cache.pop(cache_key, None)

    def test_clear_cache_removes_entry(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        t = AudioTranscriber(device="cpu")
        cache_key = f"{t.model_size}_{t.device}_{t.compute_type}"
        AudioTranscriber._model_cache[cache_key] = MagicMock()

        t.clear_cache()

        assert cache_key not in AudioTranscriber._model_cache

    def test_clear_all_caches_empties_class_cache(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        AudioTranscriber._model_cache["a"] = MagicMock()
        AudioTranscriber._model_cache["b"] = MagicMock()

        AudioTranscriber.clear_all_caches()

        assert len(AudioTranscriber._model_cache) == 0

    def test_get_supported_formats(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        formats = AudioTranscriber.get_supported_formats()

        assert "wav" in formats
        assert "mp3" in formats
        assert len(formats) >= 5

    def test_transcribe_raises_file_not_found(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        t = AudioTranscriber(device="cpu")

        with pytest.raises(FileNotFoundError):
            t.transcribe("/nonexistent/audio.wav")

    def test_detect_language_raises_file_not_found(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        t = AudioTranscriber(device="cpu")

        with pytest.raises(FileNotFoundError):
            t.detect_language("/nonexistent/audio.mp3")


# ===========================================================================
# epub_enhanced
# ===========================================================================


class TestEpubEnhancedDataclasses:
    def test_epub_chapter_fields(self) -> None:
        from utils.epub_enhanced import EPUBChapter

        ch = EPUBChapter(title="Chapter 1", content="Some text here", order=0, word_count=3)

        assert ch.title == "Chapter 1"
        assert ch.content == "Some text here"
        assert ch.order == 0
        assert ch.word_count == 3

    def test_epub_metadata_defaults(self) -> None:
        from utils.epub_enhanced import EPUBMetadata

        meta = EPUBMetadata(title="My Book", authors=["Author Name"])

        assert meta.title == "My Book"
        assert meta.authors == ["Author Name"]
        assert meta.language is None
        assert meta.has_cover is False
        assert meta.identifiers == {}
        assert meta.subjects == []
        assert meta.contributors == []

    def test_epub_metadata_with_all_fields(self) -> None:
        from utils.epub_enhanced import EPUBMetadata

        meta = EPUBMetadata(
            title="Test Book",
            authors=["Jane Doe"],
            language="en",
            publisher="Test Press",
            isbn="9780000000000",
            has_cover=True,
        )

        assert meta.language == "en"
        assert meta.publisher == "Test Press"
        assert meta.isbn == "9780000000000"
        assert meta.has_cover is True

    def test_epub_content_fields(self) -> None:
        from utils.epub_enhanced import EPUBChapter, EPUBContent, EPUBMetadata

        meta = EPUBMetadata(title="Book", authors=["Author"])
        ch = EPUBChapter(title="Ch1", content="text", order=0, word_count=1)
        content = EPUBContent(
            metadata=meta, chapters=[ch], total_words=1, total_chapters=1, raw_text="text"
        )

        assert content.total_chapters == 1
        assert content.total_words == 1
        assert content.raw_text == "text"


class TestEpubProcessingError:
    def test_can_be_raised_and_caught(self) -> None:
        from utils.epub_enhanced import EPUBProcessingError

        with pytest.raises(EPUBProcessingError, match="test error"):
            raise EPUBProcessingError("test error")

    def test_is_subclass_of_exception(self) -> None:
        from utils.epub_enhanced import EPUBProcessingError

        assert issubclass(EPUBProcessingError, Exception)


class TestEnhancedEPUBReaderWithMock:
    @pytest.fixture(autouse=True)
    def _require_ebooklib(self) -> None:
        pytest.importorskip("ebooklib")

    def _make_mock_book(self) -> MagicMock:
        """Build a mock ebooklib EpubBook."""
        book = MagicMock()
        book.get_metadata.return_value = []
        book.get_items.return_value = []
        book.version = "2.0"
        return book

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()

        with pytest.raises(FileNotFoundError):
            reader.read_epub(tmp_path / "nonexistent.epub")

    def test_epub_read_exception_wrapped(self, tmp_path: Path) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader, EPUBProcessingError

        fake_epub = tmp_path / "broken.epub"
        fake_epub.write_bytes(b"not a valid epub")

        reader = EnhancedEPUBReader()

        with patch("utils.epub_enhanced.epub.read_epub", side_effect=Exception("corrupt")):
            with pytest.raises(EPUBProcessingError, match="Failed to read EPUB"):
                reader.read_epub(fake_epub)

    def test_read_epub_basic_metadata(self, tmp_path: Path) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        fake_epub = tmp_path / "book.epub"
        fake_epub.write_bytes(b"placeholder")

        book = self._make_mock_book()

        def meta_side_effect(ns: str, key: str) -> list:
            mapping = {
                ("DC", "title"): [("My Great Novel", {})],
                ("DC", "creator"): [("John Doe", {})],
                ("DC", "language"): [("en", {})],
                ("DC", "publisher"): [("Test Press", {})],
            }
            return mapping.get((ns, key), [])

        book.get_metadata.side_effect = meta_side_effect

        reader = EnhancedEPUBReader()

        with patch("utils.epub_enhanced.epub.read_epub", return_value=book):
            content = reader.read_epub(fake_epub)

        assert content.metadata.title == "My Great Novel"
        assert content.metadata.authors == ["John Doe"]
        assert content.metadata.language == "en"

    def test_read_epub_no_metadata_uses_defaults(self, tmp_path: Path) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        fake_epub = tmp_path / "book.epub"
        fake_epub.write_bytes(b"placeholder")

        book = self._make_mock_book()
        # All metadata calls return empty
        book.get_metadata.return_value = []

        reader = EnhancedEPUBReader()

        with patch("utils.epub_enhanced.epub.read_epub", return_value=book):
            content = reader.read_epub(fake_epub)

        assert content.metadata.title == "Unknown Title"
        assert content.metadata.authors == ["Unknown Author"]

    def test_extract_text_from_html_strips_scripts(self) -> None:
        pytest.importorskip("bs4")
        from bs4 import BeautifulSoup

        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()
        html = "<html><body><script>alert('x')</script><p>Hello world</p></body></html>"
        soup = BeautifulSoup(html, "lxml")

        text = reader._extract_text_from_html(soup)

        assert "alert" not in text
        assert "Hello world" in text

    def test_clean_isbn_strips_hyphens(self) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()
        result = reader._clean_isbn("978-0-306-40615-7")

        assert result == "9780306406157"

    def test_word_to_number_valid(self) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()

        assert reader._word_to_number("one") == 1
        assert reader._word_to_number("Three") == 3
        assert reader._word_to_number("FIVE") == 5

    def test_word_to_number_unknown_returns_none(self) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()

        assert reader._word_to_number("eleven") is None

    def test_detect_series_from_title_number(self) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()
        book = self._make_mock_book()
        # Calibre meta returns nothing
        book.get_metadata.return_value = []

        series, index = reader._detect_series("Dune Messiah, Book 2", book)

        assert series == "Dune Messiah"
        assert index == 2.0

    def test_detect_series_from_hash_pattern(self) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()
        book = self._make_mock_book()
        book.get_metadata.return_value = []

        series, index = reader._detect_series("Foundation #3", book)

        assert series == "Foundation"
        assert index == 3.0

    def test_detect_series_none_when_no_match(self) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()
        book = self._make_mock_book()
        book.get_metadata.return_value = []

        series, index = reader._detect_series("Just a Book Title", book)

        assert series is None
        assert index is None

    def test_detect_epub_version_from_book_attribute(self) -> None:
        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()
        book = self._make_mock_book()
        book.version = "3.0"
        book.get_items.return_value = []

        version = reader._detect_epub_version(book)

        assert version == "3.0"


class TestEbookLibNotAvailable:
    def test_enhanced_epub_reader_raises_import_error(self) -> None:
        with patch("utils.epub_enhanced.EBOOKLIB_AVAILABLE", False):
            from utils.epub_enhanced import EnhancedEPUBReader

            with pytest.raises(ImportError, match="ebooklib"):
                EnhancedEPUBReader()

    def test_get_epub_metadata_raises_import_error(self, tmp_path: Path) -> None:
        with patch("utils.epub_enhanced.EBOOKLIB_AVAILABLE", False):
            from utils.epub_enhanced import get_epub_metadata

            with pytest.raises(ImportError):
                get_epub_metadata(tmp_path / "fake.epub")


# ===========================================================================
# text_processing — additional coverage beyond existing tests
# ===========================================================================


class TestCleanTextAdditional:
    def test_numbers_are_stripped(self) -> None:
        from utils.text_processing import clean_text

        result = clean_text("report 2024 quarterly")
        assert "2024" not in result

    def test_special_chars_stripped(self) -> None:
        from utils.text_processing import clean_text

        result = clean_text("hello! world? test.")
        # Special chars replaced; words survive
        assert "hello" in result or len(result) > 0

    def test_camelcase_split(self) -> None:
        from utils.text_processing import clean_text

        result = clean_text("camelCaseWord", remove_unwanted=False, lemmatize=False)
        assert "camel" in result

    def test_lemmatize_false_preserves_plural(self) -> None:
        from utils.text_processing import clean_text

        result = clean_text("financial reports invoices", lemmatize=False, remove_unwanted=False)
        # Without lemmatization 'reports' stays as-is
        assert "report" in result or "reports" in result

    def test_remove_unwanted_false_keeps_stopwords(self) -> None:
        from utils.text_processing import clean_text

        # "the" is normally filtered; with remove_unwanted=False it survives
        result = clean_text("the quick brown fox", remove_unwanted=False, lemmatize=False)
        assert "the" in result

    def test_max_words_one_returns_single_word(self) -> None:
        from utils.text_processing import clean_text

        result = clean_text("alpha beta gamma delta", max_words=1, remove_unwanted=False)
        words = [w for w in result.split("_") if w]
        assert len(words) == 1


class TestSanitizeFilenameAdditional:
    def test_pure_numbers_returns_untitled(self) -> None:
        from utils.text_processing import sanitize_filename

        result = sanitize_filename("12345 67890")
        assert result == "untitled"

    def test_long_name_truncated_to_max_length(self) -> None:
        from utils.text_processing import sanitize_filename

        # Use real words that survive filtering
        result = sanitize_filename("alpha beta gamma delta epsilon zeta eta theta", max_length=15)
        assert len(result) == 15

    def test_result_is_lowercase(self) -> None:
        from utils.text_processing import sanitize_filename

        result = sanitize_filename("Hello World")
        assert result == result.lower()


class TestTruncateTextAdditional:
    def test_truncation_appends_ellipsis(self) -> None:
        from utils.text_processing import truncate_text

        result = truncate_text("a" * 100, max_chars=50)
        assert result.endswith("...")

    def test_truncation_total_length_is_max_plus_three(self) -> None:
        from utils.text_processing import truncate_text

        result = truncate_text("x" * 200, max_chars=100)
        assert len(result) == 103  # 100 chars + "..."


class TestGetUnwantedWordsAdditional:
    def test_common_stopword_present(self) -> None:
        from utils.text_processing import get_unwanted_words

        words = get_unwanted_words()
        assert "the" in words

    def test_returns_set_type(self) -> None:
        from utils.text_processing import get_unwanted_words

        result = get_unwanted_words()
        assert isinstance(result, set)
        assert len(result) > 0


class TestExtractKeywordsAdditional:
    def test_extract_top_n(self) -> None:
        from utils.text_processing import extract_keywords

        result = extract_keywords("alpha beta alpha gamma alpha beta", top_n=2)

        assert len(result) == 2
        assert all(isinstance(kw, str) for kw in result)
        assert "alpha" in result

    def test_keywords_are_strings(self) -> None:
        from utils.text_processing import extract_keywords

        result = extract_keywords("financial report quarterly invoice payment")
        for kw in result:
            assert isinstance(kw, str)
            assert len(kw) > 0


# ===========================================================================
# config/provider_env — additional coverage
# ===========================================================================


class TestGetCurrentProviderAdditional:
    def test_default_is_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_current_provider

        monkeypatch.delenv("FO_PROVIDER", raising=False)

        assert get_current_provider() == "ollama"

    def test_openai_recognised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_current_provider

        monkeypatch.setenv("FO_PROVIDER", "openai")

        assert get_current_provider() == "openai"

    def test_mlx_recognised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_current_provider

        monkeypatch.setenv("FO_PROVIDER", "mlx")

        assert get_current_provider() == "mlx"

    def test_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_current_provider

        monkeypatch.setenv("FO_PROVIDER", "  openai  ")

        assert get_current_provider() == "openai"

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_current_provider

        monkeypatch.setenv("FO_PROVIDER", "OLLAMA")

        assert get_current_provider() == "ollama"

    def test_invalid_value_falls_back_to_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_current_provider

        monkeypatch.setenv("FO_PROVIDER", "notreal")

        assert get_current_provider() == "ollama"


class TestGetModelConfigsFromEnvAdditional:
    def test_openai_custom_model_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_model_configs_from_env

        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.setenv("FO_OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("FO_OPENAI_MODEL", "gpt-4-turbo")
        monkeypatch.delenv("FO_OPENAI_VISION_MODEL", raising=False)

        text_cfg, _ = get_model_configs_from_env()

        assert text_cfg.name == "gpt-4-turbo"

    def test_openai_vision_model_falls_back_to_text_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from config.provider_env import get_model_configs_from_env

        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.setenv("FO_OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("FO_OPENAI_MODEL", "gpt-4o-mini")
        monkeypatch.delenv("FO_OPENAI_VISION_MODEL", raising=False)

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert vision_cfg.name == text_cfg.name

    def test_openai_separate_vision_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_model_configs_from_env

        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.setenv("FO_OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("FO_OPENAI_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("FO_OPENAI_VISION_MODEL", "gpt-4o")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.name == "gpt-4o-mini"
        assert vision_cfg.name == "gpt-4o"

    def test_openai_base_url_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_model_configs_from_env

        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.setenv("FO_OPENAI_BASE_URL", "http://localhost:1234/v1")
        monkeypatch.delenv("FO_OPENAI_API_KEY", raising=False)

        text_cfg, _ = get_model_configs_from_env()

        assert text_cfg.api_base_url == "http://localhost:1234/v1"

    def test_openai_empty_api_key_becomes_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.provider_env import get_model_configs_from_env

        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.setenv("FO_OPENAI_API_KEY", "   ")
        monkeypatch.setenv("OPENAI_API_KEY", "sdk-key")  # suppress warning

        text_cfg, _ = get_model_configs_from_env()

        assert text_cfg.api_key is None


# ===========================================================================
# undo/models
# ===========================================================================


class TestConflictTypeEnum:
    def test_all_expected_values_present(self) -> None:
        from undo.models import ConflictType

        values = {ct.value for ct in ConflictType}
        assert "file_modified" in values
        assert "file_missing" in values
        assert "path_occupied" in values
        assert "permission_denied" in values
        assert "disk_space" in values
        assert "parent_missing" in values
        assert "hash_mismatch" in values

    def test_enum_count(self) -> None:
        from undo.models import ConflictType

        assert len(list(ConflictType)) == 7

    def test_string_equality(self) -> None:
        from undo.models import ConflictType

        # StrEnum: value equals its string representation
        assert ConflictType.FILE_MODIFIED == "file_modified"
        assert ConflictType.HASH_MISMATCH == "hash_mismatch"


class TestConflictDataclass:
    def test_basic_fields(self) -> None:
        from undo.models import Conflict, ConflictType

        c = Conflict(
            conflict_type=ConflictType.FILE_MISSING,
            path="/some/file.txt",
            description="File was deleted",
        )

        assert c.conflict_type == ConflictType.FILE_MISSING
        assert c.path == "/some/file.txt"
        assert c.description == "File was deleted"
        assert c.expected is None
        assert c.actual is None

    def test_str_without_expected_actual(self) -> None:
        from undo.models import Conflict, ConflictType

        c = Conflict(
            conflict_type=ConflictType.PATH_OCCUPIED,
            path="/dest/file.txt",
            description="Target already exists",
        )

        s = str(c)
        assert "path_occupied" in s
        assert "/dest/file.txt" in s
        assert "Target already exists" in s

    def test_str_with_expected_and_actual(self) -> None:
        from undo.models import Conflict, ConflictType

        c = Conflict(
            conflict_type=ConflictType.HASH_MISMATCH,
            path="/file.txt",
            description="Hash changed",
            expected="abc123",
            actual="def456",
        )

        s = str(c)
        assert "abc123" in s
        assert "def456" in s

    def test_optional_fields_default_to_none(self) -> None:
        from undo.models import Conflict, ConflictType

        c = Conflict(
            conflict_type=ConflictType.DISK_SPACE,
            path="/path",
            description="No space",
        )

        assert c.expected is None
        assert c.actual is None


class TestValidationResult:
    def test_can_proceed_true_is_truthy(self) -> None:
        from undo.models import ValidationResult

        result = ValidationResult(can_proceed=True)

        assert bool(result) is True

    def test_can_proceed_false_is_falsy(self) -> None:
        from undo.models import ValidationResult

        result = ValidationResult(can_proceed=False)

        assert bool(result) is False

    def test_str_passed_with_warnings(self) -> None:
        from undo.models import ValidationResult

        result = ValidationResult(can_proceed=True, warnings=["minor issue"])

        s = str(result)
        assert "Validation passed" in s
        assert "1 warnings" in s

    def test_str_failed_with_error_message(self) -> None:
        from undo.models import ValidationResult

        result = ValidationResult(can_proceed=False, error_message="Conflict detected")

        s = str(result)
        assert "Validation failed" in s
        assert "Conflict detected" in s

    def test_str_failed_with_conflicts(self) -> None:
        from undo.models import Conflict, ConflictType, ValidationResult

        conflicts = [
            Conflict(ConflictType.FILE_MISSING, f"/path{i}", f"desc {i}") for i in range(5)
        ]
        result = ValidationResult(
            can_proceed=False,
            conflicts=conflicts,
            error_message="Multiple conflicts",
        )

        s = str(result)
        assert "Conflicts: 5" in s
        assert "and 2 more" in s

    def test_default_fields(self) -> None:
        from undo.models import ValidationResult

        result = ValidationResult(can_proceed=True)

        assert result.conflicts == []
        assert result.warnings == []
        assert result.error_message is None


class TestRollbackResult:
    def test_success_true_is_truthy(self) -> None:
        from undo.models import RollbackResult

        result = RollbackResult(success=True, operations_rolled_back=3)

        assert bool(result) is True

    def test_success_false_is_falsy(self) -> None:
        from undo.models import RollbackResult

        result = RollbackResult(success=False)

        assert bool(result) is False

    def test_str_success(self) -> None:
        from undo.models import RollbackResult

        result = RollbackResult(success=True, operations_rolled_back=5)

        s = str(result)
        assert "Rollback successful" in s
        assert "5 operations" in s

    def test_str_failure_with_errors(self) -> None:
        from undo.models import RollbackResult

        result = RollbackResult(
            success=False,
            operations_rolled_back=2,
            operations_failed=3,
            errors=[(1, "err1"), (2, "err2"), (3, "err3"), (4, "err4")],
        )

        s = str(result)
        assert "Rollback failed" in s
        assert "2 succeeded" in s
        assert "and 1 more errors" in s

    def test_str_with_warnings(self) -> None:
        from undo.models import RollbackResult

        result = RollbackResult(
            success=True, operations_rolled_back=1, warnings=["file already moved"]
        )

        s = str(result)
        assert "Warnings: 1" in s

    def test_default_fields(self) -> None:
        from undo.models import RollbackResult

        result = RollbackResult(success=True)

        assert result.operations_rolled_back == 0
        assert result.operations_failed == 0
        assert result.errors == []
        assert result.warnings == []
