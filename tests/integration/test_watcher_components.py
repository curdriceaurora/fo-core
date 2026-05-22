"""Integration tests for file system watcher components.

Covers:
  - watcher/config.py  — WatcherConfig, _matches_pattern
  - watcher/queue.py   — EventQueue, FileEvent, EventType
  - watcher/handler.py — FileEventHandler SafeDir paths (PR6 / #270, #322)
  - watcher/monitor.py — FileMonitor SafeDir init, lifecycle, dynamic dirs
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from watcher.config import WatcherConfig
from watcher.monitor import FileMonitor
from watcher.queue import EventQueue, EventType, FileEvent

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# WatcherConfig
# ---------------------------------------------------------------------------


class TestWatcherConfigInit:
    def test_default_recursive(self) -> None:
        cfg = WatcherConfig()
        assert cfg.recursive is True

    def test_default_debounce(self) -> None:
        cfg = WatcherConfig()
        assert cfg.debounce_seconds == 2.0

    def test_default_batch_size(self) -> None:
        cfg = WatcherConfig()
        assert cfg.batch_size == 10

    def test_default_no_file_types(self) -> None:
        cfg = WatcherConfig()
        assert cfg.file_types is None

    def test_default_exclude_patterns_populated(self) -> None:
        cfg = WatcherConfig()
        assert len(cfg.exclude_patterns) > 0
        assert "*.tmp" in cfg.exclude_patterns

    def test_watch_directories_normalized_to_path(self, tmp_path: Path) -> None:
        cfg = WatcherConfig(watch_directories=[str(tmp_path)])
        assert isinstance(cfg.watch_directories[0], Path)

    def test_file_types_normalized_with_dot(self) -> None:
        cfg = WatcherConfig(file_types=["txt", ".pdf"])
        assert ".txt" in cfg.file_types
        assert ".pdf" in cfg.file_types

    def test_negative_debounce_raises(self) -> None:
        with pytest.raises(ValueError):
            WatcherConfig(debounce_seconds=-1.0)

    def test_zero_batch_size_raises(self) -> None:
        with pytest.raises(ValueError):
            WatcherConfig(batch_size=0)

    def test_custom_values(self, tmp_path: Path) -> None:
        cfg = WatcherConfig(
            watch_directories=[tmp_path],
            recursive=False,
            debounce_seconds=0.5,
            batch_size=5,
            file_types=[".py", ".txt"],
        )
        assert cfg.recursive is False
        assert cfg.debounce_seconds == 0.5
        assert cfg.batch_size == 5


class TestWatcherConfigShouldInclude:
    def test_includes_normal_txt_file(self, tmp_path: Path) -> None:
        cfg = WatcherConfig()
        f = tmp_path / "document.txt"
        assert cfg.should_include_file(f) is True

    def test_excludes_tmp_file(self, tmp_path: Path) -> None:
        cfg = WatcherConfig()
        f = tmp_path / "upload.tmp"
        assert cfg.should_include_file(f) is False

    def test_excludes_pyc_file(self, tmp_path: Path) -> None:
        cfg = WatcherConfig()
        f = tmp_path / "module.pyc"
        assert cfg.should_include_file(f) is False

    def test_excludes_ds_store(self, tmp_path: Path) -> None:
        cfg = WatcherConfig()
        f = tmp_path / ".DS_Store"
        assert cfg.should_include_file(f) is False

    def test_file_type_filter_includes_matching(self, tmp_path: Path) -> None:
        cfg = WatcherConfig(file_types=[".py"])
        assert cfg.should_include_file(tmp_path / "script.py") is True

    def test_file_type_filter_excludes_non_matching(self, tmp_path: Path) -> None:
        cfg = WatcherConfig(file_types=[".py"])
        assert cfg.should_include_file(tmp_path / "data.txt") is False

    def test_excludes_git_directory_files(self, tmp_path: Path) -> None:
        cfg = WatcherConfig()
        # .git itself should be excluded
        git_path = tmp_path / ".git"
        assert cfg.should_include_file(git_path) is False

    def test_custom_exclude_pattern(self, tmp_path: Path) -> None:
        cfg = WatcherConfig(exclude_patterns=["*.log"])
        assert cfg.should_include_file(tmp_path / "app.log") is False
        assert cfg.should_include_file(tmp_path / "app.txt") is True

    def test_swp_file_excluded(self, tmp_path: Path) -> None:
        cfg = WatcherConfig()
        assert cfg.should_include_file(tmp_path / "file.swp") is False

    def test_pycache_excluded(self, tmp_path: Path) -> None:
        cfg = WatcherConfig()
        pc = tmp_path / "__pycache__"
        assert cfg.should_include_file(pc) is False


# ---------------------------------------------------------------------------
# EventType
# ---------------------------------------------------------------------------


class TestEventType:
    def test_values(self) -> None:
        assert EventType.CREATED == "created"
        assert EventType.MODIFIED == "modified"
        assert EventType.DELETED == "deleted"
        assert EventType.MOVED == "moved"


# ---------------------------------------------------------------------------
# FileEvent
# ---------------------------------------------------------------------------


def _make_event(
    event_type: EventType = EventType.CREATED,
    path: Path | None = None,
) -> FileEvent:
    return FileEvent(
        event_type=event_type,
        path=path or Path("test.txt"),
        timestamp=datetime.now(UTC),
    )


class TestFileEvent:
    def test_created_event(self) -> None:
        e = _make_event(EventType.CREATED)
        assert e.event_type == EventType.CREATED

    def test_event_is_frozen(self) -> None:
        e = _make_event()
        with pytest.raises((AttributeError, TypeError)):
            e.event_type = EventType.MODIFIED  # type: ignore[misc]

    def test_default_is_directory_false(self) -> None:
        e = _make_event()
        assert e.is_directory is False

    def test_default_dest_path_none(self) -> None:
        e = _make_event()
        assert e.dest_path is None

    def test_move_event_has_dest_path(self, tmp_path: Path) -> None:
        e = FileEvent(
            event_type=EventType.MOVED,
            path=tmp_path / "old.txt",
            timestamp=datetime.now(UTC),
            dest_path=tmp_path / "new.txt",
        )
        assert e.dest_path is not None


# ---------------------------------------------------------------------------
# EventQueue
# ---------------------------------------------------------------------------


class TestEventQueueBasics:
    def test_starts_empty(self) -> None:
        q = EventQueue()
        assert q.is_empty is True
        assert q.size == 0

    def test_enqueue_increases_size(self) -> None:
        q = EventQueue()
        q.enqueue(_make_event())
        assert q.size == 1

    def test_dequeue_batch_returns_events(self) -> None:
        q = EventQueue()
        q.enqueue(_make_event(EventType.CREATED))
        batch = q.dequeue_batch()
        assert len(batch) == 1
        assert batch[0].event_type == EventType.CREATED

    def test_dequeue_batch_empties_queue(self) -> None:
        q = EventQueue()
        for _ in range(3):
            q.enqueue(_make_event())
        q.dequeue_batch(max_size=3)
        assert q.is_empty

    def test_dequeue_batch_respects_max_size(self) -> None:
        q = EventQueue()
        for _ in range(5):
            q.enqueue(_make_event())
        batch = q.dequeue_batch(max_size=2)
        assert len(batch) == 2
        assert q.size == 3

    def test_dequeue_empty_queue_returns_empty_list(self) -> None:
        q = EventQueue()
        batch = q.dequeue_batch()
        assert batch == []

    def test_peek_returns_first_event(self) -> None:
        q = EventQueue()
        e1 = _make_event(EventType.CREATED, Path("a.txt"))
        e2 = _make_event(EventType.MODIFIED, Path("b.txt"))
        q.enqueue(e1)
        q.enqueue(e2)
        assert q.peek() is e1

    def test_peek_empty_returns_none(self) -> None:
        q = EventQueue()
        assert q.peek() is None

    def test_clear_removes_all(self) -> None:
        q = EventQueue()
        for _ in range(5):
            q.enqueue(_make_event())
        removed = q.clear()
        assert removed == 5
        assert q.is_empty

    def test_clear_returns_count(self) -> None:
        q = EventQueue()
        q.enqueue(_make_event())
        q.enqueue(_make_event())
        count = q.clear()
        assert count == 2

    def test_clear_empty_returns_zero(self) -> None:
        q = EventQueue()
        assert q.clear() == 0


class TestEventQueueMaxSize:
    def test_max_size_drops_oldest(self) -> None:
        q = EventQueue(max_size=2)
        e1 = _make_event(EventType.CREATED, Path("a.txt"))
        e2 = _make_event(EventType.MODIFIED, Path("b.txt"))
        e3 = _make_event(EventType.DELETED, Path("c.txt"))
        q.enqueue(e1)
        q.enqueue(e2)
        q.enqueue(e3)  # drops e1
        assert q.size == 2
        batch = q.dequeue_batch()
        paths = [str(e.path) for e in batch]
        assert "c.txt" in paths

    def test_zero_max_size_unlimited(self) -> None:
        q = EventQueue(max_size=0)
        for i in range(100):
            q.enqueue(_make_event(EventType.CREATED, Path(f"f{i}.txt")))
        assert q.size == 100


class TestEventQueueBackpressure:
    """F1 (hardening roadmap #159): integration coverage for the new
    observability surface — ``dropped_count``, ``is_full``, ``max_size``
    — so the integration-coverage floor tracks these paths."""

    def test_dropped_count_tracks_overflow(self) -> None:
        q = EventQueue(max_size=2)
        for i in range(5):
            q.enqueue(_make_event(EventType.CREATED, Path(f"f{i}.txt")))
        # 5 enqueues on 2-slot queue → 3 drops.
        assert q.dropped_count == 3
        # Unbounded queue never records drops.
        unbounded = EventQueue()
        for i in range(50):
            unbounded.enqueue(_make_event(EventType.CREATED, Path(f"u{i}.txt")))
        assert unbounded.dropped_count == 0

    def test_is_full_signals_backpressure(self) -> None:
        q = EventQueue(max_size=2)
        assert q.is_full is False
        q.enqueue(_make_event(EventType.CREATED, Path("a.txt")))
        assert q.is_full is False
        q.enqueue(_make_event(EventType.CREATED, Path("b.txt")))
        assert q.is_full is True
        # Consuming makes room — backpressure released.
        q.dequeue_batch(max_size=1)
        assert q.is_full is False
        # Unbounded is never full.
        assert EventQueue().is_full is False

    def test_max_size_exposes_capacity(self) -> None:
        assert EventQueue(max_size=42).max_size == 42
        assert EventQueue().max_size == 0

    def test_overflow_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Pre-F1 overflow was silent; post-F1 the first drop logs."""
        q = EventQueue(max_size=1)
        q.enqueue(_make_event(EventType.CREATED, Path("seed.txt")))
        with caplog.at_level("WARNING", logger="watcher.queue"):
            q.enqueue(_make_event(EventType.CREATED, Path("overflow.txt")))
        assert any("overflow" in rec.message.lower() for rec in caplog.records)
        assert q.dropped_count == 1


class TestEventQueueBlocking:
    def test_dequeue_blocking_returns_immediately_if_events_present(self) -> None:
        q = EventQueue()
        q.enqueue(_make_event())
        batch = q.dequeue_batch_blocking(max_size=1, timeout=0.0)
        assert len(batch) == 1

    def test_dequeue_blocking_timeout_returns_empty(self) -> None:
        q = EventQueue()
        batch = q.dequeue_batch_blocking(max_size=1, timeout=0.01)
        assert batch == []


# ---------------------------------------------------------------------------
# PR6 SafeDir watcher paths — integration coverage
# ---------------------------------------------------------------------------


class TestFileEventHandlerSafeDirIntegration:
    """Integration coverage for the watch_root + SafeDir paths (PR6 / #270)."""

    def test_direct_child_regular_file_allowed(self, tmp_path: Path) -> None:
        """_safedir_allows returns True for a regular direct-child file."""

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir
        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        regular = watch_root / "file.txt"
        regular.write_bytes(b"data")

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            assert handler._safedir_allows(regular) is True

    def test_path_outside_root_rejected(self, tmp_path: Path) -> None:
        """_safedir_allows rejects paths whose lstat form is outside the primary watch root.

        S1 fix (issue #347): the old behaviour returned ``True`` (allow) when
        ``lstat_path.relative_to(watch_root)`` raised ``ValueError``, which was a
        security bypass — a symlink whose resolved directory escapes the root would
        pass the watcher-level SafeDir check unchallenged.

        The new behaviour returns ``False`` (reject).  Multi-root ``FileMonitor``
        configs avoid this by disabling the single-root SafeDir entirely in
        ``__init__`` (``_safe_dir = None``) when more than one watch directory is
        configured; the pipeline-level SafeDir is their backstop.
        """

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir
        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"x")

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            assert handler._safedir_allows(outside) is False

    def test_nested_path_allowed_through(self, tmp_path: Path) -> None:
        """_safedir_allows allows nested paths (checked at pipeline level)."""

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir
        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        watch_root = tmp_path / "watch"
        (watch_root / "sub").mkdir(parents=True)
        nested = watch_root / "sub" / "file.txt"
        nested.write_bytes(b"data")

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            assert handler._safedir_allows(nested) is True

    def test_symlink_in_watch_root_rejected(self, tmp_path: Path) -> None:
        """_safedir_allows rejects a symlink directly in the watch root."""

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir
        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        target = tmp_path / "outside.txt"
        target.write_bytes(b"secret")
        link = watch_root / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation not supported")

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            assert handler._safedir_allows(link) is False

    def test_no_safedir_allows_all(self, tmp_path: Path) -> None:
        """_safedir_allows returns True when safe_dir is None (line 252)."""
        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        watch_root = tmp_path / "watch"
        watch_root.mkdir()

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        assert handler._safedir_allows(watch_root / "anything.txt") is True

    def test_transient_oserror_allows_through(self, tmp_path: Path) -> None:
        """Non-SymlinkRejected OSError from open_child is treated as allow (lines 295-296)."""

        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_root = tmp_path / "watch"
        watch_root.mkdir()

        sd_mock = MagicMock()
        sd_mock.open_child.side_effect = FileNotFoundError("gone")

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue, safe_dir=sd_mock, watch_root=watch_root)
        assert handler._safedir_allows(watch_root / "gone.txt") is True


