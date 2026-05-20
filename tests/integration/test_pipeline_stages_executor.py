"""Integration tests for pipeline stages, parallel executor, and atomic I/O.

Covers:
  - pipeline/stages/preprocessor.py  — PreprocessorStage
  - pipeline/stages/postprocessor.py — PostprocessorStage
  - pipeline/stages/writer.py        — WriterStage
  - parallel/executor.py             — create_executor
  - utils/atomic_io.py               — fsync_directory
  - interfaces/pipeline.py           — StageContext
"""

from __future__ import annotations

from pathlib import Path

import pytest

from interfaces.pipeline import StageContext
from parallel.executor import create_executor
from pipeline.stages.postprocessor import PostprocessorStage
from pipeline.stages.preprocessor import PreprocessorStage
from pipeline.stages.writer import WriterStage
from utils.atomic_io import fsync_directory

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# StageContext
# ---------------------------------------------------------------------------


class TestStageContext:
    def test_created(self, tmp_path: Path) -> None:
        ctx = StageContext(file_path=tmp_path / "f.txt")
        assert ctx is not None

    def test_failed_false_initially(self, tmp_path: Path) -> None:
        ctx = StageContext(file_path=tmp_path / "f.txt")
        assert ctx.failed is False

    def test_failed_true_after_error(self, tmp_path: Path) -> None:
        ctx = StageContext(file_path=tmp_path / "f.txt")
        ctx.error = "boom"
        assert ctx.failed is True

    def test_dry_run_default(self, tmp_path: Path) -> None:
        ctx = StageContext(file_path=tmp_path / "f.txt")
        assert ctx.dry_run is True

    def test_category_traversal_rejected(self, tmp_path: Path) -> None:
        ctx = StageContext(file_path=tmp_path / "f.txt")
        with pytest.raises(ValueError):
            ctx.category = "../evil"

    def test_filename_slash_rejected(self, tmp_path: Path) -> None:
        ctx = StageContext(file_path=tmp_path / "f.txt")
        with pytest.raises(ValueError):
            ctx.filename = "a/b"

    def test_valid_category_accepted(self, tmp_path: Path) -> None:
        ctx = StageContext(file_path=tmp_path / "f.txt")
        ctx.category = "documents"
        assert ctx.category == "documents"


# ---------------------------------------------------------------------------
# PreprocessorStage
# ---------------------------------------------------------------------------


class TestPreprocessorStageInit:
    def test_created(self) -> None:
        stage = PreprocessorStage()
        assert stage is not None

    def test_name(self) -> None:
        assert PreprocessorStage().name == "preprocessor"

    def test_with_supported_extensions(self) -> None:
        stage = PreprocessorStage(supported_extensions=frozenset({".pdf", ".txt"}))
        assert stage is not None


