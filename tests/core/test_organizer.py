"""Tests for Core Organizer logic.

After the God Object decomposition, tests are organized by module:
- FileOrganizer facade (public API)
- file_ops (collect, organize, simulate, fallback, cleanup)
- display (Rich UI helpers)
- initializer (processor startup)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.organizer import FileOrganizer
from core.types import OrganizationResult
from models.base import ModelConfig, ModelType
from services.text_processor import ProcessedFile
from services.vision_processor import ProcessedImage


@pytest.fixture
def text_config() -> ModelConfig:
    return ModelConfig(name="test-text", model_type=ModelType.TEXT)


@pytest.fixture
def vision_config() -> ModelConfig:
    return ModelConfig(name="test-vision", model_type=ModelType.VISION)


@pytest.fixture
def organizer(text_config: ModelConfig, vision_config: ModelConfig) -> FileOrganizer:
    """FileOrganizer instance configured for testing."""
    return FileOrganizer(
        text_model_config=text_config,
        vision_model_config=vision_config,
        dry_run=True,
        use_hardlinks=False,
    )


# ---------------------------------------------------------------------------
# FileOrganizer facade tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestFileOrganizer:
    """Tests for FileOrganizer class."""

    def test_init(self, text_config: ModelConfig, vision_config: ModelConfig) -> None:
        """Test default and custom initialization."""
        with patch(
            "config.provider_env.get_model_configs",
            return_value=(
                ModelConfig(name="qwen2.5:3b-instruct-q4_K_M", model_type=ModelType.TEXT),
                ModelConfig(name="qwen2.5vl:7b-q4_K_M", model_type=ModelType.VISION),
            ),
        ):
            org = FileOrganizer()
        assert org.text_model_config.name == "qwen2.5:3b-instruct-q4_K_M"
        assert org.dry_run is True

        org = FileOrganizer(
            text_model_config=text_config,
            vision_model_config=vision_config,
            dry_run=False,
            use_hardlinks=True,
            parallel_workers=2,
            prefetch_depth=3,
        )
        assert org.text_model_config == text_config
        assert org.dry_run is False
        assert org.use_hardlinks is True
        assert org.parallel_config.max_workers == 2
        assert org.parallel_config.prefetch_depth == 3

        no_prefetch_org = FileOrganizer(
            text_model_config=text_config,
            vision_model_config=vision_config,
            no_prefetch=True,
            prefetch_depth=5,
        )
        assert no_prefetch_org.parallel_config.prefetch_depth == 0

        # Backward-compat positional shape: (..., parallel_workers, no_prefetch)
        legacy_positional = FileOrganizer(
            text_config,
            vision_config,
            True,
            True,
            None,
            True,
        )
        assert legacy_positional.no_prefetch is True
        assert legacy_positional.prefetch_depth == 0

    def test_organize_input_missing(self, organizer: FileOrganizer, tmp_path: Path) -> None:
        """Test organizing fails when input path does not exist."""
        with pytest.raises(ValueError, match="Input path does not exist"):
            organizer.organize(tmp_path / "missing", tmp_path / "out")

    @patch("core.file_ops.collect_files")
    def test_organize_empty_directory(
        self, mock_collect: MagicMock, organizer: FileOrganizer, tmp_path: Path
    ) -> None:
        """Test organizing an empty directory returns early."""
        mock_collect.return_value = []

        result = organizer.organize(tmp_path, tmp_path / "out")

        mock_collect.assert_called_once()
        assert result.total_files == 0

    def test_extension_classvars_are_sets(self, organizer: FileOrganizer) -> None:
        """Verify extension ClassVars are backward-compatible sets."""
        assert isinstance(FileOrganizer.TEXT_EXTENSIONS, set)
        assert ".pdf" in FileOrganizer.TEXT_EXTENSIONS
        assert ".jpg" in FileOrganizer.IMAGE_EXTENSIONS
        assert ".mp4" in FileOrganizer.VIDEO_EXTENSIONS
        assert ".mp3" in FileOrganizer.AUDIO_EXTENSIONS
        assert ".dwg" in FileOrganizer.CAD_EXTENSIONS

    def test_timeout_per_file_default_is_300s(self, organizer: FileOrganizer) -> None:
        """Default per-file timeout is 300s (5 min) — see issue #396."""
        assert organizer.timeout_per_file == 300.0
        assert organizer.parallel_config.timeout_per_file == 300.0

    def test_timeout_per_file_propagates_to_parallel_config(
        self, text_config: ModelConfig, vision_config: ModelConfig
    ) -> None:
        """Explicit timeout_per_file flows through to ParallelConfig (#396)."""
        org = FileOrganizer(
            text_model_config=text_config,
            vision_model_config=vision_config,
            timeout_per_file=90.0,
        )
        assert org.timeout_per_file == 90.0
        assert org.parallel_config.timeout_per_file == 90.0

    def test_timeout_per_file_rejects_non_positive(
        self, text_config: ModelConfig, vision_config: ModelConfig
    ) -> None:
        """Zero and negative timeout values are rejected at construction (#396)."""
        with pytest.raises(ValueError, match="timeout_per_file must be > 0"):
            FileOrganizer(
                text_model_config=text_config,
                vision_model_config=vision_config,
                timeout_per_file=0.0,
            )
        with pytest.raises(ValueError, match="timeout_per_file must be > 0"):
            FileOrganizer(
                text_model_config=text_config,
                vision_model_config=vision_config,
                timeout_per_file=-5.0,
            )

    def test_no_vision_uses_extension_fallback_for_images(self, tmp_path: Path) -> None:
        """When vision is disabled, image files should route through fallback."""
        src = tmp_path / "src"
        src.mkdir()
        image = src / "photo.jpg"
        image.write_bytes(b"\xff\xd8\xff\xe0")

        out = tmp_path / "out"
        organizer = FileOrganizer(dry_run=True, enable_vision=False)

        with (
            patch.object(
                organizer,
                "_fallback_by_extension",
                wraps=organizer._fallback_by_extension,
            ) as mock_fallback,
            patch.object(organizer, "_process_image_files") as mock_process_images,
        ):
            result = organizer.organize(src, out)

        assert result.failed_files == 0
        mock_process_images.assert_not_called()
        mock_fallback.assert_called_once()
        assert mock_fallback.call_args.args[0] == [image]

    def test_organizer_collects_low_confidence_results(self, tmp_path: Path) -> None:
        """#409 end-to-end: organizer aggregation routes low-confidence
        results into ``OrganizationResult.low_confidence_files`` and
        partitions inference_ms samples by modality (#410).

        Drives ``organize`` with a synthetic five-file batch:
        - happy-path vision (confidence=1.0, vision sample)
        - EXIF fallback @ 0.5 (low-conf, no inference_ms — fallback
          built by dispatcher)
        - filename fallback @ 0.3 (low-conf)
        - error @ 0.0 (low-conf)
        - happy-path text (confidence=1.0, TEXT sample so the else
          branch in the aggregator runs)

        Asserts: three non-happy entries land in low_confidence_files;
        vision/text inference_ms partition correctly. ci-marked so PR
        diff-coverage counts the organizer aggregation lines.
        """
        from services.text_processor import ProcessedFile
        from services.vision_processor import ProcessedImage

        results: list[ProcessedImage | ProcessedFile] = [
            ProcessedImage(
                file_path=tmp_path / "ok.png",
                description="d",
                folder_name="images",
                filename="ok",
                confidence=1.0,
                inference_ms=100.0,
            ),
            ProcessedImage(
                file_path=tmp_path / "exif.jpg",
                description="",
                folder_name="Images/Photos/2025/11",
                filename="exif",
                source="fallback_exif",
                confidence=0.5,
            ),
            ProcessedImage(
                file_path=tmp_path / "name.png",
                description="",
                folder_name="Images/Screenshots/2026",
                filename="name",
                source="fallback_filename",
                confidence=0.3,
            ),
            ProcessedImage(
                file_path=tmp_path / "bad.png",
                description="",
                folder_name="errors",
                filename="bad",
                error="boom",
                confidence=0.0,
            ),
            ProcessedFile(
                file_path=tmp_path / "doc.txt",
                description="d",
                folder_name="docs",
                filename="doc",
                confidence=1.0,
                inference_ms=80.0,
            ),
        ]
        # Distinct bytes per file so the SHA-256 dedup pass (#411 moved
        # the error-bucket aggregation post-dedup) doesn't collapse them
        # into a single survivor.
        for i, r in enumerate(results):
            r.file_path.write_bytes(f"unique-{i}".encode())

        organizer = FileOrganizer(dry_run=True, enable_vision=False)
        with (
            patch.object(
                organizer,
                "_categorize_files",
                return_value=([], [r.file_path for r in results], [], [], [], []),
            ),
            patch.object(organizer, "_process_all_file_types", return_value=results),
            patch.object(organizer, "_execute_organization"),
        ):
            result = organizer.organize(tmp_path, tmp_path / "out")

        # Default threshold (0.5), inclusive-but-not-1.0 → EXIF + name + bad.
        assert set(result.low_confidence_files) == {"exif.jpg", "name.png", "bad.png"}
        # Happy-path file is never flagged.
        assert "ok.png" not in result.low_confidence_files
        assert "doc.txt" not in result.low_confidence_files
        # Inference samples partition by modality (#410).
        assert result.vision_inference_ms_samples == [100.0]
        assert result.text_inference_ms_samples == [80.0]
        # Structured error breakdown (#411): the same batch yields
        # vision_timeout (two fallback_* sources) and inference_error
        # (confidence==0.0 with an error string). Happy-path entries
        # don't bucket. Examples are basenames of the first hit per
        # bucket.
        assert dict(result.error_breakdown) == {
            "vision_timeout": 2,
            "inference_error": 1,
        }
        assert result.error_examples["vision_timeout"] == "exif.jpg"
        assert result.error_examples["inference_error"] == "bad.png"

    def test_organizer_falls_back_to_default_threshold_on_config_error(
        self, tmp_path: Path
    ) -> None:
        """#409: ConfigManager.load failure degrades to ProcessingSettings()
        default rather than crashing the aggregation loop."""
        from services.vision_processor import ProcessedImage

        img = ProcessedImage(
            file_path=tmp_path / "exif.jpg",
            description="",
            folder_name="Images/Photos/2025/11",
            filename="exif",
            source="fallback_exif",
            confidence=0.5,
        )
        img.file_path.write_bytes(b"")

        organizer = FileOrganizer(dry_run=True, enable_vision=False)
        # Make ConfigManager raise to exercise the except branch.
        with (
            patch.object(
                organizer,
                "_categorize_files",
                return_value=([], [img.file_path], [], [], [], []),
            ),
            patch.object(organizer, "_process_all_file_types", return_value=[img]),
            patch.object(organizer, "_execute_organization"),
            patch(
                "config.manager.ConfigManager",
                side_effect=RuntimeError("config broken"),
            ),
        ):
            result = organizer.organize(tmp_path, tmp_path / "out")

        # Fallback to ProcessingSettings() default (0.5); the 0.5
        # confidence still lands in review under the inclusive
        # comparator.
        assert "exif.jpg" in result.low_confidence_files

    def test_threshold_one_does_not_flag_happy_path(self, tmp_path: Path) -> None:
        """#409 / Codex P2: threshold=1.0 must not flood the review list.

        Confidence==1.0 is the happy path and should never be flagged,
        regardless of threshold. Verifies the
        ``confidence < 1.0`` cap in the comparator.
        """
        from unittest.mock import MagicMock

        from services.vision_processor import ProcessedImage

        happy = ProcessedImage(
            file_path=tmp_path / "ok.png",
            description="d",
            folder_name="images",
            filename="ok",
            confidence=1.0,
        )
        happy.file_path.write_bytes(b"")

        organizer = FileOrganizer(dry_run=True, enable_vision=False)

        # Patch the threshold loader to return 1.0 (the inclusive max).
        mock_manager = MagicMock()
        mock_app_cfg = MagicMock()
        mock_app_cfg.processing.low_confidence_threshold = 1.0
        mock_manager.load.return_value = mock_app_cfg
        with (
            patch.object(
                organizer,
                "_categorize_files",
                return_value=([], [happy.file_path], [], [], [], []),
            ),
            patch.object(organizer, "_process_all_file_types", return_value=[happy]),
            patch.object(organizer, "_execute_organization"),
            patch("config.manager.ConfigManager", return_value=mock_manager),
        ):
            result = organizer.organize(tmp_path, tmp_path / "out")

        assert result.low_confidence_files == []