# ---------------------------------------------------------------------------
# handler.py — additional SafeDir + handler coverage (issue #322 / PR #337)
# ---------------------------------------------------------------------------


class TestFileEventHandlerInitValidation:
    """Line 102 — ValueError when safe_dir/watch_root are given one-sided."""

    def test_safedir_without_watch_root_raises(self, tmp_path: Path) -> None:
        """Providing safe_dir but not watch_root raises ValueError (line 102)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir
        from watcher.handler import FileEventHandler

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            with pytest.raises(ValueError, match="must be provided together"):
                FileEventHandler(config, queue, safe_dir=sd, watch_root=None)

    def test_watch_root_without_safedir_raises(self, tmp_path: Path) -> None:
        """Providing watch_root but not safe_dir raises ValueError (line 102)."""
        from watcher.handler import FileEventHandler

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with pytest.raises(ValueError, match="must be provided together"):
            FileEventHandler(config, queue, safe_dir=None, watch_root=watch_root)


class TestFileEventHandlerPipeline:
    """Lines 158-244 — on_moved dest_path decode, register_callback, _handle_event."""

    def test_on_moved_with_dest_path_decoded(self, tmp_path: Path) -> None:
        """on_moved decodes raw dest_path when present (lines 160-162)."""
        from watchdog.events import FileMovedEvent

        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        src = str(tmp_path / "old.txt")
        dest = str(tmp_path / "new.txt")
        event = FileMovedEvent(src_path=src, dest_path=dest)
        handler.on_moved(event)
        events = queue.dequeue_batch(1)
        assert len(events) == 1
        assert events[0].event_type == EventType.MOVED
        assert events[0].dest_path == Path(dest)

    def test_register_callback_all_types(self, tmp_path: Path) -> None:
        """register_callback stores callbacks for all four event types (lines 178-184)."""
        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        cb = MagicMock()
        for et in (EventType.CREATED, EventType.MODIFIED, EventType.DELETED, EventType.MOVED):
            handler.register_callback(et, cb)
        # All four lists now have the callback.
        assert cb in handler._on_created_callbacks
        assert cb in handler._on_modified_callbacks
        assert cb in handler._on_deleted_callbacks
        assert cb in handler._on_moved_callbacks

    def test_handle_event_safedir_rejected_event_dropped(self, tmp_path: Path) -> None:
        """_handle_event drops CREATE events when _safedir_allows returns False (line 228)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from unittest.mock import patch

        from watchdog.events import FileCreatedEvent

        from utils.safedir import SafeDir
        from watcher.handler import FileEventHandler

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        target = tmp_path / "secret.txt"
        target.write_bytes(b"secret")
        link = watch_root / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation not supported")

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            # Monkeypatch _safedir_allows so the symlink is definitely rejected.
            with patch.object(handler, "_safedir_allows", return_value=False):
                event = FileCreatedEvent(src_path=str(link))
                handler.on_created(event)
        assert queue.size == 0

    def test_handle_event_debounce_drop_logs(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Debounced events log a debug message (line 211) without being queued."""
        from watchdog.events import FileModifiedEvent

        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=10.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        src = str(tmp_path / "file.txt")
        with caplog.at_level("DEBUG", logger="watcher.handler"):
            handler.on_modified(FileModifiedEvent(src_path=src))
            handler.on_modified(FileModifiedEvent(src_path=src))
        assert queue.size == 1
        assert any("debounced" in r.message.lower() for r in caplog.records)

    def test_clear_debounce_state_and_pending_paths(self, tmp_path: Path) -> None:
        """clear_debounce_state resets count; pending_paths reflects the dict (lines 487-494)."""
        from watchdog.events import FileCreatedEvent

        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=10.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "a.txt")))
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "b.txt")))
        assert handler.pending_paths == 2
        handler.clear_debounce_state()
        assert handler.pending_paths == 0

    def test_fire_callbacks_on_all_event_types(self, tmp_path: Path) -> None:
        """_fire_callbacks invokes registered callbacks for CREATED/MODIFIED/DELETED/MOVED."""
        from watchdog.events import (
            FileCreatedEvent,
            FileDeletedEvent,
            FileModifiedEvent,
            FileMovedEvent,
        )

        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)

        created_cb = MagicMock()
        modified_cb = MagicMock()
        deleted_cb = MagicMock()
        moved_cb = MagicMock()

        handler.register_callback(EventType.CREATED, created_cb)
        handler.register_callback(EventType.MODIFIED, modified_cb)
        handler.register_callback(EventType.DELETED, deleted_cb)
        handler.register_callback(EventType.MOVED, moved_cb)

        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "f.txt")))
        handler.on_modified(FileModifiedEvent(src_path=str(tmp_path / "g.txt")))
        handler.on_deleted(FileDeletedEvent(src_path=str(tmp_path / "h.txt")))
        handler.on_moved(
            FileMovedEvent(src_path=str(tmp_path / "old.txt"), dest_path=str(tmp_path / "new.txt"))
        )

        created_cb.assert_called_once()
        modified_cb.assert_called_once()
        deleted_cb.assert_called_once()
        moved_cb.assert_called_once()

    def test_on_moved_no_dest_path_branch(self, tmp_path: Path) -> None:
        """on_moved skips decode when raw_dest is None — False branch of line 160."""
        from unittest.mock import MagicMock

        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        # Simulate event with no dest_path attribute.
        event = MagicMock()
        event.src_path = str(tmp_path / "old.txt")
        del event.dest_path
        handler.on_moved(event)
        events = queue.dequeue_batch(1)
        assert len(events) == 1
        assert events[0].dest_path is None

    def test_filtered_file_drops_with_debug_log(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_handle_event logs debug for filtered-out files (lines 206-207)."""
        from watchdog.events import FileCreatedEvent

        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=0.0, exclude_patterns=["*.tmp"])
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        with caplog.at_level("DEBUG", logger="watcher.handler"):
            handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "scratch.tmp")))
        assert queue.size == 0
        assert any("filtered out" in r.message.lower() for r in caplog.records)

    def test_handle_event_safedir_allows_queues_event(self, tmp_path: Path) -> None:
        """CREATE event with SafeDir allowing the file is queued (line 227->231 True path)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from watchdog.events import FileCreatedEvent

        from utils.safedir import SafeDir
        from watcher.handler import FileEventHandler

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        regular = watch_root / "allowed.txt"
        regular.write_bytes(b"ok")

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            handler.on_created(FileCreatedEvent(src_path=str(regular)))
        assert queue.size == 1

    def test_debounce_returns_false_second_rapid_event(self, tmp_path: Path) -> None:
        """Second rapid event on same file returns False from _should_process (lines 413-416)."""
        from watchdog.events import FileModifiedEvent

        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=60.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        src = str(tmp_path / "file.txt")
        # First event: allowed through (sets debounce timestamp).
        handler.on_modified(FileModifiedEvent(src_path=src))
        # Second event immediately: within window → debounced (hits line 413-414).
        handler.on_modified(FileModifiedEvent(src_path=src))
        assert queue.size == 1

    def test_stale_debounce_entry_evicted(self, tmp_path: Path) -> None:
        """Stale debounce entry is deleted (line 436) when age exceeds stale horizon."""
        import time as _time

        from watcher.handler import _MIN_EVICTION_HORIZON_S, _STALE_MULTIPLIER, FileEventHandler

        config = WatcherConfig(debounce_seconds=10.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)

        horizon = max(config.debounce_seconds * _STALE_MULTIPLIER, _MIN_EVICTION_HORIZON_S)
        now = _time.monotonic()
        with handler._debounce_lock:
            handler._last_event_times["stale/path"] = now - horizon - 1.0
        # Trigger eviction.
        handler._should_process("trigger/path")
        assert "stale/path" not in handler._last_event_times

    def test_hard_cap_drops_oldest_entries(self, tmp_path: Path) -> None:
        """Hard cap drop evicts oldest entries when dict exceeds _MAX_DEBOUNCE_ENTRIES
        (lines 442-461)."""
        import time as _time

        from watcher.handler import _MAX_DEBOUNCE_ENTRIES, FileEventHandler

        config = WatcherConfig(debounce_seconds=60.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)

        now = _time.monotonic()
        overflow = 10
        with handler._debounce_lock:
            for i in range(_MAX_DEBOUNCE_ENTRIES + overflow):
                handler._last_event_times[f"path/{i}"] = now - (overflow - i) * 0.0001

        handler._should_process("new/path")
        assert len(handler._last_event_times) <= _MAX_DEBOUNCE_ENTRIES + 1
        # Oldest entries removed.
        for i in range(overflow):
            assert f"path/{i}" not in handler._last_event_times

    def test_callback_exception_logged_not_raised(self, tmp_path: Path) -> None:
        """Exception in a callback is logged and does not propagate (lines 482-483)."""
        from watchdog.events import FileCreatedEvent

        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)

        def _bad(event: object) -> None:
            raise RuntimeError("intentional failure")

        handler.register_callback(EventType.CREATED, _bad)
        # Must not raise.
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "file.txt")))
        assert queue.size == 1


class TestSafeDirAllowsSymlinkLoopPaths:
    """Lines 290-299, 347-355 — RuntimeError symlink-loop paths in _safedir_allows."""

    def test_symlink_loop_in_parent_returns_false(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """RuntimeError during parent.resolve() is caught → False (lines 290-299)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from unittest.mock import patch

        from utils.safedir import SafeDir
        from watcher.handler import FileEventHandler

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            path = watch_root / "loop.txt"

            def _raise_runtime(*_a: object, **_kw: object) -> None:
                raise RuntimeError("Symlink loop detected")

            with caplog.at_level("ERROR", logger="watcher.handler"):
                with patch.object(Path, "resolve", side_effect=_raise_runtime):
                    result = handler._safedir_allows(path)

        assert result is False
        assert any("symlink_loop" in r.message for r in caplog.records)

    def test_symlink_loop_in_leaf_resolve_returns_false(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """RuntimeError during leaf path.resolve() is caught → False (lines 347-355).

        This exercises the second resolve() call (line 346) that guards against
        symlink loops in the leaf entry itself, reached only after the containment
        check passes (direct child of watch_root).
        """
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from unittest.mock import patch

        from utils.safedir import SafeDir
        from watcher.handler import FileEventHandler

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            path = watch_root / "leaf.txt"

            resolve_calls: list[int] = [0]

            def _selective_resolve(self: Path) -> Path:
                resolve_calls[0] += 1
                # First call is parent.resolve() — let it succeed.
                # Second call is path.resolve() — raise RuntimeError.
                if resolve_calls[0] == 1:
                    return Path(str(self))
                raise RuntimeError("Symlink loop detected in leaf")

            with caplog.at_level("ERROR", logger="watcher.handler"):
                with patch.object(Path, "resolve", _selective_resolve):
                    result = handler._safedir_allows(path)

        assert result is False
        assert any("symlink_loop" in r.message for r in caplog.records)

    def test_commonpath_outside_root_returns_false(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """commonpath mismatch → False + security_event log (lines 326-331)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        import os
        from unittest.mock import patch

        from utils.safedir import SafeDir
        from watcher.handler import FileEventHandler

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            path = watch_root / "file.txt"

            # Make commonpath return something other than watch_root so the
            # guard fires (lines 325-331).
            with caplog.at_level("ERROR", logger="watcher.handler"):
                with patch.object(os.path, "commonpath", return_value="/different/root"):
                    result = handler._safedir_allows(path)

        assert result is False
        assert any("outside_root" in r.message for r in caplog.records)

    def test_commonpath_value_error_sets_empty_common(self, tmp_path: Path) -> None:
        """os.path.commonpath raising ValueError → common = '' → rejected (lines 323-325)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        import os
        from unittest.mock import patch

        from utils.safedir import SafeDir
        from watcher.handler import FileEventHandler

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            path = watch_root / "file.txt"

            with patch.object(os.path, "commonpath", side_effect=ValueError("mixed roots")):
                result = handler._safedir_allows(path)

        # common="" != str(watch_root) → returns False.
        assert result is False


# ---------------------------------------------------------------------------
# monitor.py — FileMonitor integration coverage (PR #337)
# ---------------------------------------------------------------------------


def _wait_for_events(
    monitor: FileMonitor,
    predicate: object,
    timeout: float = 3.0,
) -> list[FileEvent]:
    """Drain events until predicate is satisfied or timeout expires."""
    all_events: list[FileEvent] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        batch = monitor.get_events_blocking(max_size=100, timeout=min(0.1, remaining))
        all_events.extend(batch)
        if predicate(all_events):  # type: ignore[operator]
            return all_events
    return all_events


def _drain_startup(monitor: FileMonitor) -> None:
    """Wait for a 0.1 s quiet window to drain startup events."""
    deadline = time.monotonic() + 1.0
    quiet_since: float | None = None
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        batch = monitor.get_events_blocking(max_size=100, timeout=min(0.05, remaining))
        if batch:
            quiet_since = None
        else:
            if quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= 0.1:
                break


class TestFileMonitorSafeDirInit:
    """Lines 56-90 — FileMonitor.__init__ SafeDir wiring and fallback."""

    def test_monitor_opens_safedir_on_posix(self, tmp_path: Path) -> None:
        """On POSIX, __init__ opens a SafeDir on the first watch directory (lines 65-84)."""
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        # SafeDir was wired into the handler.
        assert mon.handler._safe_dir is not None
        assert mon.handler._watch_root == watch_root.resolve()

    def test_monitor_no_safedir_when_no_watch_dirs(self) -> None:
        """No watch directories → safe_dir stays None (lines 65 branch not taken)."""
        config = WatcherConfig(watch_directories=[], debounce_seconds=0.0)
        mon = FileMonitor(config)
        assert mon.handler._safe_dir is None
        assert mon.handler._watch_root is None

    def test_monitor_safedir_open_failure_falls_back(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If SafeDir.open_root raises, monitor falls back gracefully (lines 71-80).

        SafeDir is imported lazily inside FileMonitor.__init__, so we patch it
        at its definition site (utils.safedir.SafeDir) rather than on the
        watcher.monitor module namespace.
        """
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from unittest.mock import patch

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        with caplog.at_level("WARNING", logger="watcher.monitor"):
            with patch("utils.safedir.SafeDir.open_root", side_effect=OSError("permission denied")):
                mon = FileMonitor(config)

        # Fallback: safe_dir and watch_root both None.
        assert mon.handler._safe_dir is None
        assert mon.handler._watch_root is None
        assert any("cannot open SafeDir" in r.message for r in caplog.records)

    def test_monitor_default_construction(self) -> None:
        """FileMonitor() with no args uses defaults (lines 56-57)."""
        mon = FileMonitor()
        assert mon.config is not None
        assert mon.queue is not None


class TestFileMonitorLifecycleIntegration:
    """Lines 104-155 — start(), stop(), and observer_type."""

    def test_start_stop_cycle(self, tmp_path: Path) -> None:
        """start() sets is_running; stop() clears it (lines 104-155)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        assert not mon.is_running
        mon.start()
        assert mon.is_running
        assert mon.observer_type in ("native", "polling")
        mon.stop()
        assert not mon.is_running

    def test_stop_when_not_running_is_noop(self, tmp_path: Path) -> None:
        """stop() on an idle monitor does not raise (lines 145-146 early return)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.stop()
        assert not mon.is_running

    def test_double_start_raises(self, tmp_path: Path) -> None:
        """Second start() raises RuntimeError (line 106)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                mon.start()
        finally:
            mon.stop()

    def test_observer_type_none_before_start(self, tmp_path: Path) -> None:
        """observer_type is 'none' before start (line 90)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        assert mon.observer_type == "none"

    def test_start_nonexistent_directory_raises(self, tmp_path: Path) -> None:
        """start() with missing directory raises FileNotFoundError (line 325)."""
        config = WatcherConfig(watch_directories=[tmp_path / "no_such_dir"], debounce_seconds=0.0)
        mon = FileMonitor(config)
        with pytest.raises(FileNotFoundError):
            mon.start()


class TestFileMonitorDynamicDirectoriesIntegration:
    """Lines 171-217 — add_directory, remove_directory edge cases."""

    def test_add_directory_while_running(self, tmp_path: Path) -> None:
        """add_directory schedules immediately when monitor is running (lines 178-179)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        extra = tmp_path / "extra"
        extra.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        try:
            mon.add_directory(extra)
            assert len(mon.watched_directories) == 2
        finally:
            mon.stop()

    def test_add_directory_before_start_appends_to_config(self, tmp_path: Path) -> None:
        """add_directory before start appends to config.watch_directories (lines 181-183)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        extra = tmp_path / "extra"
        extra.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.add_directory(extra)
        assert extra.resolve() in [d.resolve() for d in mon.config.watch_directories]

    def test_add_duplicate_directory_raises(self, tmp_path: Path) -> None:
        """Adding an already-watched dir raises ValueError (line 175-176)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        try:
            with pytest.raises(ValueError, match="already watched"):
                mon.add_directory(watch_root)
        finally:
            mon.stop()

    def test_add_nonexistent_directory_raises(self, tmp_path: Path) -> None:
        """Adding a non-existent dir raises FileNotFoundError (line 325)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        try:
            with pytest.raises(FileNotFoundError):
                mon.add_directory(tmp_path / "ghost")
        finally:
            mon.stop()

    def test_remove_directory_while_running(self, tmp_path: Path) -> None:
        """remove_directory unschedules watch when running (lines 203-209)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        try:
            mon.remove_directory(watch_root)
            assert len(mon.watched_directories) == 0
        finally:
            mon.stop()

    def test_remove_directory_removes_from_config(self, tmp_path: Path) -> None:
        """remove_directory also removes path from config.watch_directories (lines 212-215)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        try:
            mon.remove_directory(watch_root)
            assert watch_root.resolve() not in [d.resolve() for d in mon.config.watch_directories]
        finally:
            mon.stop()

    def test_remove_nonexistent_directory_raises(self, tmp_path: Path) -> None:
        """Removing an unwatched directory raises ValueError (lines 200-201)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        try:
            with pytest.raises(ValueError, match="not being watched"):
                mon.remove_directory(tmp_path / "not_watched")
        finally:
            mon.stop()


class TestFileMonitorGetEventsIntegration:
    """Lines 229-248 — get_events and get_events_blocking."""

    def test_get_events_default_batch_size(self, tmp_path: Path) -> None:
        """get_events() with no argument uses config.batch_size (lines 229-230)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0, batch_size=3)
        mon = FileMonitor(config)
        # Manually enqueue 5 events.
        for i in range(5):
            mon.queue.enqueue(
                FileEvent(
                    event_type=EventType.CREATED,
                    path=watch_root / f"f{i}.txt",
                    timestamp=datetime.now(UTC),
                )
            )
        batch = mon.get_events()
        assert len(batch) == 3

    def test_get_events_blocking_default_batch_size(self, tmp_path: Path) -> None:
        """get_events_blocking() without max_size uses config.batch_size (lines 247-248)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0, batch_size=2)
        mon = FileMonitor(config)
        for i in range(4):
            mon.queue.enqueue(
                FileEvent(
                    event_type=EventType.CREATED,
                    path=watch_root / f"g{i}.txt",
                    timestamp=datetime.now(UTC),
                )
            )
        batch = mon.get_events_blocking(timeout=0.0)
        assert len(batch) == 2


class TestFileMonitorCallbackRegistrationIntegration:
    """Lines 256-280 — on_created/modified/deleted/moved delegate to handler."""

    def test_on_created_registered(self, tmp_path: Path) -> None:
        """on_created stores callback in handler (line 256)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        cb = MagicMock()
        mon.on_created(cb)
        assert cb in mon.handler._on_created_callbacks

    def test_on_modified_registered(self, tmp_path: Path) -> None:
        """on_modified stores callback in handler (line 264)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        cb = MagicMock()
        mon.on_modified(cb)
        assert cb in mon.handler._on_modified_callbacks

    def test_on_deleted_registered(self, tmp_path: Path) -> None:
        """on_deleted stores callback in handler (line 272)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        cb = MagicMock()
        mon.on_deleted(cb)
        assert cb in mon.handler._on_deleted_callbacks

    def test_on_moved_registered(self, tmp_path: Path) -> None:
        """on_moved stores callback in handler (line 280)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        cb = MagicMock()
        mon.on_moved(cb)
        assert cb in mon.handler._on_moved_callbacks


class TestFileMonitorPropertiesIntegration:
    """Lines 285-307 — is_running, watched_directories, event_count, observer_type."""

    def test_is_running_property(self, tmp_path: Path) -> None:
        """is_running reflects internal _running flag (line 285)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        assert mon.is_running is False
        mon.start()
        assert mon.is_running is True
        mon.stop()
        assert mon.is_running is False

    def test_watched_directories_property(self, tmp_path: Path) -> None:
        """watched_directories returns list of Path objects (lines 290-291)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        dirs = mon.watched_directories
        assert isinstance(dirs, list)
        assert all(isinstance(d, Path) for d in dirs)
        mon.stop()

    def test_event_count_property(self, tmp_path: Path) -> None:
        """event_count delegates to queue.size (line 296)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        assert mon.event_count == 0
        mon.queue.enqueue(
            FileEvent(
                event_type=EventType.CREATED,
                path=watch_root / "x.txt",
                timestamp=datetime.now(UTC),
            )
        )
        assert mon.event_count == 1

    def test_observer_type_property_after_start(self, tmp_path: Path) -> None:
        """observer_type is 'native' or 'polling' after start (line 307)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        assert mon.observer_type in ("native", "polling")
        mon.stop()


class TestFileMonitorScheduleDirectoryIntegration:
    """Line 327 — _schedule_directory when observer is not None."""

    def test_schedule_directory_with_observer(self, tmp_path: Path) -> None:
        """_schedule_directory schedules the watch on the observer (lines 327-331)."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        extra = tmp_path / "extra"
        extra.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        mon.start()
        try:
            # add_directory calls _schedule_directory while observer is live.
            mon.add_directory(extra)
            path_key = str(extra.resolve())
            assert path_key in mon._watches
        finally:
            mon.stop()

    def test_observer_fallback_to_polling(self, tmp_path: Path) -> None:
        """When native Observer raises, monitor falls back to PollingObserver (lines 113-121)."""
        from unittest.mock import patch

        from watchdog.observers.polling import PollingObserver

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        with patch("watcher.monitor.Observer", side_effect=OSError("unavailable")):
            mon.start()
        try:
            assert mon.observer_type == "polling"
            assert isinstance(mon._observer, PollingObserver)
        finally:
            mon.stop()


# ---------------------------------------------------------------------------
# Targeted branch-coverage fill-ins (handler.py + monitor.py, PR #337)
# ---------------------------------------------------------------------------


class TestHandlerBranchFillins:
    """Micro-tests that exercise specific missed branches in handler.py."""

    def test_413_416_elapsed_gte_debounce_allows_through(self, tmp_path: Path) -> None:
        """Branch 413->416: elapsed >= debounce_seconds so the event is NOT debounced.

        Achieved by pre-seeding an entry that is older than the debounce window,
        then calling _should_process on the same key. With elapsed >= debounce_seconds
        the code skips the `return False` and falls through to line 416.
        """
        import time as _time

        from watcher.handler import FileEventHandler

        config = WatcherConfig(debounce_seconds=0.1)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        key = "some/path.txt"
        # Seed an entry that is already beyond the debounce window.
        with handler._debounce_lock:
            handler._last_event_times[key] = _time.monotonic() - 1.0  # 1 s > 0.1 s window
        # Now _should_process sees last_time is not None AND elapsed (1s) >= 0.1s.
        result = handler._should_process(key)
        assert result is True

    def test_455_exit_latch_suppresses_repeat_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Branch 455->exit: warning latch prevents repeated log on successive breaches."""
        import time as _time

        from watcher.handler import _MAX_DEBOUNCE_ENTRIES, FileEventHandler

        config = WatcherConfig(debounce_seconds=60.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)
        now = _time.monotonic()

        def _fill_over_cap() -> None:
            # Add entries just above the cap so hard-cap eviction fires.
            with handler._debounce_lock:
                for i in range(_MAX_DEBOUNCE_ENTRIES + 5):
                    handler._last_event_times[f"p/{i}"] = now - i * 0.00001

        _fill_over_cap()
        with caplog.at_level("WARNING", logger="watcher.handler"):
            handler._should_process("trigger/0")  # First breach: logs warning, sets latch.
            first_warnings = [r for r in caplog.records if "exceeded" in r.message.lower()]
            assert len(first_warnings) == 1

        caplog.clear()
        _fill_over_cap()
        with caplog.at_level("WARNING", logger="watcher.handler"):
            handler._should_process("trigger/1")  # Latch active: suppresses log (455->exit).
            repeat_warnings = [r for r in caplog.records if "exceeded" in r.message.lower()]
            assert len(repeat_warnings) == 0

    def test_283_345_safedir_set_watch_root_none_falls_through(self, tmp_path: Path) -> None:
        """Branch 283->345: _watch_root is None while _safe_dir is not None.

        The constructor prevents this combination, so we bypass it by directly
        assigning _watch_root = None after construction to force the False branch
        at line 283 (skipping the containment-check block and falling through to
        the path.resolve() guard at line 345).
        """
        if sys.platform == "win32":
            pytest.skip("SafeDir is POSIX-only")

        from utils.safedir import SafeDir
        from watcher.handler import FileEventHandler

        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        regular = watch_root / "file.txt"
        regular.write_bytes(b"x")

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        with SafeDir.open_root(watch_root) as sd:
            handler = FileEventHandler(config, queue, safe_dir=sd, watch_root=watch_root)
            # Force the branch: safe_dir is set but watch_root cleared.
            handler._watch_root = None
            result = handler._safedir_allows(regular)
        # Falls through to open_child on the real file → allowed.
        assert result is True


class TestMonitorBranchFillins:
    """Micro-tests for missed branches in monitor.py."""

    def test_182_185_add_directory_before_start_path_already_in_config(
        self, tmp_path: Path
    ) -> None:
        """Branch 182->185: add_directory before start when path already in config.

        When the monitor is not running, add_directory checks whether the path is
        already in config.watch_directories. The False branch (path IS present)
        skips the append.
        """
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        dirs_before = list(mon.config.watch_directories)
        # Attempting to add the same directory before start hits the False branch
        # at line 182 (the duplicate check in add_directory). However the duplicate
        # check at line 175 fires first when the path is already in _watches. Since
        # _watches is empty before start, we need to add the path directly so the
        # code reaches line 182 and skips the append because it IS in watch_directories.
        mon._watches[str(watch_root.resolve())] = None  # type: ignore[assignment]
        # The duplicate-watch guard at 175 now fires — test that it raises.
        # To reach 182->185, we need NOT running AND path NOT in _watches.
        # Reset _watches so the duplicate guard doesn't fire.
        mon._watches.clear()
        mon.add_directory(watch_root)
        # Path was already in config — should NOT be duplicated.
        resolved = watch_root.resolve()
        count = sum(1 for d in mon.config.watch_directories if d.resolve() == resolved)
        assert count == len([d for d in dirs_before if d.resolve() == resolved])

    def test_203_209_remove_directory_when_not_running(self, tmp_path: Path) -> None:
        """Branch 203->209: remove_directory when _running is False skips unschedule."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        # Pre-populate _watches without starting, so remove_directory doesn't raise.
        path_key = str(watch_root.resolve())
        mon._watches[path_key] = None  # type: ignore[assignment]
        # _running is False → the condition at 203 is False → branch 203->209.
        mon.remove_directory(watch_root)
        assert path_key not in mon._watches

    def test_214_215_remove_directory_removes_from_config(self, tmp_path: Path) -> None:
        """Lines 214-215: config.watch_directories.remove(path) succeeds."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        path_key = str(watch_root.resolve())
        mon._watches[path_key] = None  # type: ignore[assignment]
        mon.remove_directory(watch_root)
        # The resolved path should no longer be in config.watch_directories.
        resolved = watch_root.resolve()
        assert not any(d.resolve() == resolved for d in mon.config.watch_directories)

    def test_327_exit_schedule_directory_observer_none(self, tmp_path: Path) -> None:
        """Branch 327->exit: _schedule_directory when observer is None is a no-op."""
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        extra = tmp_path / "extra"
        extra.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        # Call _schedule_directory directly with observer=None (pre-start state).
        # The branch `if self._observer is not None:` is False → 327->exit.
        mon._schedule_directory(extra, recursive=True)
        # No watch was registered (observer is None), but also no error.
        assert str(extra.resolve()) not in mon._watches

    def test_214_215_remove_directory_path_not_in_config_watch_dirs(self, tmp_path: Path) -> None:
        """Lines 214-215: ValueError from config.watch_directories.remove() is silenced.

        This branch fires when the path being removed is in _watches but was never
        added to (or was already removed from) config.watch_directories.
        """
        watch_root = tmp_path / "watch"
        watch_root.mkdir()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)
        mon = FileMonitor(config)
        path_key = str(watch_root.resolve())
        mon._watches[path_key] = None  # type: ignore[assignment]
        # Clear config.watch_directories so remove() raises ValueError → except branch.
        mon.config.watch_directories.clear()
        # Should succeed without raising (the ValueError is silenced at lines 214-215).
        mon.remove_directory(watch_root)
        assert path_key not in mon._watches