class TestPreprocessorStageProcess:
    def test_missing_file_sets_error(self, tmp_path: Path) -> None:
        stage = PreprocessorStage()
        ctx = StageContext(file_path=tmp_path / "missing.txt")
        result = stage.process(ctx)
        assert result.failed
        assert "not found" in result.error.lower()

    def test_valid_file_sets_metadata(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("hello world")
        stage = PreprocessorStage()
        ctx = StageContext(file_path=f)
        result = stage.process(ctx)
        assert not result.failed
        assert "size_bytes" in result.metadata
        assert "extension" in result.metadata

    def test_extension_lowercased(self, tmp_path: Path) -> None:
        f = tmp_path / "DOC.TXT"
        f.write_text("content")
        stage = PreprocessorStage()
        ctx = StageContext(file_path=f)
        result = stage.process(ctx)
        assert result.metadata["extension"] == ".txt"

    def test_unsupported_extension_sets_error(self, tmp_path: Path) -> None:
        f = tmp_path / "file.xyz"
        f.write_text("content")
        stage = PreprocessorStage(supported_extensions=frozenset({".pdf"}))
        ctx = StageContext(file_path=f)
        result = stage.process(ctx)
        assert result.failed
        assert "extension" in result.error.lower()

    def test_supported_extension_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "file.pdf"
        f.write_bytes(b"%PDF content")
        stage = PreprocessorStage(supported_extensions=frozenset({".pdf"}))
        ctx = StageContext(file_path=f)
        result = stage.process(ctx)
        assert not result.failed

    def test_skips_already_failed(self, tmp_path: Path) -> None:
        stage = PreprocessorStage()
        ctx = StageContext(file_path=tmp_path / "any.txt", error="prior failure")
        result = stage.process(ctx)
        assert result.error == "prior failure"

    def test_stem_set_as_filename(self, tmp_path: Path) -> None:
        f = tmp_path / "my_document.txt"
        f.write_text("content")
        stage = PreprocessorStage()
        ctx = StageContext(file_path=f)
        result = stage.process(ctx)
        assert result.filename == "my_document"

    def test_mime_type_in_metadata(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("content")
        stage = PreprocessorStage()
        ctx = StageContext(file_path=f)
        result = stage.process(ctx)
        assert "mime_type" in result.metadata


# ---------------------------------------------------------------------------
# PostprocessorStage
# ---------------------------------------------------------------------------


class TestPostprocessorStageInit:
    def test_created(self, tmp_path: Path) -> None:
        stage = PostprocessorStage(output_directory=tmp_path)
        assert stage is not None

    def test_name(self, tmp_path: Path) -> None:
        assert PostprocessorStage(output_directory=tmp_path).name == "postprocessor"


class TestPostprocessorStageProcess:
    def test_sets_destination(self, tmp_path: Path) -> None:
        stage = PostprocessorStage(output_directory=tmp_path / "out")
        src = tmp_path / "report.txt"
        src.write_text("content")
        ctx = StageContext(file_path=src, category="finance", filename="report")
        result = stage.process(ctx)
        assert result.destination is not None

    def test_destination_under_output_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        stage = PostprocessorStage(output_directory=out)
        src = tmp_path / "report.txt"
        src.write_text("content")
        ctx = StageContext(file_path=src, category="finance", filename="report")
        result = stage.process(ctx)
        assert str(result.destination).startswith(str(out))

    def test_category_in_destination(self, tmp_path: Path) -> None:
        stage = PostprocessorStage(output_directory=tmp_path / "out")
        src = tmp_path / "report.pdf"
        src.write_bytes(b"content")
        ctx = StageContext(file_path=src, category="invoices", filename="inv001")
        result = stage.process(ctx)
        assert "invoices" in str(result.destination)

    def test_deduplicates_existing_file(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        (out / "finance").mkdir()
        (out / "finance" / "report.txt").write_text("existing")
        stage = PostprocessorStage(output_directory=out)
        src = tmp_path / "report.txt"
        src.write_text("content")
        ctx = StageContext(file_path=src, category="finance", filename="report")
        result = stage.process(ctx)
        assert result.destination != out / "finance" / "report.txt"

    def test_skips_already_failed(self, tmp_path: Path) -> None:
        stage = PostprocessorStage(output_directory=tmp_path)
        src = tmp_path / "f.txt"
        ctx = StageContext(file_path=src, error="prior failure")
        result = stage.process(ctx)
        assert result.error == "prior failure"
        assert result.destination is None

    def test_fallback_category(self, tmp_path: Path) -> None:
        stage = PostprocessorStage(output_directory=tmp_path / "out")
        src = tmp_path / "file.txt"
        src.write_text("content")
        ctx = StageContext(file_path=src)
        result = stage.process(ctx)
        assert "uncategorized" in str(result.destination)


# ---------------------------------------------------------------------------
# WriterStage
# ---------------------------------------------------------------------------


class TestWriterStageInit:
    def test_created(self) -> None:
        assert WriterStage() is not None

    def test_name(self) -> None:
        assert WriterStage().name == "writer"


class TestWriterStageProcess:
    def test_dry_run_does_not_copy(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("content")
        dest = tmp_path / "out" / "dest.txt"
        stage = WriterStage()
        ctx = StageContext(file_path=src, destination=dest, dry_run=True)
        result = stage.process(ctx)
        assert not dest.exists()
        assert not result.failed

    def test_actual_copy_creates_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("content")
        dest = tmp_path / "out" / "dest.txt"
        stage = WriterStage()
        ctx = StageContext(file_path=src, destination=dest, dry_run=False)
        result = stage.process(ctx)
        assert dest.exists()
        assert not result.failed

    def test_copy_preserves_content(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("hello world")
        dest = tmp_path / "out" / "dest.txt"
        stage = WriterStage()
        ctx = StageContext(file_path=src, destination=dest, dry_run=False)
        stage.process(ctx)
        assert dest.read_text() == "hello world"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("content")
        dest = tmp_path / "deep" / "nested" / "dest.txt"
        stage = WriterStage()
        ctx = StageContext(file_path=src, destination=dest, dry_run=False)
        result = stage.process(ctx)
        assert dest.exists()
        assert not result.failed

    def test_missing_destination_sets_error(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("content")
        stage = WriterStage()
        ctx = StageContext(file_path=src, destination=None, dry_run=False)
        result = stage.process(ctx)
        assert result.failed

    def test_skips_already_failed(self, tmp_path: Path) -> None:
        stage = WriterStage()
        ctx = StageContext(file_path=tmp_path / "f.txt", error="prior failure")
        result = stage.process(ctx)
        assert result.error == "prior failure"


# ---------------------------------------------------------------------------
# create_executor
# ---------------------------------------------------------------------------


class TestCreateExecutor:
    def test_thread_executor_created(self) -> None:
        executor, executor_type = create_executor("thread", max_workers=2)
        try:
            assert executor is not None
            assert executor_type == "thread"
        finally:
            executor.shutdown(wait=False)

    def test_process_executor_created(self) -> None:
        executor, executor_type = create_executor("process", max_workers=2)
        try:
            assert executor is not None
            assert executor_type in ("process", "thread")
        finally:
            executor.shutdown(wait=False)

    def test_unknown_type_creates_thread_executor(self) -> None:
        executor, executor_type = create_executor("unknown", max_workers=1)
        try:
            assert executor_type == "thread"
        finally:
            executor.shutdown(wait=False)

    def test_thread_executor_runs_tasks(self) -> None:
        executor, _ = create_executor("thread", max_workers=2)
        try:
            future = executor.submit(lambda: 42)
            assert future.result() == 42
        finally:
            executor.shutdown(wait=True)

    def test_returns_tuple(self) -> None:
        result = create_executor("thread", max_workers=1)
        assert isinstance(result, tuple)
        assert len(result) == 2
        result[0].shutdown(wait=False)


# ---------------------------------------------------------------------------
# fsync_directory
# ---------------------------------------------------------------------------


class TestFsyncDirectory:
    def test_no_error_on_existing_dir(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("data")
        fsync_directory(f)

    def test_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("data")
        result = fsync_directory(f)
        assert result is None

    def test_nested_path(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        f = nested / "file.txt"
        f.write_text("content")
        fsync_directory(f)


# ---------------------------------------------------------------------------
# PR6 SafeDir paths — integration coverage
# ---------------------------------------------------------------------------


class TestWriterStageSafeDirIntegration:
    """Integration coverage for the SafeDir copy path in WriterStage (PR6 / #270)."""

    def test_safedir_copy_writes_content(self, tmp_path: Path) -> None:
        """WriterStage copies via SafeDir open_child when dest_safedir is set."""
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir

        out = tmp_path / "out" / "docs"
        out.mkdir(parents=True)
        src = tmp_path / "doc.txt"
        src.write_bytes(b"integration content")

        with SafeDir.open_root(out) as sd:
            ctx = StageContext(
                file_path=src,
                destination=out / "doc.txt",
                dest_safedir=sd,
                dry_run=False,
            )
            result = WriterStage().process(ctx)

        assert not result.failed
        assert (out / "doc.txt").read_bytes() == b"integration content"

    def test_safedir_copy_large_file(self, tmp_path: Path) -> None:
        """SafeDir copy handles multi-chunk files (triggers partial-write loop)."""
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir

        out = tmp_path / "out" / "docs"
        out.mkdir(parents=True)
        src = tmp_path / "big.bin"
        data = b"x" * (65536 * 3 + 100)
        src.write_bytes(data)

        with SafeDir.open_root(out) as sd:
            ctx = StageContext(
                file_path=src,
                destination=out / "big.bin",
                dest_safedir=sd,
                dry_run=False,
            )
            result = WriterStage().process(ctx)

        assert not result.failed
        assert (out / "big.bin").read_bytes() == data

    def test_safedir_symlink_rejected_sets_error(self, tmp_path: Path) -> None:
        """SymlinkRejected from open_child sets context.error (security_event path)."""
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir

        out = tmp_path / "out" / "docs"
        out.mkdir(parents=True)
        src = tmp_path / "doc.txt"
        src.write_bytes(b"data")

        victim = tmp_path / "victim.txt"
        victim.write_bytes(b"sensitive")
        dst = out / "doc.txt"
        try:
            dst.symlink_to(victim)
        except OSError:
            pytest.skip("symlink creation not supported")

        with SafeDir.open_root(out) as sd:
            ctx = StageContext(
                file_path=src,
                destination=dst,
                dest_safedir=sd,
                dry_run=False,
            )
            result = WriterStage().process(ctx)

        assert result.failed
        assert result.error is not None
        assert victim.read_bytes() == b"sensitive"

    def test_safedir_oserror_sets_error(self, tmp_path: Path) -> None:
        """Non-SymlinkRejected OSError in SafeDir copy sets context.error."""
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir

        out = tmp_path / "out" / "docs"
        out.mkdir(parents=True)
        src = tmp_path / "missing.txt"

        with SafeDir.open_root(out) as sd:
            ctx = StageContext(
                file_path=src,
                destination=out / "missing.txt",
                dest_safedir=sd,
                dry_run=False,
            )
            result = WriterStage().process(ctx)

        assert result.failed
        assert result.error is not None


class TestPostprocessorSafeDirIntegration:
    """Integration coverage for SafeDir paths in PostprocessorStage (PR6 / #270)."""

    def test_safedir_roundtrip_two_categories(self, tmp_path: Path) -> None:
        """Process two files in different categories; close() releases all fds."""
        import sys

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        out = tmp_path / "out"
        out.mkdir()
        src_a = tmp_path / "a.txt"
        src_b = tmp_path / "b.txt"
        src_a.write_bytes(b"a")
        src_b.write_bytes(b"b")

        stage = PostprocessorStage(output_directory=out)
        try:
            r_a = stage.process(
                StageContext(file_path=src_a, dry_run=False, category="docs", filename="a")
            )
            r_b = stage.process(
                StageContext(file_path=src_b, dry_run=False, category="images", filename="b")
            )
        finally:
            stage.close()

        assert not r_a.failed
        assert not r_b.failed
        assert stage._cat_subdirs == {}
        assert stage._root_sd is None

    def test_safedir_cache_hit_same_category(self, tmp_path: Path) -> None:
        """Two files in the same category hit the _cat_subdirs cache."""
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
            r2 = stage.process(
                StageContext(file_path=src2, dry_run=False, category="docs", filename="b")
            )
        finally:
            stage.close()

        assert not r2.failed

    def test_no_safedir_plain_mkdir_integration(self, tmp_path: Path) -> None:
        """_root_sd=None falls back to plain mkdir (exercises the else branch)."""
        out = tmp_path / "out"
        src = tmp_path / "doc.txt"
        src.write_bytes(b"hi")

        stage = PostprocessorStage(output_directory=out)
        stage._root_sd = None
        stage._cat_subdirs.clear()
        try:
            result = stage.process(
                StageContext(file_path=src, dry_run=False, category="docs", filename="doc")
            )
        finally:
            stage.close()

        assert not result.failed
        assert (out / "docs").is_dir()