# ---------------------------------------------------------------------------
# file_ops module tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestFileOps:
    """Tests for core.file_ops module."""

    def test_collect_files(self, tmp_path: Path) -> None:
        """Test scanning files in a directory hierarchy."""
        from core.file_ops import collect_files

        (tmp_path / "file1.txt").touch()
        (tmp_path / ".hidden.txt").touch()
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file2.jpg").touch()

        console = MagicMock()
        files = collect_files(tmp_path, console)

        assert len(files) == 2
        names = {f.name for f in files}
        assert "file1.txt" in names
        assert "file2.jpg" in names
        assert ".hidden.txt" not in names

    def test_simulate_organization(self, tmp_path: Path) -> None:
        """Test simulation builds output structure without creating files."""
        from core.file_ops import simulate_organization

        p1 = ProcessedFile(tmp_path / "f1.txt", "", "docs", "file_1")
        p2 = ProcessedFile(tmp_path / "f2.txt", "", "docs", "file_2")
        p3 = ProcessedImage(tmp_path / "i1.jpg", "", "images", "img_1")
        err = ProcessedFile(tmp_path / "e.txt", "", "errs", "e", error="fail")

        out_path = tmp_path / "out"
        structure = simulate_organization([p1, p2, p3, err], out_path)

        assert structure == {
            "docs": ["file_1.txt", "file_2.txt"],
            "images": ["img_1.jpg"],
        }
        assert not out_path.exists()

    @patch("core.file_ops.shutil.copy2")
    def test_organize_files_copy(self, mock_copy: MagicMock, tmp_path: Path) -> None:
        """Test physical file copy organization."""
        from core.file_ops import organize_files

        out_path = tmp_path / "out"
        f1 = tmp_path / "f1.txt"

        proc = ProcessedFile(f1, "", "docs", "file_1")

        structure = organize_files(
            [proc],
            out_path,
            skip_existing=True,
            use_hardlinks=False,
            undo_manager=None,
            transaction_id=None,
        )

        assert structure == {"docs": ["file_1.txt"]}
        mock_copy.assert_called_once_with(f1, out_path / "docs" / "file_1.txt")
        assert (out_path / "docs").is_dir()

    @patch("core.file_ops.os.link")
    def test_organize_files_hardlink(self, mock_link: MagicMock, tmp_path: Path) -> None:
        """Test physical file hardlink organization."""
        from core.file_ops import organize_files

        out_path = tmp_path / "out"
        f1 = tmp_path / "f1.txt"

        proc = ProcessedFile(f1, "", "docs", "file_1")

        structure = organize_files(
            [proc],
            out_path,
            skip_existing=True,
            use_hardlinks=True,
            undo_manager=None,
            transaction_id=None,
        )

        assert structure == {"docs": ["file_1.txt"]}
        mock_link.assert_called_once_with(f1, out_path / "docs" / "file_1.txt")

    @patch("core.file_ops.shutil.copy2")
    def test_organize_files_collision(self, mock_copy: MagicMock, tmp_path: Path) -> None:
        """Test handling of identical filenames during copy."""
        from core.file_ops import organize_files

        out_path = tmp_path / "out"
        docs_dir = out_path / "docs"
        docs_dir.mkdir(parents=True)
        (docs_dir / "file_1.txt").touch()

        f1 = tmp_path / "f1.txt"
        proc = ProcessedFile(f1, "", "docs", "file_1")

        structure = organize_files(
            [proc],
            out_path,
            skip_existing=False,
            use_hardlinks=False,
            undo_manager=None,
            transaction_id=None,
        )

        assert structure == {"docs": ["file_1_1.txt"]}
        mock_copy.assert_called_once_with(f1, out_path / "docs" / "file_1_1.txt")

    def test_fallback_by_extension(self, tmp_path: Path) -> None:
        """Test extension-based fallback organization."""
        from core.file_ops import fallback_by_extension

        files = [tmp_path / "doc.pdf", tmp_path / "sheet.xlsx"]
        results = fallback_by_extension(files)

        assert len(results) == 2
        assert results[0].folder_name == "PDFs"
        assert results[1].folder_name == "Spreadsheets"

    def test_cleanup_empty_dirs(self, tmp_path: Path) -> None:
        """Test empty directory cleanup removes only empty subdirs."""
        from core.file_ops import cleanup_empty_dirs

        (tmp_path / "empty_sub").mkdir()
        (tmp_path / "non_empty_sub").mkdir()
        (tmp_path / "non_empty_sub" / "file.txt").touch()

        cleanup_empty_dirs(tmp_path)

        assert not (tmp_path / "empty_sub").exists()
        assert (tmp_path / "non_empty_sub").exists()
        assert tmp_path.exists()  # Root preserved


