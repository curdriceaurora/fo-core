"""Tests for composable pipeline stages.

Validates that each stage implements PipelineStage protocol, processes
files correctly, handles errors gracefully, and can be composed into
custom pipelines.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from interfaces.pipeline import PipelineStage, StageContext
from pipeline.stages.analyzer import AnalyzerStage
from pipeline.stages.postprocessor import PostprocessorStage
from pipeline.stages.preprocessor import PreprocessorStage
from pipeline.stages.writer import WriterStage

# ---------------------------------------------------------------------------
# StageContext
# ---------------------------------------------------------------------------


@pytest.mark.ci
@pytest.mark.unit
class TestStageContext:
    """Test StageContext dataclass behavior."""

    def test_failed_property_false_by_default(self) -> None:
        ctx = StageContext(file_path=Path("test.txt"))
        assert ctx.failed is False

    def test_failed_property_true_when_error_set(self) -> None:
        ctx = StageContext(file_path=Path("test.txt"), error="boom")
        assert ctx.failed is True

    def test_defaults(self) -> None:
        ctx = StageContext(file_path=Path("test.txt"))
        assert ctx.metadata == {}
        assert ctx.analysis == {}
        assert ctx.destination is None
        assert ctx.category == ""
        assert ctx.filename == ""
        assert ctx.dry_run is True
        assert ctx.extra == {}

    def test_rejects_path_traversal_in_category(self) -> None:
        with pytest.raises(ValueError, match="Invalid category"):
            StageContext(file_path=Path("input/file.txt"), category="../etc")

    def test_rejects_path_traversal_in_filename(self) -> None:
        with pytest.raises(ValueError, match="Invalid filename"):
            StageContext(file_path=Path("input/file.txt"), filename="../../etc/passwd")

    def test_rejects_windows_drive_in_category(self) -> None:
        # Regression for #760: "C:" has no slash but still escapes output_dir / category
        # on Windows via PureWindowsPath.drive being non-empty.
        with pytest.raises(ValueError, match="Invalid category"):
            StageContext(file_path=Path("input/file.txt"), category="C:")

    def test_rejects_windows_drive_with_path_in_category(self) -> None:
        # Drive-qualified path without a separator also escapes containment.
        with pytest.raises(ValueError, match="Invalid category"):
            StageContext(file_path=Path("input/file.txt"), category="C:docs")

    def test_rejects_windows_drive_in_filename(self) -> None:
        with pytest.raises(ValueError, match="Invalid filename"):
            StageContext(file_path=Path("input/file.txt"), filename="C:")

    def test_rejects_windows_drive_in_filename_via_setattr(self) -> None:
        ctx = StageContext(file_path=Path("input/file.txt"))
        with pytest.raises(ValueError, match="Invalid filename"):
            ctx.filename = "C:evil"

    def test_accepts_normal_category(self) -> None:
        ctx = StageContext(file_path=Path("input/file.txt"), category="Documents")
        assert ctx.category == "Documents"

    def test_accepts_normal_filename(self) -> None:
        ctx = StageContext(file_path=Path("input/file.txt"), filename="report_2026")
        assert ctx.filename == "report_2026"


# ---------------------------------------------------------------------------
# PreprocessorStage
# ---------------------------------------------------------------------------


@pytest.mark.ci
@pytest.mark.unit
class TestPreprocessorStage:
    """Test PreprocessorStage validation and metadata extraction."""

    def test_satisfies_protocol(self) -> None:
        assert isinstance(PreprocessorStage(), PipelineStage)

    def test_name(self) -> None:
        assert PreprocessorStage().name == "preprocessor"

    def test_extracts_metadata(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("hello world")

        ctx = StageContext(file_path=f)
        result = PreprocessorStage().process(ctx)

        assert result.error is None
        assert result.metadata["extension"] == ".txt"
        assert result.metadata["size_bytes"] == 11
        assert result.metadata["stem"] == "hello"
        assert result.filename == "hello"

    def test_file_not_found(self) -> None:
        ctx = StageContext(file_path=Path("nonexistent/file.txt"))
        result = PreprocessorStage().process(ctx)
        assert result.failed
        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_directory_rejected(self, tmp_path: Path) -> None:
        ctx = StageContext(file_path=tmp_path)
        result = PreprocessorStage().process(ctx)
        assert result.failed
        assert result.error is not None
        assert "Not a file" in result.error

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "test.xyz"
        f.write_text("data")

        stage = PreprocessorStage(supported_extensions=frozenset({".txt"}))
        ctx = StageContext(file_path=f)
        result = stage.process(ctx)
        assert result.failed
        assert result.error is not None
        assert "Unsupported" in result.error

    def test_no_extension_filter(self, tmp_path: Path) -> None:
        """When supported_extensions is None, all extensions pass."""
        f = tmp_path / "test.xyz"
        f.write_text("data")

        ctx = StageContext(file_path=f)
        result = PreprocessorStage().process(ctx)
        assert not result.failed

    def test_skips_when_already_failed(self) -> None:
        ctx = StageContext(file_path=Path("x.txt"), error="prior error")
        result = PreprocessorStage().process(ctx)
        assert result.error == "prior error"


# ---------------------------------------------------------------------------
# AnalyzerStage
# ---------------------------------------------------------------------------


@pytest.mark.ci
@pytest.mark.unit
class TestAnalyzerStage:
    """Test AnalyzerStage routing and processor invocation."""

    def test_satisfies_protocol(self) -> None:
        assert isinstance(AnalyzerStage(), PipelineStage)

    def test_name(self) -> None:
        assert AnalyzerStage().name == "analyzer"

    def test_skips_when_no_router(self, tmp_path: Path) -> None:
        """Without a router, analyzer is a no-op."""
        f = tmp_path / "test.txt"
        f.write_text("content")
        ctx = StageContext(file_path=f)
        result = AnalyzerStage().process(ctx)
        assert not result.failed

    def test_skips_when_context_already_failed(self, tmp_path: Path) -> None:
        ctx = StageContext(file_path=tmp_path / "x.txt", error="prior error")
        result = AnalyzerStage().process(ctx)
        assert result.error == "prior error"

    def test_processes_file_with_router_and_pool(self, tmp_path: Path) -> None:
        """When router and pool are configured, analyzer invokes processor."""
        from pipeline.processor_pool import ProcessorPool
        from pipeline.router import FileRouter

        f = tmp_path / "doc.txt"
        f.write_text("hello")

        mock_result = MagicMock()
        mock_result.folder_name = "Documents"
        mock_result.filename = "hello_doc"
        mock_result.error = None

        mock_processor = MagicMock()
        mock_processor.process_file.return_value = mock_result

        router = FileRouter()
        pool = MagicMock(spec=ProcessorPool)
        pool.get_processor.return_value = mock_processor

        stage = AnalyzerStage(router=router, processor_pool=pool)
        ctx = StageContext(file_path=f)
        result = stage.process(ctx)

        assert not result.failed
        assert result.category == "Documents"
        assert result.filename == "hello_doc"
        mock_processor.process_file.assert_called_once_with(f)
        pool.get_processor.assert_called_once()


# ---------------------------------------------------------------------------
# PostprocessorStage
# ---------------------------------------------------------------------------


@pytest.mark.ci
@pytest.mark.unit
class TestPostprocessorStage:
    """Test PostprocessorStage path computation."""

    def test_satisfies_protocol(self) -> None:
        assert isinstance(PostprocessorStage(output_directory=Path("out")), PipelineStage)

    def test_name(self) -> None:
        assert PostprocessorStage(output_directory=Path("out")).name == "postprocessor"

    def test_builds_destination(self, tmp_path: Path) -> None:
        stage = PostprocessorStage(output_directory=tmp_path / "out")
        ctx = StageContext(
            file_path=Path("input/report.pdf"),
            category="Documents",
            filename="quarterly_report",
        )
        result = stage.process(ctx)

        assert result.destination == tmp_path / "out" / "Documents" / "quarterly_report.pdf"

    def test_defaults_to_uncategorized(self, tmp_path: Path) -> None:
        stage = PostprocessorStage(output_directory=tmp_path / "out")
        ctx = StageContext(file_path=Path("input/file.txt"))
        result = stage.process(ctx)

        assert "uncategorized" in str(result.destination)

    def test_deduplicates_existing_files(self, tmp_path: Path) -> None:
        out = tmp_path / "out" / "Docs"
        out.mkdir(parents=True)
        (out / "file.txt").write_text("existing")

        stage = PostprocessorStage(output_directory=tmp_path / "out")
        ctx = StageContext(
            file_path=Path("input/file.txt"),
            category="Docs",
            filename="file",
        )
        result = stage.process(ctx)
        assert result.destination == out / "file_1.txt"

    def test_skips_when_failed(self) -> None:
        stage = PostprocessorStage(output_directory=Path("out"))
        ctx = StageContext(file_path=Path("x.txt"), error="prior")
        result = stage.process(ctx)
        assert result.destination is None


# ---------------------------------------------------------------------------
# WriterStage
# ---------------------------------------------------------------------------


@pytest.mark.ci
@pytest.mark.unit
class TestWriterStage:
    """Test WriterStage file copy operations."""

    def test_satisfies_protocol(self) -> None:
        assert isinstance(WriterStage(), PipelineStage)

    def test_name(self) -> None:
        assert WriterStage().name == "writer"

    def test_copies_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "file.txt"
        src.parent.mkdir()
        src.write_text("content")

        dest = tmp_path / "dest" / "Docs" / "file.txt"
        ctx = StageContext(
            file_path=src,
            destination=dest,
            dry_run=False,
        )
        result = WriterStage().process(ctx)
        assert not result.failed
        assert dest.exists()
        assert dest.read_text() == "content"

    def test_dry_run_no_copy(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        src.write_text("content")
        dest = tmp_path / "out" / "file.txt"

        ctx = StageContext(file_path=src, destination=dest, dry_run=True)
        result = WriterStage().process(ctx)
        assert not result.failed
        assert not dest.exists()

    def test_error_when_no_destination(self) -> None:
        ctx = StageContext(file_path=Path("x.txt"), dry_run=False)
        result = WriterStage().process(ctx)
        assert result.failed
        assert result.error is not None
        assert "destination" in result.error.lower()

    def test_skips_when_already_failed(self) -> None:
        ctx = StageContext(
            file_path=Path("x.txt"),
            destination=Path("out/x.txt"),
            error="prior",
            dry_run=False,
        )
        result = WriterStage().process(ctx)
        assert result.error == "prior"


# ---------------------------------------------------------------------------
# Composition tests
# ---------------------------------------------------------------------------


@pytest.mark.ci
@pytest.mark.unit
class TestPipelineComposition:
    """Test composing stages into custom pipelines."""

    def test_preprocessor_plus_writer_skipping_analyzer(self, tmp_path: Path) -> None:
        """Custom pipeline with only preprocessor + postprocessor + writer."""
        src = tmp_path / "input" / "file.txt"
        src.parent.mkdir()
        src.write_text("hello")

        out = tmp_path / "output"
        stages: list[PipelineStage] = [
            PreprocessorStage(),
            PostprocessorStage(output_directory=out),
            WriterStage(),
        ]

        ctx = StageContext(file_path=src, dry_run=False, category="Quick")
        for stage in stages:
            ctx = stage.process(ctx)

        assert not ctx.failed
        assert ctx.destination is not None
        assert ctx.destination.exists()
        assert ctx.destination.read_text() == "hello"

    def test_custom_stage_without_orchestrator_changes(self, tmp_path: Path) -> None:
        """Adding a custom stage doesn't require orchestrator changes."""

        class UppercaseStage:
            @property
            def name(self) -> str:
                return "uppercase"

            def process(self, context: StageContext) -> StageContext:
                context.filename = context.filename.upper()
                return context

        assert isinstance(UppercaseStage(), PipelineStage)

        src = tmp_path / "report.txt"
        src.write_text("data")

        stages: list[PipelineStage] = [
            PreprocessorStage(),
            UppercaseStage(),
            PostprocessorStage(output_directory=tmp_path / "out"),
        ]

        ctx = StageContext(file_path=src, category="Docs")
        for stage in stages:
            ctx = stage.process(ctx)

        assert ctx.filename == "REPORT"
        assert "REPORT" in str(ctx.destination)

    def test_orchestrator_with_stages(self, tmp_path: Path) -> None:
        """PipelineOrchestrator delegates to stages when configured."""
        from pipeline.config import PipelineConfig
        from pipeline.orchestrator import PipelineOrchestrator

        src = tmp_path / "file.txt"
        src.write_text("hello")

        config = PipelineConfig(
            output_directory=tmp_path / "out",
            dry_run=True,
        )

        pipeline = PipelineOrchestrator(
            config,
            stages=[
                PreprocessorStage(),
                PostprocessorStage(output_directory=config.output_directory),
                WriterStage(),
            ],
        )

        result = pipeline.process_file(src)
        assert result.success
        assert result.destination is not None
        assert result.dry_run is True

    def test_error_propagation_through_stages(self) -> None:
        """Error in early stage propagates; later stages skip."""
        stages: list[PipelineStage] = [
            PreprocessorStage(),
            AnalyzerStage(),
            PostprocessorStage(output_directory=Path("out")),
            WriterStage(),
        ]

        ctx = StageContext(file_path=Path("nonexistent.txt"))
        for stage in stages:
            ctx = stage.process(ctx)

        assert ctx.failed
        assert ctx.destination is None  # postprocessor skipped


