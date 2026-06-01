"""Tests for VisionProcessor service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config.schema import ProcessingSettings
from models.base import ModelType
from services.vision_processor import ProcessedImage, VisionProcessor, compute_vision_timeout

pytestmark = [pytest.mark.unit, pytest.mark.ci]


@pytest.fixture
def mock_vision_model() -> MagicMock:
    """Mocked VisionModel instance."""
    model = MagicMock()
    model.is_initialized = True
    model.config.model_type = ModelType.VISION
    model.generate.return_value = "Mocked AI Response"
    return model


@pytest.fixture
def vision_processor(mock_vision_model: MagicMock) -> VisionProcessor:
    """VisionProcessor with a mocked vision model."""
    processor = VisionProcessor(vision_model=mock_vision_model)
    return processor


@pytest.mark.unit
class TestProcessedImage:
    """Tests for ProcessedImage dataclass."""

    def test_defaults(self) -> None:
        """Test ProcessedImage default values."""
        result = ProcessedImage(
            file_path=Path("/img.jpg"),
            description="desc",
            folder_name="folder",
            filename="name",
        )
        assert result.has_text is False
        assert result.extracted_text is None
        assert result.processing_time == 0.0
        assert result.error is None

    def test_custom_values(self) -> None:
        """Test ProcessedImage with all fields set."""
        result = ProcessedImage(
            file_path=Path("/img.jpg"),
            description="A photo",
            folder_name="nature",
            filename="sunset",
            has_text=True,
            extracted_text="Hello World",
            processing_time=1.5,
            error=None,
        )
        assert result.has_text is True
        assert result.extracted_text == "Hello World"
        assert result.processing_time == 1.5


@pytest.mark.unit
class TestVisionProcessorInit:
    """Tests for VisionProcessor initialization."""

    def test_init_with_provided_model(self, mock_vision_model: MagicMock) -> None:
        """Test init with pre-existing model sets _owns_model=False."""
        processor = VisionProcessor(vision_model=mock_vision_model)
        assert processor.vision_model is mock_vision_model
        assert processor._owns_model is False

    @patch("services.vision_processor.get_vision_model")
    @patch("services.vision_processor.VisionModel")
    def test_init_creates_own_model(
        self, mock_vm_cls: MagicMock, mock_get_vision_model: MagicMock
    ) -> None:
        """Test init without model creates one and sets _owns_model=True."""
        mock_vm_cls.get_default_config.return_value = MagicMock()
        mock_get_vision_model.return_value = MagicMock()

        processor = VisionProcessor()

        mock_get_vision_model.assert_called_once()
        assert processor._owns_model is True

    def test_initialize_delegates_to_model(self, mock_vision_model: MagicMock) -> None:
        """Test initialize calls model.initialize when not initialized."""
        mock_vision_model.is_initialized = False
        processor = VisionProcessor(vision_model=mock_vision_model)

        processor.initialize()

        mock_vision_model.initialize.assert_called_once()

    def test_initialize_skips_if_already_initialized(self, mock_vision_model: MagicMock) -> None:
        """Test initialize is a no-op when model is already initialized."""
        mock_vision_model.is_initialized = True
        processor = VisionProcessor(vision_model=mock_vision_model)

        processor.initialize()

        mock_vision_model.initialize.assert_not_called()


@pytest.mark.unit
class TestVisionProcessorProcessFile:
    """Tests for VisionProcessor.process_file()."""

    def test_process_file_success(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test full processing pipeline with all flags on."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic bytes

        # Sequential responses: description, OCR, folder, filename
        mock_vision_model.generate.side_effect = [
            "A beautiful sunset over mountains",
            "Welcome to Nature Park",  # OCR text (>10 chars)
            "nature_landscapes",
            "mountain_sunset_view",
        ]

        result = vision_processor.process_file(img)

        assert isinstance(result, ProcessedImage)
        assert result.file_path == img
        assert result.description == "A beautiful sunset over mountains"
        assert result.has_text is True
        assert result.extracted_text is not None
        assert result.error is None
        assert result.processing_time >= 0

    def test_process_file_not_found(self, vision_processor: VisionProcessor) -> None:
        """Test processing a nonexistent file returns error result."""
        result = vision_processor.process_file("/nonexistent/img.jpg")

        assert result.error == "File not found"
        assert result.folder_name == "errors"

    def test_process_file_not_found_does_not_record_inference_ms(
        self, vision_processor: VisionProcessor
    ) -> None:
        """Early-return paths skip the inference-ms sample (#410 / CodeRabbit P2)."""
        result = vision_processor.process_file("/nonexistent/img.jpg")
        # Non-inference path → inference_ms stays None so the run summary
        # doesn't get a 0ms sample that would skew p95/p99 low.
        assert result.error == "File not found"
        assert result.inference_ms is None

    def test_process_file_success_records_inference_ms(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Happy-path inferences populate inference_ms (#410)."""
        img = tmp_path / "ok.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0")
        mock_vision_model.generate.side_effect = [
            "A sunset",
            "",  # OCR empty
            "nature",
            "sunset",
        ]
        result = vision_processor.process_file(img)
        assert result.error is None
        assert isinstance(result.inference_ms, float)
        assert result.inference_ms < 5000.0

    def test_process_file_model_errors_graceful(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test model errors are handled gracefully by each internal method."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")

        mock_vision_model.generate.side_effect = RuntimeError("model crashed")

        result = vision_processor.process_file(img)

        # Each helper catches errors individually and returns fallbacks
        assert result.error is None  # outer try/except not triggered
        assert result.description == f"Image from {img.name}"  # _generate_description fallback
        assert result.folder_name == "images"  # _generate_folder_name fallback
        assert result.filename == img.stem  # _generate_filename fallback
        assert result.has_text is False  # _extract_text returned None

    def test_process_file_no_description(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test processing with generate_description=False."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")

        # Only OCR, folder, filename responses needed
        mock_vision_model.generate.side_effect = [
            "NO_TEXT",  # OCR
            "general",  # folder (context will be empty description)
            "test_file",  # filename
        ]

        result = vision_processor.process_file(img, generate_description=False)

        assert result.description == ""

    def test_process_file_no_ocr(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test processing with perform_ocr=False."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")

        mock_vision_model.generate.side_effect = [
            "Image description",  # description
            "nature",  # folder
            "sunset",  # filename
        ]

        result = vision_processor.process_file(img, perform_ocr=False)

        assert result.extracted_text is None
        assert result.has_text is False

    def test_process_file_all_flags_off(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test processing with all generation flags disabled."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")

        result = vision_processor.process_file(
            img,
            generate_description=False,
            generate_folder=False,
            generate_filename=False,
            perform_ocr=False,
        )

        mock_vision_model.generate.assert_not_called()
        assert result.description == ""
        assert result.folder_name == ""
        assert result.filename == ""
        assert result.error is None

    def test_process_file_ocr_no_text(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test OCR returning NO_TEXT sets has_text=False."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")

        mock_vision_model.generate.side_effect = [
            "Image description",  # description
            "NO_TEXT",  # OCR -> no text
            "general",  # folder (using description as context)
            "img_file",  # filename
        ]

        result = vision_processor.process_file(img)

        assert result.has_text is False
        assert result.extracted_text is None

    def test_process_file_truncates_long_ocr(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test extracted_text is truncated to 500 chars in result."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")

        long_text = "x" * 1000
        mock_vision_model.generate.side_effect = [
            "desc",
            long_text,  # OCR returns >500 chars
            "folder",
            "filename",
        ]

        result = vision_processor.process_file(img)

        assert result.has_text is True
        assert len(result.extracted_text) == 500

    def test_process_file_trips_circuit_on_fatal_backend_error(
        self, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Fatal backend errors should open circuit and stop repeated model calls."""
        img1 = tmp_path / "first.jpg"
        img2 = tmp_path / "second.jpg"
        img1.write_bytes(b"\xff\xd8")
        img2.write_bytes(b"\xff\xd8")

        call_count = 0

        def _fatal_generate(**_: object) -> str:
            nonlocal call_count
            call_count += 1
            raise RuntimeError(
                "model runner has unexpectedly stopped; "
                "health resp: dial tcp 127.0.0.1:6948 connect: connection refused "
                "(status code: 500)"
            )

        mock_vision_model.generate.side_effect = _fatal_generate
        processor = VisionProcessor(
            vision_model=mock_vision_model,
            backend_cooldown_seconds=120.0,
        )

        first = processor.process_file(img1)
        second = processor.process_file(img2)

        assert call_count == 1
        assert first.error is not None
        assert second.error is not None
        assert "Vision backend unavailable" in first.error
        assert "Vision backend unavailable" in second.error
        assert second.folder_name == "images"
        assert second.filename == "second"

    def test_process_file_nonfatal_500_does_not_trip_circuit(
        self, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Generic provider 500s alone should not trigger fatal backend circuit."""
        img = tmp_path / "sample.jpg"
        img.write_bytes(b"\xff\xd8")

        mock_vision_model.generate.side_effect = RuntimeError(
            "provider response (status code: 500)"
        )
        processor = VisionProcessor(
            vision_model=mock_vision_model,
            backend_cooldown_seconds=120.0,
        )

        result = processor.process_file(img)

        assert result.error is None
        assert processor._is_circuit_open() is False

    def test_failed_mid_flight_inference_still_records_inference_ms(
        self, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """A model call that raises mid-flight DOES record inference_ms (#410).

        CodeRabbit P2 round-trip on PR #424: failed-but-attempted
        inferences must contribute to p95/p99 so operators see real
        tail latency during degraded-backend periods. Only purely
        pre-inference paths (circuit-open at the very start,
        file-not-found) should be excluded.
        """
        img1 = tmp_path / "trip.jpg"
        img2 = tmp_path / "after.jpg"
        img1.write_bytes(b"\xff\xd8")
        img2.write_bytes(b"\xff\xd8")

        # Fatal error trips the circuit on the first call; the second
        # invocation then short-circuits BEFORE any model call.
        mock_vision_model.generate.side_effect = RuntimeError(
            "model runner has unexpectedly stopped"
        )
        processor = VisionProcessor(
            vision_model=mock_vision_model,
            backend_cooldown_seconds=120.0,
        )

        first = processor.process_file(img1)
        # First call: model WAS invoked (and failed) → inference_ms set
        assert first.error is not None
        assert isinstance(first.inference_ms, float)
        assert first.inference_ms < 5000.0

        # Second call: circuit-open short-circuit → inference_ms None
        second = processor.process_file(img2)
        assert second.error is not None
        assert "Vision backend unavailable" in second.error
        assert second.inference_ms is None


@pytest.mark.unit
class TestVisionProcessorCleanName:
    """Tests for VisionProcessor._clean_ai_generated_name()."""

    def test_clean_basic(self, vision_processor: VisionProcessor) -> None:
        """Test basic name cleaning."""
        assert (
            vision_processor._clean_ai_generated_name("Nature Photography") == "nature_photography"
        )

    def test_clean_bad_words_filtered(self, vision_processor: VisionProcessor) -> None:
        """Test bad words (articles, generic terms) are filtered."""
        result = vision_processor._clean_ai_generated_name("the image photo")
        assert result == ""

    def test_clean_deduplication(self, vision_processor: VisionProcessor) -> None:
        """Test duplicate words are removed."""
        result = vision_processor._clean_ai_generated_name("cat cat dog")
        assert result == "cat_dog"

    def test_clean_max_words(self, vision_processor: VisionProcessor) -> None:
        """Test word count is limited to max_words."""
        result = vision_processor._clean_ai_generated_name("one two three four", max_words=2)
        assert result == "one_two"

    def test_clean_special_characters(self, vision_processor: VisionProcessor) -> None:
        """Test special characters and numbers are removed."""
        result = vision_processor._clean_ai_generated_name("test@123 file!")
        assert result == "test_file"

    def test_clean_empty_input(self, vision_processor: VisionProcessor) -> None:
        """Test empty input returns empty string."""
        assert vision_processor._clean_ai_generated_name("") == ""

    def test_clean_single_char_words_filtered(self, vision_processor: VisionProcessor) -> None:
        """Test single-character words are filtered out."""
        result = vision_processor._clean_ai_generated_name("a b c real word")
        assert result == "real_word"

    def test_clean_underscores_and_hyphens(self, vision_processor: VisionProcessor) -> None:
        """Test underscores and hyphens are converted to spaces before processing."""
        result = vision_processor._clean_ai_generated_name("nature-landscape_view")
        assert result == "nature_landscape_view"


@pytest.mark.ci
def test_finalize_folder_name_cleans_and_truncates(
    vision_processor: VisionProcessor,
) -> None:
    """Folder-name finalizer strips prefixes, cleans, and falls back when empty."""
    assert (
        vision_processor._finalize_folder_name("Category: Nature Photography!!")
        == "nature_photography"
    )
    assert vision_processor._finalize_folder_name("") == "images"


@pytest.mark.ci
def test_finalize_filename_cleans_and_falls_back_to_stem(
    vision_processor: VisionProcessor, tmp_path: Path
) -> None:
    """Filename finalizer strips prefixes/extension and falls back to the stem."""
    img = tmp_path / "DSC_0001.jpg"
    assert vision_processor._finalize_filename("filename: Sunset Over Mountains.png", img) == (
        "sunset_over_mountains"
    )
    assert vision_processor._finalize_filename("", img) == "DSC_0001"


@pytest.mark.unit
class TestVisionProcessorDescriptionAndOCR:
    """Tests for _generate_description and _extract_text methods."""

    def test_generate_description_success(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test successful description generation."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "  A sunset over the ocean  "

        result = vision_processor._generate_description(img)

        assert result == "A sunset over the ocean"

    def test_generate_description_error_fallback(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test description error returns fallback message."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.side_effect = RuntimeError("model error")

        result = vision_processor._generate_description(img)

        assert result == "Image from test.jpg"

    def test_extract_text_success(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test successful text extraction."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "Welcome to the conference 2024"

        result = vision_processor._extract_text(img)

        assert result == "Welcome to the conference 2024"

    def test_extract_text_no_text_response(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test NO_TEXT response returns None."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "NO_TEXT"

        result = vision_processor._extract_text(img)

        assert result is None

    def test_extract_text_none_response(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test NONE response returns None."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "None"

        result = vision_processor._extract_text(img)

        assert result is None

    def test_extract_text_too_short(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test short text (<10 chars) returns None."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "Hi"

        result = vision_processor._extract_text(img)

        assert result is None

    def test_extract_text_error(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test extraction error returns None."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.side_effect = RuntimeError("error")

        result = vision_processor._extract_text(img)

        assert result is None

    def test_extract_text_na_response(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test 'N/A' response returns None."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "N/A"

        result = vision_processor._extract_text(img)

        assert result is None

    def test_extract_text_no_text_with_space(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test 'NO TEXT' (with space) response returns None."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "NO TEXT"

        result = vision_processor._extract_text(img)

        assert result is None


@pytest.mark.unit
class TestVisionProcessorFolderAndFilename:
    """Tests for _generate_folder_name and _generate_filename methods."""

    def test_generate_folder_name_success(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test successful folder name generation."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "nature_landscapes"

        result = vision_processor._generate_folder_name(img, "A sunset photo")

        assert result == "nature_landscapes"

    def test_generate_folder_name_strips_prefix(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test folder name generation strips common AI prefixes."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "category: nature"

        result = vision_processor._generate_folder_name(img, "context")

        assert "nature" in result

    def test_generate_folder_name_empty_fallback(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test empty/too-short folder name falls back to 'images'."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        # "the" is a bad word and will be filtered, leaving empty string
        mock_vision_model.generate.return_value = "the"

        result = vision_processor._generate_folder_name(img, "context")

        assert result == "images"

    def test_generate_folder_name_error(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test folder name error returns 'images'."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.side_effect = RuntimeError("error")

        result = vision_processor._generate_folder_name(img, "context")

        assert result == "images"

    def test_generate_filename_success(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test successful filename generation."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "mountain_sunset_view"

        result = vision_processor._generate_filename(img, "A sunset photo")

        assert result == "mountain_sunset_view"

    def test_generate_filename_strips_extension(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test filename generation removes file extensions added by AI."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.return_value = "sunset_photo.jpg"

        result = vision_processor._generate_filename(img, "context")

        assert ".jpg" not in result

    def test_generate_filename_empty_fallback(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test empty filename falls back to original stem."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        # "photo" is a bad word, will be filtered
        mock_vision_model.generate.return_value = "photo"

        result = vision_processor._generate_filename(img, "context")

        assert result == "test"  # Falls back to img.stem

    def test_generate_filename_error(
        self, vision_processor: VisionProcessor, mock_vision_model: MagicMock, tmp_path: Path
    ) -> None:
        """Test filename error returns original stem."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        mock_vision_model.generate.side_effect = RuntimeError("error")

        result = vision_processor._generate_filename(img, "context")

        assert result == "test"


@pytest.mark.unit
class TestVisionProcessorLifecycle:
    """Tests for VisionProcessor cleanup and context manager."""

    def test_cleanup_owns_model(self, mock_vision_model: MagicMock) -> None:
        """Test cleanup calls model cleanup when processor owns the model."""
        mock_model = MagicMock()
        with patch("services.vision_processor.VisionModel") as mock_cls:
            mock_cls.get_default_config.return_value = MagicMock()
            with patch(
                "services.vision_processor.get_vision_model",
                return_value=mock_model,
            ):
                processor = VisionProcessor()
                assert processor._owns_model is True

                processor.cleanup()

                mock_model.safe_cleanup.assert_called_once()

    def test_cleanup_does_not_own_model(self, mock_vision_model: MagicMock) -> None:
        """Test cleanup skips model cleanup when processor doesn't own it."""
        processor = VisionProcessor(vision_model=mock_vision_model)
        assert processor._owns_model is False

        processor.cleanup()

        mock_vision_model.cleanup.assert_not_called()

    def test_context_manager(self, mock_vision_model: MagicMock) -> None:
        """Test context manager calls initialize and cleanup."""
        mock_vision_model.is_initialized = False
        processor = VisionProcessor(vision_model=mock_vision_model)

        with processor as p:
            assert p is processor
            mock_vision_model.initialize.assert_called_once()

        # Cleanup should not call model.cleanup since we don't own the model
        mock_vision_model.cleanup.assert_not_called()


# ---------------------------------------------------------------------------
# compute_vision_timeout — adaptive per-image timeout (#407)
# ---------------------------------------------------------------------------


class TestComputeVisionTimeout:
    """Boundary tests for compute_vision_timeout(file_size_bytes, settings)."""

    def test_zero_byte_file_returns_base_timeout(self) -> None:
        """A 0-byte image gets exactly base_timeout (size contribution = 0)."""
        settings = ProcessingSettings()  # base=30, per_mb=15, max=300
        assert compute_vision_timeout(0, settings) == 30.0

    def test_100kb_image_under_35s(self) -> None:
        """Per the issue: 100KB image must be ≤ 35s with defaults."""
        # 100 KB ≈ 0.0977 MB → 30 + 0.0977*15 ≈ 31.46s
        assert compute_vision_timeout(100 * 1024) <= 35.0

    def test_10mb_image_at_or_below_base_plus_150(self) -> None:
        """Per the issue: 10MB image must be ≤ min(base + 150, max) with defaults."""
        # 10 MB → 30 + 10*15 = 180s; min(30+150, 300) = 180 → exactly at bound
        result = compute_vision_timeout(10 * 1024 * 1024)
        assert result == 180.0
        assert result <= min(30.0 + 150.0, 300.0)

    def test_huge_image_clamped_to_max(self) -> None:
        """A 100MB image would raw=1530s; clamped to vision_max_timeout_s=300s."""
        assert compute_vision_timeout(100 * 1024 * 1024) == 300.0

    def test_exactly_at_max_threshold(self) -> None:
        """A file size yielding raw == max returns max exactly."""
        settings = ProcessingSettings()
        # raw = 300 means size_mb = (300 - 30) / 15 = 18MB exactly
        size_at_max = 18 * 1024 * 1024
        assert compute_vision_timeout(size_at_max, settings) == 300.0

    def test_above_max_clamps_not_panics(self) -> None:
        """Sizes that would overshoot max return exactly max (not max + 1)."""
        settings = ProcessingSettings()
        # raw would be ~1500s
        assert compute_vision_timeout(100 * 1024 * 1024, settings) == 300.0

    def test_negative_size_treated_as_zero(self) -> None:
        """Defensive: a bogus negative size falls back to base, not below it."""
        assert compute_vision_timeout(-1024, ProcessingSettings()) == 30.0

    def test_settings_none_uses_defaults(self) -> None:
        """Omitting settings is equivalent to passing ProcessingSettings()."""
        assert compute_vision_timeout(0) == compute_vision_timeout(0, ProcessingSettings())
        assert compute_vision_timeout(5 * 1024 * 1024) == compute_vision_timeout(
            5 * 1024 * 1024, ProcessingSettings()
        )

    def test_custom_settings_propagate(self) -> None:
        """Non-default ProcessingSettings produce a correspondingly different result."""
        settings = ProcessingSettings(
            vision_base_timeout_s=60.0,
            vision_per_mb_factor_s=5.0,
            vision_max_timeout_s=120.0,
        )
        # 10MB → 60 + 50 = 110s (within max=120) → 110.0
        assert compute_vision_timeout(10 * 1024 * 1024, settings) == 110.0
        # 30MB → raw=210, clamped to 120
        assert compute_vision_timeout(30 * 1024 * 1024, settings) == 120.0

    def test_zero_per_mb_factor_yields_flat_timeout(self) -> None:
        """vision_per_mb_factor_s=0 means every image gets exactly base."""
        settings = ProcessingSettings(vision_per_mb_factor_s=0.0)
        assert compute_vision_timeout(0, settings) == 30.0
        assert compute_vision_timeout(1024 * 1024, settings) == 30.0
        assert compute_vision_timeout(50 * 1024 * 1024, settings) == 30.0


class TestProcessFileLogsAdaptiveTimeout:
    """process_file emits the adaptive timeout at DEBUG (#407)."""

    def test_logs_computed_timeout_on_existing_file(
        self,
        mock_vision_model: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A real image file is stat()'d and the adaptive timeout logged at DEBUG."""
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 50_000)

        processor = VisionProcessor(vision_model=mock_vision_model)

        with patch("services.vision_processor.logger") as mock_logger:
            processor.process_file(
                img,
                generate_description=False,
                generate_folder=False,
                generate_filename=False,
                perform_ocr=False,
            )

        adaptive_logs = [
            call
            for call in mock_logger.debug.call_args_list
            if call.args and "Adaptive vision timeout" in str(call.args[0])
        ]
        assert len(adaptive_logs) == 1
        # Message should include the file name and the numeric timeout
        _msg, *args = adaptive_logs[0].args
        assert "shot.png" in args
        # Computed value should be in [base, max]
        assert 30.0 <= args[1] <= 300.0

    def test_stat_failure_does_not_break_processing(
        self,
        mock_vision_model: MagicMock,
        tmp_path: Path,
    ) -> None:
        """An OSError from stat() must not block the real processing path."""
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 10)

        processor = VisionProcessor(vision_model=mock_vision_model)

        with patch("pathlib.Path.stat", side_effect=PermissionError("denied")):
            result = processor.process_file(
                img,
                generate_description=False,
                generate_folder=False,
                generate_filename=False,
                perform_ocr=False,
            )

        assert isinstance(result, ProcessedImage)