# ---------------------------------------------------------------------------
# display module tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestDisplay:
    """Tests for core.display module."""

    def test_show_file_breakdown_renders_table(self, tmp_path: Path) -> None:
        """Ensure show_file_breakdown renders a Rich Table."""
        from rich.table import Table

        from core.display import show_file_breakdown

        console = MagicMock()
        show_file_breakdown(
            console,
            text_files=[tmp_path],
            image_files=[tmp_path],
            video_files=[tmp_path],
            audio_files=[tmp_path],
            cad_files=[tmp_path],
            other_files=[tmp_path],
        )
        console.print.assert_called_once()
        printed_arg = console.print.call_args[0][0]
        assert isinstance(printed_arg, Table)

    def test_show_summary_does_not_crash(self, tmp_path: Path) -> None:
        """Ensure show_summary renders statistics output."""
        from core.display import show_summary

        console = MagicMock()
        res = OrganizationResult(total_files=5, processing_time=1.0)
        show_summary(console, res, tmp_path, dry_run=True)
        # Should print multiple lines of summary stats
        assert console.print.call_count >= 2

    def test_show_summary_surfaces_deduplicated_count(self, tmp_path: Path) -> None:
        """show_summary prints deduplicated line when deduplicated_files > 0."""
        from core.display import show_summary

        console = MagicMock()
        res = OrganizationResult(total_files=5, processed_files=3, deduplicated_files=2)
        show_summary(console, res, tmp_path, dry_run=False)
        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert "2" in printed and "uplicate" in printed


