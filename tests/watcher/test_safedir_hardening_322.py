"""Unit tests for the SafeDir hardening changes from issue #322.

Covers all 7 findings:

  1.1  Constructor safe_dir/watch_root pairing validation.
  1.2  FileMonitor passes safe_dir/watch_root to FileEventHandler.
  1.3  path.resolve() RuntimeError (symlink loop) is handled.
  1.4  Containment check uses lstat path so a symlink pointing outside
       watch_root is rejected before open_child.
  1.5  Nested paths are ancestry-checked (no longer returned True
       unconditionally).
  1.6  PostprocessorStage fails closed on SymlinkRejected — no fallback to
       Path.mkdir.
  (1.7 is verified in test_stages.py TestWriterSafeDirBranches and by
       inspection — the copystat call was removed.)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from watcher.config import WatcherConfig
from watcher.handler import FileEventHandler
from watcher.monitor import FileMonitor
from watcher.queue import EventQueue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config() -> WatcherConfig:
    return WatcherConfig(debounce_seconds=0.0, exclude_patterns=[])


@pytest.fixture
def queue() -> EventQueue:
    return EventQueue()


# ---------------------------------------------------------------------------
# 1.1 — Constructor safe_dir/watch_root pairing
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestHandlerConstructorPairing:
    """FileEventHandler.__init__ must reject mismatched safe_dir/watch_root."""

    def test_both_none_accepted(self, default_config: WatcherConfig, queue: EventQueue) -> None:
        """Both omitted → no error."""
        handler = FileEventHandler(default_config, queue, safe_dir=None, watch_root=None)
        assert handler._safe_dir is None
        assert handler._watch_root is None

    def test_both_provided_accepted(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """Both provided → accepted (POSIX only)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(default_config, queue, safe_dir=sd, watch_root=watch_root)
        assert handler._watch_root == watch_root.resolve()

    def test_safe_dir_without_watch_root_raises(
        self, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """Providing safe_dir without watch_root must raise ValueError."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir

        sd_mock = MagicMock(spec=SafeDir)
        with pytest.raises(ValueError, match="together or both omitted"):
            FileEventHandler(default_config, queue, safe_dir=sd_mock, watch_root=None)

    def test_watch_root_without_safe_dir_raises(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """Providing watch_root without safe_dir must raise ValueError."""
        with pytest.raises(ValueError, match="together or both omitted"):
            FileEventHandler(default_config, queue, safe_dir=None, watch_root=tmp_path)


# ---------------------------------------------------------------------------
# 1.2 — FileMonitor passes safe_dir/watch_root to FileEventHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestFileMonitorPassesSafeDir:
    """FileMonitor.__init__ must wire safe_dir/watch_root into handler."""

    def test_handler_has_safe_dir_when_watch_dir_configured(self, tmp_path: Path) -> None:
        """On POSIX with a real watch directory, handler receives a SafeDir."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        config = WatcherConfig(watch_directories=[watch_dir], debounce_seconds=0.0)
        monitor = FileMonitor(config)
        assert monitor.handler._safe_dir is not None
        assert monitor.handler._watch_root == watch_dir.resolve()
        # stop() must close the SafeDir fd (R1 fix — no manual __exit__ workaround needed)
        monitor.stop()
        assert monitor.handler._safe_dir is None

    def test_handler_has_no_safe_dir_when_no_watch_dir(self) -> None:
        """When no watch directories are configured, handler has no SafeDir."""
        config = WatcherConfig(watch_directories=[], debounce_seconds=0.0)
        monitor = FileMonitor(config)
        assert monitor.handler._safe_dir is None
        assert monitor.handler._watch_root is None

    def test_handler_falls_back_when_safedir_unavailable(self, tmp_path: Path) -> None:
        """If SafeDir.open_root raises, handler falls back to no SafeDir."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        config = WatcherConfig(watch_directories=[watch_dir], debounce_seconds=0.0)

        # SafeDir is imported inline inside FileMonitor.__init__; patch at source.
        with patch("utils.safedir.SafeDir.open_root", side_effect=OSError("unavailable")):
            monitor = FileMonitor(config)

        assert monitor.handler._safe_dir is None
        assert monitor.handler._watch_root is None

    def test_handler_safe_dir_and_watch_root_consistent(self, tmp_path: Path) -> None:
        """safe_dir and watch_root are always set together (never one without the other)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        config = WatcherConfig(watch_directories=[watch_dir], debounce_seconds=0.0)
        monitor = FileMonitor(config)
        sd = monitor.handler._safe_dir
        wr = monitor.handler._watch_root
        assert (sd is None) == (wr is None), "safe_dir and watch_root must be paired"
        monitor.stop()  # releases SafeDir fd cleanly


# ---------------------------------------------------------------------------
# 1.2b — Multi-root startup + conditional add_directory clear (issue #347)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestFileMonitorMultiRootStartup:
    """Multi-root configs at startup must not silently drop events (issue #347 P1)."""

    def test_multi_root_startup_disables_containment_check(self, tmp_path: Path) -> None:
        """With 2+ watch_directories at startup, watch_root is None (no silent drops)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        config = WatcherConfig(watch_directories=[d1, d2], debounce_seconds=0.0)
        monitor = FileMonitor(config)

        # Both safe_dir and watch_root must be None — containment check disabled
        # so events from d2 are not silently dropped.
        assert monitor.handler._watch_root is None
        assert monitor.handler._safe_dir is None

    def test_add_directory_under_root_preserves_containment(self, tmp_path: Path) -> None:
        """Adding a sub-directory of the primary root must NOT disable containment."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_dir = tmp_path / "watch"
        subdir = watch_dir / "subdir"
        watch_dir.mkdir()
        subdir.mkdir()
        config = WatcherConfig(watch_directories=[watch_dir], debounce_seconds=0.0)
        monitor = FileMonitor(config)

        assert monitor.handler._watch_root is not None  # pre-condition

        monitor.add_directory(subdir)

        # subdir is under watch_dir — _watch_root must still be set
        assert monitor.handler._watch_root is not None
        monitor.stop()

    def test_add_directory_outside_root_disables_containment(self, tmp_path: Path) -> None:
        """Adding a directory outside the primary root disables containment check."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_dir = tmp_path / "watch"
        other_dir = tmp_path / "other"
        watch_dir.mkdir()
        other_dir.mkdir()
        config = WatcherConfig(watch_directories=[watch_dir], debounce_seconds=0.0)
        monitor = FileMonitor(config)

        assert monitor.handler._watch_root is not None  # pre-condition

        monitor.add_directory(other_dir)

        # other_dir is outside watch_dir — _watch_root must be cleared
        assert monitor.handler._watch_root is None
        monitor.stop()


# ---------------------------------------------------------------------------
# 1.3 — path.resolve() RuntimeError (symlink loop) handled
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestSafeDirAllowsSymlinkLoopHandled:
    """RuntimeError from path.resolve() is caught and treated as rejection."""

    def test_resolve_runtime_error_returns_false(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """A RuntimeError from Path.resolve (symlink loop) causes _safedir_allows → False."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        loop_path = watch_root / "loop.txt"

        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(default_config, queue, safe_dir=sd, watch_root=watch_root)

            # Patch Path.parent.resolve to raise RuntimeError simulating a loop
            with patch.object(Path, "resolve", side_effect=RuntimeError("Symlink loop detected")):
                result = handler._safedir_allows(loop_path)

        assert result is False

    def test_resolve_runtime_error_in_parent_returns_false(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """RuntimeError during parent resolution (loop in directory path) → False."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        sd_mock = MagicMock()
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        path = watch_root / "file.txt"

        handler = FileEventHandler(default_config, queue, safe_dir=sd_mock, watch_root=watch_root)

        # Simulate RuntimeError on parent.resolve()
        original_resolve = Path.resolve

        def _patched_resolve(self: Path, *args: object, **kwargs: object) -> Path:
            if self == path.parent:
                raise RuntimeError("Symlink loop detected in directory")
            return original_resolve(self, *args, **kwargs)

        with patch.object(Path, "resolve", _patched_resolve):
            result = handler._safedir_allows(path)

        assert result is False


# ---------------------------------------------------------------------------
# 1.4 — Containment uses lstat path (symlink-to-outside rejected)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestSafeDirAllowsLstatContainment:
    """Symlink pointing outside watch_root is rejected before open_child."""

    def test_symlink_pointing_outside_watch_root_rejected(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """A direct-child symlink whose TARGET is outside watch_root is blocked.

        Pre-fix, resolved.relative_to(watch_root) would raise ValueError and
        the event would be dropped — but only if the symlink's resolved path
        was outside the root.  Post-fix we check using commonpath on the
        lstat path so the check happens before O_NOFOLLOW.
        """
        if sys.platform == "win32":
            pytest.skip("SafeDir / symlinks are POSIX-only in this test")

        from utils.safedir import SafeDir

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"sensitive")

        # Create a symlink inside watch_root that points to the outside file.
        symlink_path = watch_root / "escape.txt"
        try:
            symlink_path.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(default_config, queue, safe_dir=sd, watch_root=watch_root)
            result = handler._safedir_allows(symlink_path)

        # The symlink should be rejected by the ancestry check on lstat path
        # OR by SymlinkRejected from open_child.
        assert result is False

    def test_regular_file_inside_watch_root_allowed(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """A real file directly inside watch_root passes the containment check."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        real_file = watch_root / "real.txt"
        real_file.write_bytes(b"data")

        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(default_config, queue, safe_dir=sd, watch_root=watch_root)
            result = handler._safedir_allows(real_file)

        assert result is True

    def test_path_completely_outside_watch_root_rejected(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """A path not under watch_root is rejected by the handler (issue #347).

        ``relative_to(watch_root)`` raises ``ValueError`` when the lstat path
        cannot be contained within the primary root.  The old code returned
        ``True`` here (security bypass); the fix returns ``False`` so that
        escaping-symlink paths cannot slip through (#347).

        In the multi-directory monitor scenario ``FileMonitor.add_directory()``
        clears ``handler._watch_root`` to ``None`` so that the containment
        check is disabled before a second directory is added — this test
        verifies the single-root, single-handler case where the check is active.
        """
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        sd_mock = MagicMock()
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        outside = tmp_path / "other_dir" / "file.txt"

        handler = FileEventHandler(default_config, queue, safe_dir=sd_mock, watch_root=watch_root)
        result = handler._safedir_allows(outside)

        # Rejected — lstat path cannot be relativized to watch_root (#347).
        assert result is False
        # open_child should NOT have been called on a path outside the primary root.
        sd_mock.open_child.assert_not_called()


# ---------------------------------------------------------------------------
# 1.5 — Nested paths ancestry-checked (no longer unconditionally True)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestSafeDirAllowsNestedPaths:
    """Nested paths under watch_root are ancestry-checked; paths outside are rejected (issue #347)."""

    def test_nested_path_inside_root_allowed(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """A real nested path under watch_root is still allowed through."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir

        watch_root = tmp_path / "watch"
        subdir = watch_root / "sub"
        subdir.mkdir(parents=True)
        nested = subdir / "file.txt"
        nested.write_bytes(b"x")

        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(default_config, queue, safe_dir=sd, watch_root=watch_root)
            result = handler._safedir_allows(nested)

        assert result is True

    def test_nested_path_outside_root_rejected(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """A nested path outside watch_root is rejected when watch_root is set (issue #347).

        Events from secondary watch directories added via ``add_directory()``
        are handled by ``FileMonitor.add_directory()`` clearing
        ``handler._watch_root`` to ``None`` before the second directory is
        registered — so the containment check is disabled for multi-root setups.
        This test verifies the single-root case where the check is active and
        outside paths must be rejected.
        """
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        sd_mock = MagicMock()
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        # Fabricate a nested path that is outside the primary root.
        outside_nested = tmp_path / "other" / "sub" / "file.txt"

        handler = FileEventHandler(default_config, queue, safe_dir=sd_mock, watch_root=watch_root)
        result = handler._safedir_allows(outside_nested)

        # Rejected — lstat path is outside watch_root and cannot be relativized (#347).
        assert result is False
        sd_mock.open_child.assert_not_called()

    def test_nested_path_does_not_call_open_child(
        self, tmp_path: Path, default_config: WatcherConfig, queue: EventQueue
    ) -> None:
        """For nested-but-valid paths open_child is not called (pipeline backstop)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        sd_mock = MagicMock()
        watch_root = tmp_path / "watch"
        subdir = watch_root / "sub"
        subdir.mkdir(parents=True)
        nested = subdir / "file.txt"
        nested.write_bytes(b"data")

        handler = FileEventHandler(default_config, queue, safe_dir=sd_mock, watch_root=watch_root)
        result = handler._safedir_allows(nested)

        assert result is True
        sd_mock.open_child.assert_not_called()


# ---------------------------------------------------------------------------
# 1.6 — PostprocessorStage fails closed on SymlinkRejected
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestPostprocessorFailsClosedOnSymlinkRejected:
    """PostprocessorStage must NOT fall back to Path.mkdir when SymlinkRejected."""

    def test_symlink_rejected_returns_failed_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SymlinkRejected from _get_category_safedir sets error and returns immediately."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from interfaces.pipeline import StageContext
        from pipeline.stages.postprocessor import PostprocessorStage
        from utils.safedir import SymlinkRejected

        out = tmp_path / "out"
        out.mkdir()
        src = tmp_path / "doc.txt"
        src.write_bytes(b"hi")

        stage = PostprocessorStage(output_directory=out)

        def _raise_symlink(*_a: object, **_kw: object) -> None:
            raise SymlinkRejected(20, "refused to open symlinked entry", "docs")

        monkeypatch.setattr(stage, "_get_category_safedir", _raise_symlink)
        try:
            ctx = StageContext(file_path=src, dry_run=False, category="docs", filename="doc")
            result = stage.process(ctx)
        finally:
            stage.close()

        assert result.failed
        assert result.error is not None
        assert "symlink" in result.error.lower()

    def test_symlink_rejected_does_not_create_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After SymlinkRejected no fallback directory must be created."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from interfaces.pipeline import StageContext
        from pipeline.stages.postprocessor import PostprocessorStage
        from utils.safedir import SymlinkRejected

        out = tmp_path / "out"
        out.mkdir()
        src = tmp_path / "doc.txt"
        src.write_bytes(b"hi")

        stage = PostprocessorStage(output_directory=out)

        def _raise_symlink(*_a: object, **_kw: object) -> None:
            raise SymlinkRejected(20, "refused to open symlinked entry", "secret_category")

        monkeypatch.setattr(stage, "_get_category_safedir", _raise_symlink)
        try:
            ctx = StageContext(
                file_path=src, dry_run=False, category="secret_category", filename="doc"
            )
            stage.process(ctx)
        finally:
            stage.close()

        # The category directory must NOT have been created via Path.mkdir fallback.
        assert not (out / "secret_category").exists()

    def test_non_symlink_exception_still_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-SymlinkRejected OSError from _get_category_safedir still falls back."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from interfaces.pipeline import StageContext
        from pipeline.stages.postprocessor import PostprocessorStage

        out = tmp_path / "out"
        out.mkdir()
        src = tmp_path / "doc.txt"
        src.write_bytes(b"hi")

        stage = PostprocessorStage(output_directory=out)

        def _raise_oserror(*_a: object, **_kw: object) -> None:
            raise OSError("simulated non-symlink failure")

        monkeypatch.setattr(stage, "_get_category_safedir", _raise_oserror)
        try:
            ctx = StageContext(file_path=src, dry_run=False, category="docs", filename="doc")
            result = stage.process(ctx)
        finally:
            stage.close()

        # Non-SymlinkRejected: postprocessor falls back and continues.
        assert not result.failed
        assert result.destination is not None
        assert isinstance(result.destination, Path)

    def test_symlink_rejected_logs_security_event(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """SymlinkRejected logs a security_event at ERROR level."""
        import logging

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from interfaces.pipeline import StageContext
        from pipeline.stages.postprocessor import PostprocessorStage
        from utils.safedir import SymlinkRejected

        out = tmp_path / "out"
        out.mkdir()
        src = tmp_path / "doc.txt"
        src.write_bytes(b"hi")

        stage = PostprocessorStage(output_directory=out)

        def _raise_symlink(*_a: object, **_kw: object) -> None:
            raise SymlinkRejected(20, "refused", "docs")

        monkeypatch.setattr(stage, "_get_category_safedir", _raise_symlink)
        try:
            ctx = StageContext(file_path=src, dry_run=False, category="docs", filename="doc")
            with caplog.at_level(logging.ERROR, logger="pipeline.stages.postprocessor"):
                stage.process(ctx)
        finally:
            stage.close()

        assert any("security_event" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Issue #348 — R1: stop() closes SafeDir fd; R2: add_directory() warns
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestFileMonitorStopClosesSafeDir:
    """FileMonitor.stop() must release the SafeDir fd (issue #348 R1)."""

    def test_stop_closes_safe_dir(self, tmp_path: Path) -> None:
        """stop() sets handler._safe_dir to None, releasing the directory fd."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        config = WatcherConfig(watch_directories=[watch_dir], debounce_seconds=0.0)
        monitor = FileMonitor(config)
        assert monitor.handler._safe_dir is not None, "pre-condition: SafeDir must be open"

        monitor.stop()

        assert monitor.handler._safe_dir is None

    def test_stop_is_idempotent(self, tmp_path: Path) -> None:
        """Calling stop() twice must not raise."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        config = WatcherConfig(watch_directories=[watch_dir], debounce_seconds=0.0)
        monitor = FileMonitor(config)

        monitor.stop()
        monitor.stop()  # second call must not raise

        assert monitor.handler._safe_dir is None

    def test_stop_without_safe_dir_does_not_raise(self) -> None:
        """stop() on a monitor with no watch directories (no SafeDir) must not raise."""
        config = WatcherConfig(watch_directories=[], debounce_seconds=0.0)
        monitor = FileMonitor(config)
        assert monitor.handler._safe_dir is None

        monitor.stop()  # no-op; must not raise

    def test_start_after_stop_reinitializes_safe_dir(self, tmp_path: Path) -> None:
        """start() after stop() must reopen SafeDir so symlink checks remain active.

        stop() clears handler._safe_dir to release the fd.  Without a matching
        reinitialize in start(), a stop()/start() restart leaves the monitor
        running with no watcher-level SafeDir — a security regression (issue #348 P1).
        """
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        config = WatcherConfig(watch_directories=[watch_dir], debounce_seconds=0.0)
        monitor = FileMonitor(config)

        assert monitor.handler._safe_dir is not None, "pre-condition: SafeDir open after init"

        monitor.stop()
        assert monitor.handler._safe_dir is None, "pre-condition: SafeDir closed after stop()"

        monitor.start()
        try:
            assert monitor.handler._safe_dir is not None, (
                "SafeDir must be reinitialized after start() — "
                "watcher-level symlink checks were disabled after restart"
            )
            assert monitor.handler._watch_root == watch_dir.resolve()
        finally:
            monitor.stop()


@pytest.mark.unit
@pytest.mark.ci
class TestFileMonitorAddDirectoryWarns:
    """add_directory() must warn that secondary dirs are not SafeDir-hardened (#348 R2)."""

    def test_add_directory_logs_warning_when_safe_dir_active(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Adding a second directory while SafeDir is active emits a warning."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        import logging

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        second_dir = tmp_path / "second"
        second_dir.mkdir()

        config = WatcherConfig(watch_directories=[watch_dir], debounce_seconds=0.0)
        monitor = FileMonitor(config)
        assert monitor.handler._safe_dir is not None, "pre-condition: SafeDir must be open"

        try:
            with caplog.at_level(logging.WARNING, logger="watcher.monitor"):
                monitor.add_directory(second_dir, recursive=False)

            warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
            assert any("outside the primary SafeDir root" in msg for msg in warning_messages), (
                f"expected SafeDir warning, got: {warning_messages}"
            )
        finally:
            monitor.stop()

    def test_add_directory_no_warning_without_safe_dir(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Adding a directory when no SafeDir is active must not emit a SafeDir warning."""
        import logging

        second_dir = tmp_path / "second"
        second_dir.mkdir()

        config = WatcherConfig(watch_directories=[], debounce_seconds=0.0)
        monitor = FileMonitor(config)
        assert monitor.handler._safe_dir is None

        with caplog.at_level(logging.WARNING, logger="watcher.monitor"):
            monitor.add_directory(second_dir, recursive=False)

        safedir_warnings = [
            r.message
            for r in caplog.records
            if r.levelno == logging.WARNING and "SafeDir" in r.message
        ]
        assert safedir_warnings == [], f"unexpected SafeDir warnings: {safedir_warnings}"
