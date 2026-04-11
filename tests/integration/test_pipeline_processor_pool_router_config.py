"""Integration tests for pipeline modules.

Covers:
  - pipeline/config.py           — PipelineConfig, DEFAULT_SUPPORTED_EXTENSIONS
  - pipeline/router.py           — FileRouter, ProcessorType
  - pipeline/processor_pool.py   — ProcessorPool, BaseProcessor, normalize_processor_result
  - pipeline/stages/analyzer.py  — AnalyzerStage
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# PipelineConfig
# ---------------------------------------------------------------------------


class TestPipelineConfigDefaults:
    def test_default_output_directory(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.output_directory == Path("organized_files")

    def test_default_dry_run_true(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.dry_run is True

    def test_default_auto_organize_false(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.auto_organize is False

    def test_default_max_concurrent(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.max_concurrent == 4

    def test_default_watch_config_is_none(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.watch_config is None

    def test_default_notification_callback_is_none(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.notification_callback is None

    def test_default_supported_extensions_is_none(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.supported_extensions is None


class TestPipelineConfigValidation:
    def test_max_concurrent_zero_raises(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        with pytest.raises(ValueError, match="max_concurrent must be at least 1"):
            PipelineConfig(max_concurrent=0)

    def test_max_concurrent_negative_raises(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        with pytest.raises(ValueError, match="max_concurrent must be at least 1"):
            PipelineConfig(max_concurrent=-1)

    def test_output_directory_coerced_to_path(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig(output_directory=Path("some/dir"))
        assert isinstance(cfg.output_directory, Path)

    def test_supported_extensions_normalized_with_dot(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig(supported_extensions={"pdf", "txt"})
        assert ".pdf" in cfg.supported_extensions  # type: ignore[operator]
        assert ".txt" in cfg.supported_extensions  # type: ignore[operator]

    def test_supported_extensions_already_dotted_preserved(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig(supported_extensions={".md", ".docx"})
        assert ".md" in cfg.supported_extensions  # type: ignore[operator]
        assert ".docx" in cfg.supported_extensions  # type: ignore[operator]

    def test_max_concurrent_one_is_valid(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig(max_concurrent=1)
        assert cfg.max_concurrent == 1


class TestPipelineConfigProperties:
    def test_should_move_files_false_when_dry_run(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig(dry_run=True, auto_organize=True)
        assert cfg.should_move_files is False

    def test_should_move_files_false_when_not_auto_organize(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig(dry_run=False, auto_organize=False)
        assert cfg.should_move_files is False

    def test_should_move_files_true_when_both_set(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig(dry_run=False, auto_organize=True)
        assert cfg.should_move_files is True

    def test_effective_extensions_returns_defaults_when_none(self) -> None:
        from file_organizer.pipeline.config import DEFAULT_SUPPORTED_EXTENSIONS, PipelineConfig

        cfg = PipelineConfig()
        assert cfg.effective_extensions == DEFAULT_SUPPORTED_EXTENSIONS

    def test_effective_extensions_returns_custom_set(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig(supported_extensions={".pdf", ".txt"})
        eff = cfg.effective_extensions
        assert ".pdf" in eff
        assert ".txt" in eff
        assert ".jpg" not in eff

    def test_is_supported_txt_file(self, tmp_path: Path) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.is_supported(Path("report.txt")) is True

    def test_is_supported_pdf_file(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.is_supported(Path("document.pdf")) is True

    def test_is_supported_jpg_image(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.is_supported(Path("photo.jpg")) is True

    def test_is_supported_unknown_extension(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.is_supported(Path("file.xyz123")) is False

    def test_is_supported_case_insensitive(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.is_supported(Path("FILE.PDF")) is True

    def test_is_supported_with_custom_extensions(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        cfg = PipelineConfig(supported_extensions={".custom"})
        assert cfg.is_supported(Path("file.custom")) is True
        assert cfg.is_supported(Path("file.pdf")) is False

    def test_notification_callback_is_called(self) -> None:
        from file_organizer.pipeline.config import PipelineConfig

        calls: list[tuple[Path, bool]] = []

        def callback(path: Path, success: bool) -> None:
            calls.append((path, success))

        cfg = PipelineConfig(notification_callback=callback)
        assert cfg.notification_callback is not None
        cfg.notification_callback(Path("test.txt"), True)
        assert len(calls) == 1
        assert calls[0] == (Path("test.txt"), True)


# ---------------------------------------------------------------------------
# FileRouter
# ---------------------------------------------------------------------------


class TestFileRouterDefaults:
    def test_pdf_routes_to_text(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("doc.pdf")) == ProcessorType.TEXT

    def test_docx_routes_to_text(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("doc.docx")) == ProcessorType.TEXT

    def test_md_routes_to_text(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("notes.md")) == ProcessorType.TEXT

    def test_csv_routes_to_text(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("data.csv")) == ProcessorType.TEXT

    def test_jpg_routes_to_image(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("photo.jpg")) == ProcessorType.IMAGE

    def test_jpeg_routes_to_image(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("photo.jpeg")) == ProcessorType.IMAGE

    def test_png_routes_to_image(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("image.png")) == ProcessorType.IMAGE

    def test_mp4_routes_to_video(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("clip.mp4")) == ProcessorType.VIDEO

    def test_mkv_routes_to_video(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("movie.mkv")) == ProcessorType.VIDEO

    def test_mp3_routes_to_audio(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("song.mp3")) == ProcessorType.AUDIO

    def test_wav_routes_to_audio(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("track.wav")) == ProcessorType.AUDIO

    def test_unknown_extension_routes_to_unknown(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("archive.xyz")) == ProcessorType.UNKNOWN

    def test_extension_lookup_case_insensitive(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("IMAGE.JPG")) == ProcessorType.IMAGE


class TestFileRouterMutation:
    def test_add_extension_with_dot(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        router.add_extension(".custom", ProcessorType.TEXT)
        assert router.route(Path("file.custom")) == ProcessorType.TEXT

    def test_add_extension_without_dot(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        router.add_extension("myext", ProcessorType.IMAGE)
        assert router.route(Path("file.myext")) == ProcessorType.IMAGE

    def test_add_extension_overrides_existing(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        router.add_extension(".pdf", ProcessorType.IMAGE)
        assert router.route(Path("file.pdf")) == ProcessorType.IMAGE

    def test_remove_extension_causes_unknown(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        router.remove_extension(".pdf")
        assert router.route(Path("file.pdf")) == ProcessorType.UNKNOWN

    def test_remove_extension_without_dot(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        router.remove_extension("pdf")
        assert router.route(Path("file.pdf")) == ProcessorType.UNKNOWN

    def test_remove_unknown_extension_raises(self) -> None:
        from file_organizer.pipeline.router import FileRouter

        router = FileRouter()
        with pytest.raises(KeyError):
            router.remove_extension(".doesnotexist")

    def test_get_extension_map_returns_copy(self) -> None:
        from file_organizer.pipeline.router import FileRouter

        router = FileRouter()
        ext_map = router.get_extension_map()
        ext_map[".pdf"] = None  # type: ignore[assignment]
        assert router.route(Path("file.pdf")) is not None  # map mutation does not affect router

    def test_get_extension_map_contains_pdf(self) -> None:
        from file_organizer.pipeline.router import FileRouter

        router = FileRouter()
        ext_map = router.get_extension_map()
        assert ".pdf" in ext_map

    def test_custom_rule_count_initially_zero(self) -> None:
        from file_organizer.pipeline.router import FileRouter

        router = FileRouter()
        assert router.custom_rule_count == 0


class TestFileRouterCustomRules:
    def test_custom_rule_overrides_extension(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        router.add_custom_rule(lambda p: p.name.startswith("vid_"), ProcessorType.VIDEO)
        assert router.route(Path("vid_file.pdf")) == ProcessorType.VIDEO

    def test_custom_rule_first_match_wins(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        router.add_custom_rule(lambda p: True, ProcessorType.AUDIO)
        router.add_custom_rule(lambda p: True, ProcessorType.VIDEO)
        assert router.route(Path("any.txt")) == ProcessorType.AUDIO

    def test_custom_rule_exception_falls_through(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()

        def bad_predicate(p: Path) -> bool:
            raise RuntimeError("oops")

        router.add_custom_rule(bad_predicate, ProcessorType.VIDEO)
        # Should fall through to extension-based routing
        assert router.route(Path("doc.pdf")) == ProcessorType.TEXT

    def test_clear_custom_rules(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        router.add_custom_rule(lambda p: True, ProcessorType.AUDIO)
        router.clear_custom_rules()
        assert router.custom_rule_count == 0
        assert router.route(Path("doc.pdf")) == ProcessorType.TEXT

    def test_custom_rule_count_increments(self) -> None:
        from file_organizer.pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        router.add_custom_rule(lambda p: False, ProcessorType.AUDIO)
        router.add_custom_rule(lambda p: False, ProcessorType.VIDEO)
        assert router.custom_rule_count == 2


# ---------------------------------------------------------------------------
# ProcessorPool
# ---------------------------------------------------------------------------


class _FakeProcessor:
    """Concrete test double for the BaseProcessor protocol."""

    def __init__(self, result: Any = None, raise_on_init: bool = False) -> None:
        self._result = result
        self._raise_on_init = raise_on_init
        self._initialized = False
        self._cleaned_up = False

    def initialize(self) -> None:
        if self._raise_on_init:
            raise RuntimeError("init failure")
        self._initialized = True

    def process_file(self, file_path: Path) -> Any:
        return self._result

    def cleanup(self) -> None:
        self._cleaned_up = True


class TestProcessorPool:
    def test_empty_pool_get_returns_none(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        assert pool.get_processor(ProcessorType.TEXT) is None

    def test_register_and_get_processor(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        proc = _FakeProcessor(result={"category": "docs", "filename": "test"})
        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: proc)

        retrieved = pool.get_processor(ProcessorType.TEXT)
        assert retrieved is proc
        assert proc._initialized is True

    def test_get_processor_returns_cached_instance(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        pool.register_factory(ProcessorType.IMAGE, lambda: _FakeProcessor())

        first = pool.get_processor(ProcessorType.IMAGE)
        second = pool.get_processor(ProcessorType.IMAGE)
        assert first is second

    def test_factory_exception_returns_none(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: _FakeProcessor(raise_on_init=True))

        result = pool.get_processor(ProcessorType.TEXT)
        assert result is None

    def test_has_processor_true_after_register(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        pool.register_factory(ProcessorType.VIDEO, lambda: _FakeProcessor())
        assert pool.has_processor(ProcessorType.VIDEO) is True

    def test_has_processor_false_without_factory(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        assert pool.has_processor(ProcessorType.AUDIO) is False

    def test_is_initialized_false_before_get(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: _FakeProcessor())
        assert pool.is_initialized(ProcessorType.TEXT) is False

    def test_is_initialized_true_after_get(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: _FakeProcessor())
        pool.get_processor(ProcessorType.TEXT)
        assert pool.is_initialized(ProcessorType.TEXT) is True

    def test_active_count_zero_initially(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool

        pool = ProcessorPool()
        assert pool.active_count == 0

    def test_active_count_increments_on_get(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: _FakeProcessor())
        pool.get_processor(ProcessorType.TEXT)
        assert pool.active_count == 1

    def test_registered_types_lists_factories(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: _FakeProcessor())
        pool.register_factory(ProcessorType.IMAGE, lambda: _FakeProcessor())
        types = pool.registered_types
        assert ProcessorType.TEXT in types
        assert ProcessorType.IMAGE in types

    def test_cleanup_calls_processor_cleanup(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        proc = _FakeProcessor()
        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: proc)
        pool.get_processor(ProcessorType.TEXT)

        pool.cleanup()
        assert proc._cleaned_up is True
        assert pool.active_count == 0

    def test_cleanup_tolerates_processor_exception(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        class BrokenCleanupProcessor(_FakeProcessor):
            def cleanup(self) -> None:
                raise OSError("cleanup exploded")

        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: BrokenCleanupProcessor())
        pool.get_processor(ProcessorType.TEXT)

        # Should not propagate the exception
        pool.cleanup()
        assert pool.active_count == 0

    def test_cleanup_clears_all_processors(self) -> None:
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import ProcessorType

        pool = ProcessorPool()
        for pt in (ProcessorType.TEXT, ProcessorType.IMAGE):
            pool.register_factory(pt, lambda: _FakeProcessor())
            pool.get_processor(pt)

        pool.cleanup()
        assert pool.active_count == 0


class TestNormalizeProcessorResult:
    def test_folder_name_used_as_category(self) -> None:
        from file_organizer.pipeline.processor_pool import normalize_processor_result

        result = MagicMock()
        result.folder_name = "Finance"
        result.filename = "invoice_2025"
        result.error = None

        normalized = normalize_processor_result(Path("invoice.pdf"), result)
        assert normalized["category"] == "Finance"
        assert normalized["filename"] == "invoice_2025"

    def test_missing_folder_name_uses_uncategorized(self) -> None:
        from file_organizer.pipeline.processor_pool import normalize_processor_result

        result = MagicMock(spec=[])
        normalized = normalize_processor_result(Path("file.txt"), result)
        assert normalized["category"] == "uncategorized"

    def test_missing_filename_uses_stem(self) -> None:
        from file_organizer.pipeline.processor_pool import normalize_processor_result

        result = MagicMock(spec=[])
        normalized = normalize_processor_result(Path("my_document.pdf"), result)
        assert normalized["filename"] == "my_document"

    def test_error_attribute_raises_runtime_error(self) -> None:
        from file_organizer.pipeline.processor_pool import normalize_processor_result

        result = MagicMock()
        result.folder_name = "Docs"
        result.filename = "test"
        result.error = "something went wrong"

        with pytest.raises(RuntimeError, match="Processor reported error"):
            normalize_processor_result(Path("file.pdf"), result)

    def test_empty_error_does_not_raise(self) -> None:
        from file_organizer.pipeline.processor_pool import normalize_processor_result

        result = MagicMock()
        result.folder_name = "Docs"
        result.filename = "myfile"
        result.error = ""

        normalized = normalize_processor_result(Path("file.pdf"), result)
        assert normalized["category"] == "Docs"

    def test_empty_folder_name_uses_uncategorized(self) -> None:
        from file_organizer.pipeline.processor_pool import normalize_processor_result

        result = MagicMock()
        result.folder_name = ""
        result.filename = "fname"
        result.error = None

        normalized = normalize_processor_result(Path("file.pdf"), result)
        assert normalized["category"] == "uncategorized"


# ---------------------------------------------------------------------------
# AnalyzerStage
# ---------------------------------------------------------------------------


class TestAnalyzerStageInit:
    def test_name_is_analyzer(self) -> None:
        from file_organizer.pipeline.stages.analyzer import AnalyzerStage

        stage = AnalyzerStage()
        assert stage.name == "analyzer"

    def test_no_router_no_pool_is_noop(self, tmp_path: Path) -> None:
        from file_organizer.interfaces.pipeline import StageContext
        from file_organizer.pipeline.stages.analyzer import AnalyzerStage

        stage = AnalyzerStage()
        f = tmp_path / "file.txt"
        f.write_text("hello")
        ctx = StageContext(file_path=f)
        result = stage.process(ctx)
        assert not result.failed
        assert result.analysis == {}

    def test_already_failed_context_is_returned_unchanged(self, tmp_path: Path) -> None:
        from file_organizer.interfaces.pipeline import StageContext
        from file_organizer.pipeline.stages.analyzer import AnalyzerStage

        stage = AnalyzerStage()
        ctx = StageContext(file_path=tmp_path / "f.txt", error="prior failure")
        result = stage.process(ctx)
        assert result.error == "prior failure"


class TestAnalyzerStageWithRouter:
    def test_unknown_processor_type_sets_error(self, tmp_path: Path) -> None:
        from file_organizer.interfaces.pipeline import StageContext
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import FileRouter
        from file_organizer.pipeline.stages.analyzer import AnalyzerStage

        router = FileRouter()
        pool = ProcessorPool()

        f = tmp_path / "file.xyz_unknown"
        f.write_text("data")
        ctx = StageContext(file_path=f)

        stage = AnalyzerStage(router=router, processor_pool=pool)
        result = stage.process(ctx)
        assert result.failed
        assert "No processor available" in result.error  # type: ignore[operator]

    def test_no_initialized_processor_sets_error(self, tmp_path: Path) -> None:
        from file_organizer.interfaces.pipeline import StageContext
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import FileRouter
        from file_organizer.pipeline.stages.analyzer import AnalyzerStage

        router = FileRouter()
        pool = ProcessorPool()
        # No factory registered for TEXT, so get_processor returns None

        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF content")
        ctx = StageContext(file_path=f)

        stage = AnalyzerStage(router=router, processor_pool=pool)
        result = stage.process(ctx)
        assert result.failed
        assert "processor" in result.error.lower()  # type: ignore[operator]

    def test_successful_analysis_populates_context(self, tmp_path: Path) -> None:
        from file_organizer.interfaces.pipeline import StageContext
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import FileRouter, ProcessorType
        from file_organizer.pipeline.stages.analyzer import AnalyzerStage

        class _ResultStub:
            folder_name = "Finance"
            filename = "invoice_2025"
            error = None

        proc = _FakeProcessor(result=_ResultStub())
        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: proc)

        router = FileRouter()
        f = tmp_path / "invoice.pdf"
        f.write_bytes(b"data")
        ctx = StageContext(file_path=f)

        stage = AnalyzerStage(router=router, processor_pool=pool)
        result = stage.process(ctx)

        assert not result.failed
        assert result.category == "Finance"
        assert result.filename == "invoice_2025"
        assert result.analysis["category"] == "Finance"

    def test_processor_exception_sets_error(self, tmp_path: Path) -> None:
        from file_organizer.interfaces.pipeline import StageContext
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import FileRouter, ProcessorType
        from file_organizer.pipeline.stages.analyzer import AnalyzerStage

        class _ExplodingProcessor(_FakeProcessor):
            def process_file(self, file_path: Path) -> Any:
                raise RuntimeError("model unavailable")

        proc = _ExplodingProcessor()
        pool = ProcessorPool()
        pool.register_factory(ProcessorType.TEXT, lambda: proc)

        router = FileRouter()
        f = tmp_path / "doc.txt"
        f.write_text("content")
        ctx = StageContext(file_path=f)

        stage = AnalyzerStage(router=router, processor_pool=pool)
        result = stage.process(ctx)

        assert result.failed
        assert "model unavailable" in result.error  # type: ignore[operator]

    def test_analyzer_records_processor_type_in_extra(self, tmp_path: Path) -> None:
        from file_organizer.interfaces.pipeline import StageContext
        from file_organizer.pipeline.processor_pool import ProcessorPool
        from file_organizer.pipeline.router import FileRouter, ProcessorType
        from file_organizer.pipeline.stages.analyzer import AnalyzerStage

        class _ResultStub:
            folder_name = "Images"
            filename = "photo_01"
            error = None

        pool = ProcessorPool()
        pool.register_factory(ProcessorType.IMAGE, lambda: _FakeProcessor(result=_ResultStub()))

        router = FileRouter()
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"\xff\xd8\xff")  # minimal JPEG header bytes
        ctx = StageContext(file_path=f)

        stage = AnalyzerStage(router=router, processor_pool=pool)
        result = stage.process(ctx)

        assert not result.failed
        assert result.extra.get("analyzer.processor_type") == ProcessorType.IMAGE