# ---------------------------------------------------------------------------
# initializer module tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestInitializer:
    """Tests for core.initializer module."""

    @patch("core.initializer.TextProcessor")
    def test_init_text_processor_success(self, mock_text_cls: MagicMock) -> None:
        """Successful text processor init returns initialized processor."""
        from core.initializer import init_text_processor

        config = ModelConfig(name="test", model_type=ModelType.TEXT)
        console = MagicMock()
        result = init_text_processor(config, console)

        mock_text_cls.assert_called_once_with(config=config)
        mock_text_cls.return_value.initialize.assert_called_once()
        assert result is mock_text_cls.return_value

    @patch("core.initializer.TextProcessor")
    def test_init_text_processor_failure_returns_none(self, mock_text_cls: MagicMock) -> None:
        """Any exception during text init returns None."""
        from core.initializer import init_text_processor

        mock_text_cls.return_value.initialize.side_effect = ConnectionRefusedError("down")
        config = ModelConfig(name="test", model_type=ModelType.TEXT)
        console = MagicMock()
        result = init_text_processor(config, console)

        assert result is None

    @patch("core.initializer.VisionProcessor")
    def test_init_vision_processor_success(self, mock_vision_cls: MagicMock) -> None:
        """Successful vision processor init returns initialized processor."""
        from core.initializer import init_vision_processor

        config = ModelConfig(name="test", model_type=ModelType.VISION)
        console = MagicMock()
        result = init_vision_processor(config, console)

        mock_vision_cls.assert_called_once_with(config=config)
        mock_vision_cls.return_value.initialize.assert_called_once()
        assert result is mock_vision_cls.return_value

    @patch("core.initializer.VisionProcessor")
    def test_init_vision_processor_failure_returns_none(self, mock_vision_cls: MagicMock) -> None:
        """Any exception during vision init returns None."""
        from core.initializer import init_vision_processor

        mock_vision_cls.return_value.initialize.side_effect = ImportError("missing")
        config = ModelConfig(name="test", model_type=ModelType.VISION)
        console = MagicMock()
        result = init_vision_processor(config, console)

        assert result is None