# ---------------------------------------------------------------------------
# SafeDir branch coverage (PR6 / #270)
# ---------------------------------------------------------------------------


@pytest.mark.ci
@pytest.mark.unit
class TestPostprocessorSafeDirBranches:
    """Cover PR6 SafeDir branches in PostprocessorStage."""

    def test_close_releases_cached_category_dirs(self, tmp_path: Path) -> None:
        """close() iterates _cat_subdirs and calls __exit__ on each fd."""
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        out = tmp_path / "out"
        out.mkdir()
        src = tmp_path / "doc.txt"
        src.write_bytes(b"hi")

        stage = PostprocessorStage(output_directory=out)
        ctx = StageContext(file_path=src, dry_run=False, category="docs", filename="doc")
        stage.process(ctx)
        assert "docs" in stage._cat_subdirs
        stage.close()
        assert stage._cat_subdirs == {}
        assert stage._root_sd is None

    def test_category_dir_already_exists_is_ok(self, tmp_path: Path) -> None:
        """FileExistsError from mkdir is swallowed; existing real dir is opened."""
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        out = tmp_path / "out"
        out.mkdir()
        (out / "docs").mkdir()
        src = tmp_path / "doc.txt"
        src.write_bytes(b"hi")

        stage = PostprocessorStage(output_directory=out)
        try:
            ctx = StageContext(file_path=src, dry_run=False, category="docs", filename="doc")
            result = stage.process(ctx)
        finally:
            stage.close()

        assert not result.failed

    def test_category_safedir_cached_on_second_call(self, tmp_path: Path) -> None:
        """Second process() for the same category hits the _cat_subdirs cache."""
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        out = tmp_path / "out"
        out.mkdir()
        src1 = tmp_path / "a.txt"
        src2 = tmp_path / "b.txt"
        src1.write_bytes(b"a")
        src2.write_bytes(b"b")

        stage = PostprocessorStage(output_directory=out)
        try:
            stage.process(
                StageContext(file_path=src1, dry_run=False, category="docs", filename="a")
            )
            assert "docs" in stage._cat_subdirs
            result2 = stage.process(
                StageContext(file_path=src2, dry_run=False, category="docs", filename="b")
            )
        finally:
            stage.close()

        assert not result2.failed

    def test_safedir_generic_exception_falls_back_to_plain_mkdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-SymlinkRejected exception in _get_category_safedir → plain mkdir fallback."""
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        out = tmp_path / "out"
        out.mkdir()
        src = tmp_path / "doc.txt"
        src.write_bytes(b"hi")

        stage = PostprocessorStage(output_directory=out)

        def _raise(*_a: object, **_kw: object) -> None:
            raise OSError("simulated non-symlink failure")

        monkeypatch.setattr(stage, "_get_category_safedir", _raise)
        try:
            ctx = StageContext(file_path=src, dry_run=False, category="docs", filename="doc")
            result = stage.process(ctx)
        finally:
            stage.close()

        assert not result.failed
        # Postprocessor sets destination but does not write the file (WriterStage does).
        assert result.destination is not None
        assert result.destination.parent.is_dir()

    def test_no_safedir_plain_mkdir(self, tmp_path: Path) -> None:
        """When _root_sd is None (SafeDir unavailable) stage uses plain mkdir."""
        out = tmp_path / "out"
        src = tmp_path / "doc.txt"
        src.write_bytes(b"hi")

        stage = PostprocessorStage(output_directory=out)
        stage._root_sd = None
        stage._cat_subdirs.clear()
        try:
            ctx = StageContext(file_path=src, dry_run=False, category="docs", filename="doc")
            result = stage.process(ctx)
        finally:
            stage.close()

        assert not result.failed
        assert (out / "docs").is_dir()


@pytest.mark.ci
@pytest.mark.unit
class TestWriterSafeDirBranches:
    """Cover PR6 SafeDir error paths in WriterStage."""

    def test_copystat_oserror_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError from shutil.copystat is swallowed; write still succeeds."""
        import shutil
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir path is POSIX-only")

        from utils.safedir import SafeDir

        out = tmp_path / "out" / "docs"
        out.mkdir(parents=True)
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")

        monkeypatch.setattr(shutil, "copystat", MagicMock(side_effect=OSError("perm")))

        with SafeDir.open_root(out) as sd:
            ctx = StageContext(
                file_path=src,
                destination=out / "src.txt",
                dest_safedir=sd,
                dry_run=False,
            )
            result = WriterStage().process(ctx)

        assert not result.failed
        assert (out / "src.txt").read_bytes() == b"data"

    def test_symlink_rejected_logs_security_event_and_sets_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SymlinkRejected from open_child is logged as security_event and sets error."""
        import logging
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir path is POSIX-only")

        from utils.safedir import SafeDir

        out = tmp_path / "out" / "docs"
        out.mkdir(parents=True)
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")

        # Create a symlink at the destination name.
        victim = tmp_path / "victim.txt"
        victim.write_bytes(b"sensitive")
        dst_path = out / "src.txt"
        try:
            dst_path.symlink_to(victim)
        except OSError:
            pytest.skip("symlink creation not supported")

        with SafeDir.open_root(out) as sd:
            ctx = StageContext(
                file_path=src,
                destination=dst_path,
                dest_safedir=sd,
                dry_run=False,
            )
            with caplog.at_level(logging.ERROR, logger="pipeline.stages.writer"):
                result = WriterStage().process(ctx)

        assert result.failed
        assert "symlink" in (result.error or "").lower() or result.error is not None
        assert any("security_event" in r.message for r in caplog.records)
        assert victim.read_bytes() == b"sensitive"

    def test_generic_oserror_sets_error(self, tmp_path: Path) -> None:
        """A generic OSError (no dest_safedir) logs exception and sets error."""
        # Source does not exist → shutil.copy2 raises FileNotFoundError (OSError).
        src = tmp_path / "nonexistent.txt"
        dest = tmp_path / "out" / "file.txt"
        dest.parent.mkdir(parents=True)

        ctx = StageContext(file_path=src, destination=dest, dry_run=False)
        result = WriterStage().process(ctx)

        assert result.failed
        assert result.error is not None