# ---------------------------------------------------------------------------
# _categorize_files tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestCategorizeFiles:
    """Unit tests for FileOrganizer._categorize_files()."""

    def test_routes_by_extension(self, tmp_path: Path) -> None:
        """Files are routed to the correct bucket based on extension."""
        organizer = FileOrganizer.__new__(FileOrganizer)

        text, image, video, audio, cad, other = organizer._categorize_files(
            [tmp_path / "doc.txt", tmp_path / "photo.jpg", tmp_path / "movie.mp4"]
        )

        assert len(text) == 1
        assert text[0].name == "doc.txt"
        assert len(image) == 1
        assert image[0].name == "photo.jpg"
        assert len(video) == 1
        assert video[0].name == "movie.mp4"
        assert audio == []
        assert cad == []
        assert other == []

    def test_unknown_goes_to_other(self, tmp_path: Path) -> None:
        """Files with unrecognized extensions go to the other bucket."""
        organizer = FileOrganizer.__new__(FileOrganizer)
        f = tmp_path / "mystery.xyz"

        text, image, video, audio, cad, other = organizer._categorize_files([f])

        assert text == image == video == audio == cad == []
        assert len(other) == 1
        assert other[0].name == "mystery.xyz"

    def test_office_temp_lock_file_goes_to_other(self, tmp_path: Path) -> None:
        """Office temporary lock files are skipped via the other bucket."""
        organizer = FileOrganizer.__new__(FileOrganizer)
        f = tmp_path / "~$test.docx"

        with patch("core.organizer.logger.debug") as mock_debug:
            text, image, video, audio, cad, other = organizer._categorize_files([f])

        assert text == image == video == audio == cad == []
        assert len(other) == 1
        assert other[0].name == "~$test.docx"
        mock_debug.assert_called_once_with("skipped: office_temp_file {}", f)

    def test_cad_extension(self, tmp_path: Path) -> None:
        """DWG files are categorized as CAD."""
        organizer = FileOrganizer.__new__(FileOrganizer)
        f = tmp_path / "drawing.dwg"

        _text, _image, _video, _audio, cad, _other = organizer._categorize_files([f])

        assert len(cad) == 1
        assert cad[0].name == "drawing.dwg"

    def test_empty_returns_empty_buckets(self) -> None:
        """Empty input yields six empty lists."""
        organizer = FileOrganizer.__new__(FileOrganizer)

        result = organizer._categorize_files([])

        assert all(lst == [] for lst in result)


# ---------------------------------------------------------------------------
# skipped_by_extension reporting (#412)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestSkippedByExtensionReport:
    """Unit tests for the skipped-extension breakdown on OrganizationResult."""

    def test_organization_result_default_skipped_by_extension_is_empty(self) -> None:
        """OrganizationResult initializes skipped_by_extension to an empty Counter."""
        from collections import Counter

        result = OrganizationResult()
        assert isinstance(result.skipped_by_extension, Counter)
        assert result.skipped_by_extension == Counter()

    def test_skipped_extension_key_for_normal_extension(self, tmp_path: Path) -> None:
        """A normal unsupported extension is lower-cased and returned as-is."""
        assert FileOrganizer._skipped_extension_key(tmp_path / "a.NIB") == ".nib"
        assert FileOrganizer._skipped_extension_key(tmp_path / "b.stl") == ".stl"

    def test_skipped_extension_key_for_office_temp(self, tmp_path: Path) -> None:
        """Office temp lock files bucket under the <office-temp> sentinel."""
        assert FileOrganizer._skipped_extension_key(tmp_path / "~$doc.docx") == "<office-temp>"

    def test_skipped_extension_key_for_extensionless(self, tmp_path: Path) -> None:
        """Files without a suffix bucket under the <no-extension> sentinel."""
        assert FileOrganizer._skipped_extension_key(tmp_path / "README") == "<no-extension>"

    def test_organize_populates_skipped_by_extension(self, tmp_path: Path) -> None:
        """After organize(), skipped_by_extension counts unsupported extensions."""
        from collections import Counter

        src = tmp_path / "src"
        src.mkdir()
        # Three unsupported extensions with varying counts.
        for i in range(3):
            (src / f"a{i}.nib").write_bytes(b"x")
        for i in range(2):
            (src / f"b{i}.stl").write_bytes(b"x")
        (src / "c.xyz").write_bytes(b"x")

        out = tmp_path / "out"
        organizer = FileOrganizer(dry_run=True, enable_vision=False)
        result = organizer.organize(src, out)

        assert isinstance(result.skipped_by_extension, Counter)
        assert result.skipped_by_extension[".nib"] == 3
        assert result.skipped_by_extension[".stl"] == 2
        assert result.skipped_by_extension[".xyz"] == 1
        # Aggregate skipped_files still matches.
        assert result.skipped_files == 6

    def test_extensions_are_case_insensitive(self, tmp_path: Path) -> None:
        """Uppercase/lowercase variants of an unsupported extension merge."""
        from collections import Counter

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.NIB").write_bytes(b"x")
        (src / "b.nib").write_bytes(b"x")
        (src / "c.Nib").write_bytes(b"x")

        out = tmp_path / "out"
        organizer = FileOrganizer(dry_run=True, enable_vision=False)
        result = organizer.organize(src, out)

        assert isinstance(result.skipped_by_extension, Counter)
        assert result.skipped_by_extension[".nib"] == 3

    def test_office_temp_lock_files_use_sentinel_extension(self, tmp_path: Path) -> None:
        """Office temp lock files (~$*) bucket under <office-temp>, not .docx."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "~$doc.docx").write_bytes(b"x")
        (src / "~$sheet.xlsx").write_bytes(b"x")

        out = tmp_path / "out"
        organizer = FileOrganizer(dry_run=True, enable_vision=False)
        result = organizer.organize(src, out)

        # Office temp files share a sentinel category, not their suffix —
        # using their suffix would route them to .docx/.xlsx which are
        # *supported*, giving misleading actionable signal.
        assert result.skipped_by_extension["<office-temp>"] == 2
        assert result.skipped_by_extension[".docx"] == 0
        assert result.skipped_files == 2

    def test_extensionless_files_use_sentinel(self, tmp_path: Path) -> None:
        """Files without an extension bucket under <no-extension>."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "README").write_bytes(b"x")
        (src / "LICENSE").write_bytes(b"x")

        out = tmp_path / "out"
        organizer = FileOrganizer(dry_run=True, enable_vision=False)
        result = organizer.organize(src, out)

        assert result.skipped_by_extension["<no-extension>"] == 2


# ---------------------------------------------------------------------------
# show_summary skipped-extension display (#412)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestSummarySkippedExtensions:
    """Tests for top-10 / --show-skipped rendering in show_summary."""

    def test_summary_shows_extensions_with_counts(self, tmp_path: Path) -> None:
        """show_summary prints each tallied extension with its count."""
        from collections import Counter

        from core.display import show_summary

        console = MagicMock()
        res = OrganizationResult(
            total_files=10,
            skipped_files=10,
            skipped_by_extension=Counter({".nib": 5, ".stl": 3, ".xyz": 2}),
        )
        show_summary(console, res, tmp_path, dry_run=True)

        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert ".nib" in printed and "5" in printed
        assert ".stl" in printed and "3" in printed
        assert ".xyz" in printed and "2" in printed

    def test_summary_caps_at_top_10_with_hint(self, tmp_path: Path) -> None:
        """With >10 distinct extensions, show top 10 and a hint about the rest."""
        from collections import Counter

        from core.display import show_summary

        console = MagicMock()
        # 12 distinct extensions
        counts = Counter({f".ext{i:02d}": (20 - i) for i in range(12)})
        res = OrganizationResult(
            total_files=sum(counts.values()),
            skipped_files=sum(counts.values()),
            skipped_by_extension=counts,
        )
        show_summary(console, res, tmp_path, dry_run=True)

        printed = " ".join(str(c) for c in console.print.call_args_list)
        # Top-1 .ext00 (count 20) appears; tail .ext11 (count 9) does NOT.
        assert ".ext00" in printed
        assert ".ext11" not in printed
        # Hint message about more.
        assert "--show-skipped" in printed

    def test_summary_show_skipped_lists_all(self, tmp_path: Path) -> None:
        """show_summary(show_skipped=True) prints the full grouped list."""
        from collections import Counter

        from core.display import show_summary

        console = MagicMock()
        counts = Counter({f".ext{i:02d}": (20 - i) for i in range(12)})
        res = OrganizationResult(
            total_files=sum(counts.values()),
            skipped_files=sum(counts.values()),
            skipped_by_extension=counts,
        )
        show_summary(console, res, tmp_path, dry_run=True, show_skipped=True)

        printed = " ".join(str(c) for c in console.print.call_args_list)
        # Every extension must appear.
        for i in range(12):
            assert f".ext{i:02d}" in printed

    def test_summary_omits_breakdown_when_no_skipped_files(self, tmp_path: Path) -> None:
        """No top-extensions block printed when nothing was skipped."""
        from core.display import show_summary

        console = MagicMock()
        res = OrganizationResult(total_files=3, processed_files=3)
        show_summary(console, res, tmp_path, dry_run=True)

        printed = " ".join(str(c) for c in console.print.call_args_list)
        # The breakdown header is only shown when there are skipped entries.
        assert "Top 10 skipped extensions" not in printed
        assert "Skipped by extension" not in printed


# ---------------------------------------------------------------------------
# _deduplicate_processed tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestDeduplicateProcessed:
    """Unit tests for FileOrganizer._deduplicate_processed()."""

    def _make_organizer(self) -> FileOrganizer:
        organizer = FileOrganizer.__new__(FileOrganizer)
        organizer.console = MagicMock()
        return organizer

    def test_identical_content_deduplicated(self, tmp_path: Path) -> None:
        """Two files with identical content: only the first is kept."""
        organizer = self._make_organizer()
        result = OrganizationResult(total_files=2)

        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"same content")
        f2.write_bytes(b"same content")

        pf1 = ProcessedFile(f1, "", "document", "a")
        pf2 = ProcessedFile(f2, "", "document", "b")

        deduped = organizer._deduplicate_processed([pf1, pf2], result)

        assert len(deduped) == 1
        assert result.deduplicated_files == 1

    def test_unique_content_all_kept(self, tmp_path: Path) -> None:
        """Files with distinct content are all retained."""
        organizer = self._make_organizer()
        result = OrganizationResult(total_files=2)

        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")

        pf1 = ProcessedFile(f1, "", "document", "a")
        pf2 = ProcessedFile(f2, "", "document", "b")

        deduped = organizer._deduplicate_processed([pf1, pf2], result)

        assert len(deduped) == 2
        assert result.deduplicated_files == 0

    def test_unreadable_file_kept(self, tmp_path: Path) -> None:
        """A file that raises OSError on read is kept (handled downstream)."""
        organizer = self._make_organizer()
        result = OrganizationResult(total_files=1)

        # Point to a nonexistent path — open() raises OSError
        f = tmp_path / "ghost.txt"
        pf = ProcessedFile(f, "", "document", "ghost")

        deduped = organizer._deduplicate_processed([pf], result)

        assert len(deduped) == 1
        assert result.deduplicated_files == 0


@pytest.mark.unit
@pytest.mark.ci
class TestProcessImageType:
    """Unit tests for FileOrganizer._process_image_type()."""

    def _make_organizer(self, enable_vision: bool = True) -> FileOrganizer:
        organizer = FileOrganizer.__new__(FileOrganizer)
        organizer.console = MagicMock()
        organizer.enable_vision = enable_vision
        organizer.vision_processor = None
        return organizer

    def test_vision_disabled_uses_fallback(self, tmp_path: Path) -> None:
        """When enable_vision is False, _fallback_by_extension is called."""
        organizer = self._make_organizer(enable_vision=False)
        fallback_result = [ProcessedFile(tmp_path / "a.jpg", "", "image", "a")]

        with patch.object(
            organizer, "_fallback_by_extension", return_value=fallback_result
        ) as mock_fb:
            result = organizer._process_image_type([tmp_path / "a.jpg"])

        mock_fb.assert_called_once_with([tmp_path / "a.jpg"])
        assert result == fallback_result

    def test_vision_enabled_not_initialized_uses_fallback(self, tmp_path: Path) -> None:
        """When vision is enabled but processor fails to init, falls back to extension."""
        organizer = self._make_organizer(enable_vision=True)
        fallback_result = [ProcessedFile(tmp_path / "a.jpg", "", "image", "a")]

        # _init_vision_processor sets vision_processor = None (init failed)
        with patch.object(organizer, "_init_vision_processor"):
            organizer.vision_processor = None  # not initialized
            with patch.object(
                organizer, "_fallback_by_extension", return_value=fallback_result
            ) as mock_fb:
                result = organizer._process_image_type([tmp_path / "a.jpg"])

        mock_fb.assert_called_once_with([tmp_path / "a.jpg"])
        assert result == fallback_result

    def test_vision_enabled_and_ready_uses_vision_pipeline(self, tmp_path: Path) -> None:
        """When vision processor is ready, _process_image_files is called."""
        organizer = self._make_organizer(enable_vision=True)
        image_results = [MagicMock(spec=ProcessedImage)]

        mock_processor = MagicMock()
        mock_processor.vision_model.is_initialized = True

        with patch.object(organizer, "_init_vision_processor"):
            organizer.vision_processor = mock_processor
            with patch.object(
                organizer, "_process_image_files", return_value=image_results
            ) as mock_pif:
                result = organizer._process_image_type([tmp_path / "a.jpg"])

        mock_pif.assert_called_once_with([tmp_path / "a.jpg"])
        assert result == image_results
