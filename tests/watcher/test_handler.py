"""
Unit tests for FileEventHandler.

Tests debouncing, pattern filtering, callback dispatch, and event queuing.
Uses real file operations via tmp_path where possible.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from watcher.config import WatcherConfig
from watcher.handler import FileEventHandler
from watcher.queue import EventQueue, EventType, FileEvent


@pytest.fixture
def default_config() -> WatcherConfig:
    """Watcher config with short debounce for fast tests."""
    return WatcherConfig(
        debounce_seconds=0.0,
        exclude_patterns=["*.tmp", ".git", "__pycache__"],
    )


@pytest.fixture
def debounce_config() -> WatcherConfig:
    """Watcher config with a measurable debounce window."""
    return WatcherConfig(
        debounce_seconds=0.5,
        exclude_patterns=[],
    )


@pytest.fixture
def queue() -> EventQueue:
    """Fresh event queue."""
    return EventQueue()


@pytest.mark.unit
class TestFileEventHandlerFiltering:
    """Tests for event filtering by pattern and file type."""

    def test_accepts_normal_file(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that a normal file passes through filters."""
        handler = FileEventHandler(default_config, queue)
        event = FileCreatedEvent(src_path=str(tmp_path / "document.txt"))
        handler.on_created(event)
        assert queue.size == 1

    def test_filters_tmp_files(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that .tmp files are filtered out."""
        handler = FileEventHandler(default_config, queue)
        event = FileCreatedEvent(src_path=str(tmp_path / "scratch.tmp"))
        handler.on_created(event)
        assert queue.size == 0

    def test_filters_git_directory(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that .git directory contents are filtered."""
        handler = FileEventHandler(default_config, queue)
        event = FileModifiedEvent(src_path=str(tmp_path / ".git" / "index"))
        handler.on_modified(event)
        assert queue.size == 0

    def test_filters_pycache(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that __pycache__ contents are filtered."""
        handler = FileEventHandler(default_config, queue)
        event = FileCreatedEvent(src_path=str(tmp_path / "__pycache__" / "module.cpython-312.pyc"))
        handler.on_created(event)
        assert queue.size == 0

    def test_file_type_filter_allows_matching_extension(
        self, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that file_types filter allows matching extensions."""
        config = WatcherConfig(
            debounce_seconds=0.0,
            exclude_patterns=[],
            file_types=[".txt", ".pdf"],
        )
        handler = FileEventHandler(config, queue)

        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "doc.txt")))
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "report.pdf")))
        assert queue.size == 2

    def test_file_type_filter_rejects_non_matching(self, queue: EventQueue, tmp_path: Path) -> None:
        """Test that file_types filter rejects non-matching extensions."""
        config = WatcherConfig(
            debounce_seconds=0.0,
            exclude_patterns=[],
            file_types=[".txt"],
        )
        handler = FileEventHandler(config, queue)

        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "image.png")))
        assert queue.size == 0

    def test_directory_events_pass_through(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that directory events are not filtered by file type."""
        config = WatcherConfig(
            debounce_seconds=0.0,
            exclude_patterns=[],
            file_types=[".txt"],
        )
        handler = FileEventHandler(config, queue)

        event = DirCreatedEvent(src_path=str(tmp_path / "newdir"))
        handler.on_created(event)
        assert queue.size == 1
        queued = queue.dequeue_batch(1)
        assert queued[0].is_directory is True


@pytest.mark.unit
class TestFileEventHandlerDebounce:
    """Tests for debouncing behavior."""

    def test_rapid_events_debounced(
        self, debounce_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that rapid events on the same file are debounced."""
        handler = FileEventHandler(debounce_config, queue)
        src = str(tmp_path / "file.txt")

        # Fire multiple events rapidly on the same file
        for _ in range(5):
            handler.on_modified(FileModifiedEvent(src_path=src))

        # Only the first should have gotten through
        assert queue.size == 1

    def test_events_after_debounce_window_pass(
        self, queue: EventQueue, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Test that events after debounce window are processed."""
        import watcher.handler as _handler_module

        config = WatcherConfig(debounce_seconds=0.1, exclude_patterns=[])
        handler = FileEventHandler(config, queue)
        src = str(tmp_path / "file.txt")

        handler.on_modified(FileModifiedEvent(src_path=src))
        assert queue.size == 1

        # Advance the handler's monotonic clock past the debounce window
        real_monotonic = time.monotonic
        monkeypatch.setattr(_handler_module.time, "monotonic", lambda: real_monotonic() + 1.0)

        handler.on_modified(FileModifiedEvent(src_path=src))
        assert queue.size == 2

    def test_different_files_not_debounced(
        self, debounce_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that events on different files are independent."""
        handler = FileEventHandler(debounce_config, queue)

        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "file_a.txt")))
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "file_b.txt")))
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "file_c.txt")))

        assert queue.size == 3

    def test_zero_debounce_allows_all(self, queue: EventQueue, tmp_path: Path) -> None:
        """Test that zero debounce allows all events through."""
        config = WatcherConfig(debounce_seconds=0.0, exclude_patterns=[])
        handler = FileEventHandler(config, queue)
        src = str(tmp_path / "file.txt")

        for _ in range(10):
            handler.on_modified(FileModifiedEvent(src_path=src))

        assert queue.size == 10

    def test_clear_debounce_state(
        self, debounce_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that clearing debounce state allows immediate re-processing."""
        handler = FileEventHandler(debounce_config, queue)
        src = str(tmp_path / "file.txt")

        handler.on_modified(FileModifiedEvent(src_path=src))
        assert queue.size == 1

        # This would be debounced
        handler.on_modified(FileModifiedEvent(src_path=src))
        assert queue.size == 1

        # Clear debounce state
        handler.clear_debounce_state()

        # Now it should pass through again
        handler.on_modified(FileModifiedEvent(src_path=src))
        assert queue.size == 2


@pytest.mark.unit
class TestFileEventHandlerEventTypes:
    """Tests for correct handling of different event types."""

    def test_created_event(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test handling of file creation events."""
        handler = FileEventHandler(default_config, queue)
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "new.txt")))

        events = queue.dequeue_batch(1)
        assert events[0].event_type == EventType.CREATED

    def test_modified_event(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test handling of file modification events."""
        handler = FileEventHandler(default_config, queue)
        handler.on_modified(FileModifiedEvent(src_path=str(tmp_path / "existing.txt")))

        events = queue.dequeue_batch(1)
        assert events[0].event_type == EventType.MODIFIED

    def test_deleted_event(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test handling of file deletion events."""
        handler = FileEventHandler(default_config, queue)
        handler.on_deleted(FileDeletedEvent(src_path=str(tmp_path / "gone.txt")))

        events = queue.dequeue_batch(1)
        assert events[0].event_type == EventType.DELETED

    def test_moved_event_with_dest(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test handling of file move events with destination path."""
        handler = FileEventHandler(default_config, queue)
        dest = tmp_path / "new.txt"
        event = FileMovedEvent(src_path=str(tmp_path / "old.txt"), dest_path=str(dest))
        handler.on_moved(event)

        events = queue.dequeue_batch(1)
        assert events[0].event_type == EventType.MOVED
        assert events[0].dest_path == dest

    def test_directory_deletion_event(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test handling of directory deletion events."""
        handler = FileEventHandler(default_config, queue)
        handler.on_deleted(DirDeletedEvent(src_path=str(tmp_path / "olddir")))

        events = queue.dequeue_batch(1)
        assert events[0].is_directory is True
        assert events[0].event_type == EventType.DELETED


@pytest.mark.unit
class TestFileEventHandlerCallbacks:
    """Tests for callback registration and dispatch."""

    def test_created_callback_invoked(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that on_created callback fires."""
        handler = FileEventHandler(default_config, queue)
        callback = MagicMock()
        handler.register_callback(EventType.CREATED, callback)

        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "new.txt")))
        callback.assert_called_once()
        arg = callback.call_args[0][0]
        assert isinstance(arg, FileEvent)
        assert arg.event_type == EventType.CREATED

    def test_modified_callback_invoked(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that on_modified callback fires."""
        handler = FileEventHandler(default_config, queue)
        callback = MagicMock()
        handler.register_callback(EventType.MODIFIED, callback)

        handler.on_modified(FileModifiedEvent(src_path=str(tmp_path / "file.txt")))
        callback.assert_called_once()

    def test_deleted_callback_invoked(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that on_deleted callback fires."""
        handler = FileEventHandler(default_config, queue)
        callback = MagicMock()
        handler.register_callback(EventType.DELETED, callback)

        handler.on_deleted(FileDeletedEvent(src_path=str(tmp_path / "gone.txt")))
        callback.assert_called_once()

    def test_moved_callback_invoked(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that on_moved callback fires."""
        handler = FileEventHandler(default_config, queue)
        callback = MagicMock()
        handler.register_callback(EventType.MOVED, callback)

        handler.on_moved(
            FileMovedEvent(src_path=str(tmp_path / "old.txt"), dest_path=str(tmp_path / "new.txt"))
        )
        callback.assert_called_once()

    def test_callback_error_does_not_crash(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that a failing callback does not prevent event queuing."""
        handler = FileEventHandler(default_config, queue)

        def bad_callback(event: FileEvent) -> None:
            raise RuntimeError("boom")

        handler.register_callback(EventType.CREATED, bad_callback)
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "file.txt")))

        # Event should still be in the queue despite callback failure
        assert queue.size == 1

    def test_multiple_callbacks_all_invoked(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that multiple callbacks for the same event type all fire."""
        handler = FileEventHandler(default_config, queue)
        cb1 = MagicMock()
        cb2 = MagicMock()
        handler.register_callback(EventType.CREATED, cb1)
        handler.register_callback(EventType.CREATED, cb2)

        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "file.txt")))
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_pending_paths_tracking(
        self, debounce_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that pending_paths tracks debounced path count."""
        handler = FileEventHandler(debounce_config, queue)
        assert handler.pending_paths == 0

        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "a.txt")))
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "b.txt")))
        assert handler.pending_paths == 2

        handler.clear_debounce_state()
        assert handler.pending_paths == 0


@pytest.mark.unit
class TestFileEventHandlerMovedEdgeCases:
    """Tests for edge cases in on_moved handling."""

    def test_moved_event_without_dest_path_attr(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test move event when dest_path attribute is absent."""
        handler = FileEventHandler(default_config, queue)
        # Simulate a move event where dest_path is missing
        event = MagicMock(spec=FileMovedEvent)
        event.src_path = str(tmp_path / "old.txt")
        # Remove dest_path attribute entirely
        del event.dest_path
        handler.on_moved(event)

        events = queue.dequeue_batch(1)
        assert len(events) == 1
        assert events[0].event_type == EventType.MOVED
        assert events[0].dest_path is None

    def test_moved_event_with_none_dest_path(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test move event when dest_path is explicitly None."""
        handler = FileEventHandler(default_config, queue)
        event = MagicMock(spec=FileMovedEvent)
        event.src_path = str(tmp_path / "old.txt")
        event.dest_path = None
        handler.on_moved(event)

        events = queue.dequeue_batch(1)
        assert len(events) == 1
        assert events[0].dest_path is None

    def test_moved_directory_event(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that directory move events are classified correctly."""
        from watchdog.events import DirMovedEvent

        handler = FileEventHandler(default_config, queue)
        dest = tmp_path / "newdir"
        event = DirMovedEvent(src_path=str(tmp_path / "olddir"), dest_path=str(dest))
        handler.on_moved(event)

        events = queue.dequeue_batch(1)
        assert len(events) == 1
        assert events[0].is_directory is True
        assert events[0].event_type == EventType.MOVED
        assert events[0].dest_path == dest


@pytest.mark.unit
class TestFileEventHandlerDebounceThreadSafety:
    """Tests for debounce behavior under concurrent access."""

    def test_concurrent_debounce_same_file(self, queue: EventQueue, tmp_path: Path) -> None:
        """Test that debouncing is thread-safe for the same file path."""
        config = WatcherConfig(debounce_seconds=1.0, exclude_patterns=[])
        handler = FileEventHandler(config, queue)
        errors: list[Exception] = []
        src = str(tmp_path / "shared.txt")

        def fire_event() -> None:
            try:
                handler.on_modified(FileModifiedEvent(src_path=src))
            except Exception as e:
                errors.append(e)

        import threading

        threads = [threading.Thread(target=fire_event) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Only one event should get through due to debouncing
        assert queue.size == 1

    def test_concurrent_debounce_different_files(self, queue: EventQueue, tmp_path: Path) -> None:
        """Test debouncing with different files from concurrent threads."""
        config = WatcherConfig(debounce_seconds=1.0, exclude_patterns=[])
        handler = FileEventHandler(config, queue)

        import threading

        def fire_event(file_id: int) -> None:
            handler.on_created(FileCreatedEvent(src_path=str(tmp_path / f"file_{file_id}.txt")))

        threads = [threading.Thread(target=fire_event, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each file should get through exactly once
        assert queue.size == 10


@pytest.mark.unit
class TestFileEventHandlerEventTimestamps:
    """Tests for event timestamp correctness."""

    def test_queued_event_has_utc_timestamp(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that queued events have UTC timestamps."""
        from datetime import UTC

        handler = FileEventHandler(default_config, queue)
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "test.txt")))

        events = queue.dequeue_batch(1)
        assert events[0].timestamp.tzinfo is not None
        assert events[0].timestamp.tzinfo in (UTC, UTC)

    def test_queued_event_path_is_pathlib(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that queued events have Path objects, not strings."""
        handler = FileEventHandler(default_config, queue)
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "test.txt")))

        events = queue.dequeue_batch(1)
        assert isinstance(events[0].path, Path)


@pytest.mark.unit
class TestFileEventHandlerCallbackEdgeCases:
    """Tests for callback dispatch edge cases."""

    def test_callback_receives_correct_dest_path_on_move(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that move callbacks receive the correct destination path."""
        handler = FileEventHandler(default_config, queue)
        received_events: list[FileEvent] = []
        handler.register_callback(EventType.MOVED, received_events.append)

        dest = tmp_path / "new.txt"
        event = FileMovedEvent(src_path=str(tmp_path / "old.txt"), dest_path=str(dest))
        handler.on_moved(event)

        assert len(received_events) == 1
        assert received_events[0].dest_path == dest

    def test_second_callback_runs_when_first_fails(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that subsequent callbacks fire even if an earlier one raises."""
        handler = FileEventHandler(default_config, queue)
        second_called = MagicMock()

        def bad_callback(event: FileEvent) -> None:
            raise ValueError("intentional error")

        handler.register_callback(EventType.CREATED, bad_callback)
        handler.register_callback(EventType.CREATED, second_called)

        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "file.txt")))

        second_called.assert_called_once()

    def test_no_callbacks_registered_no_error(
        self, default_config: WatcherConfig, queue: EventQueue, tmp_path: Path
    ) -> None:
        """Test that events process cleanly when no callbacks are registered."""
        handler = FileEventHandler(default_config, queue)
        # No callbacks registered, should not raise
        handler.on_created(FileCreatedEvent(src_path=str(tmp_path / "file.txt")))
        assert queue.size == 1


# ---------------------------------------------------------------------------
# F3 hardening — debounce-dict TTL eviction
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestDebounceDictEviction:
    """F3 (hardening roadmap #159): ``_last_event_times`` must not grow
    unbounded.

    Pre-F3: the dict accumulated one entry per observed path for the
    life of the daemon. A watcher pointed at a frequently-churning
    directory leaked memory indefinitely.

    Post-F3: entries older than ``debounce_seconds * _STALE_MULTIPLIER``
    are dropped on every ``_should_process`` call, and a hard cap of
    ``_MAX_DEBOUNCE_ENTRIES`` prevents pathological growth.
    """

    def test_stale_entries_are_evicted(self) -> None:
        """Entries older than the stale horizon drop on the next call.

        Uses a config with a realistic debounce window so
        ``_MIN_EVICTION_HORIZON_S`` (60s floor) isn't the active
        bound — we want the multiplier-driven horizon here.
        """
        from watcher.handler import _MIN_EVICTION_HORIZON_S, _STALE_MULTIPLIER

        config = WatcherConfig(debounce_seconds=10.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)

        # Effective horizon: max(10 * 10, 60) = 100s.
        horizon = max(config.debounce_seconds * _STALE_MULTIPLIER, _MIN_EVICTION_HORIZON_S)
        now = time.monotonic()
        with handler._debounce_lock:
            handler._last_event_times["stale/path"] = now - horizon - 1.0
            handler._last_event_times["fresh/path"] = now - 1.0

        handler._should_process("new/path")

        assert "stale/path" not in handler._last_event_times
        assert "fresh/path" in handler._last_event_times
        assert "new/path" in handler._last_event_times

    def test_min_horizon_floor_protects_zero_debounce_config(self) -> None:
        """When ``debounce_seconds=0`` the multiplier-based horizon is
        also 0 — without the ``_MIN_EVICTION_HORIZON_S`` floor every
        entry would evict on every call. Verify the floor holds."""
        from watcher.handler import _MIN_EVICTION_HORIZON_S

        config = WatcherConfig(debounce_seconds=0.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)

        now = time.monotonic()
        # Entry that is under the floor (must survive).
        with handler._debounce_lock:
            handler._last_event_times["recent"] = now - _MIN_EVICTION_HORIZON_S / 2

        handler._should_process("trigger")
        assert "recent" in handler._last_event_times

    def test_hard_cap_drops_oldest_in_bulk(self, caplog: pytest.LogCaptureFixture) -> None:
        """When the dict exceeds _MAX_DEBOUNCE_ENTRIES, oldest drop out."""
        from watcher.handler import _MAX_DEBOUNCE_ENTRIES

        # Use a large debounce_seconds so the age-eviction horizon is
        # comfortably larger than our synthetic timestamp spread, leaving
        # only the hard-cap branch to do the dropping.
        config = WatcherConfig(debounce_seconds=60.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)

        # Force entries above the cap, all within the stale horizon so
        # the age-based eviction path doesn't drop them — this exercises
        # the hard-cap branch specifically.
        now = time.monotonic()
        over = 50
        with handler._debounce_lock:
            for i in range(_MAX_DEBOUNCE_ENTRIES + over):
                # Fresh enough to survive age eviction, but ordered oldest-first
                # so the hard-cap branch drops the first ``over`` entries.
                handler._last_event_times[f"path/{i}"] = now - (over - i) * 0.0001

        with caplog.at_level("WARNING", logger="watcher.handler"):
            handler._should_process("new/path")

        # Dict size is now at the cap (plus the one new key).
        assert len(handler._last_event_times) <= _MAX_DEBOUNCE_ENTRIES + 1
        # First ``over`` entries (oldest) are gone.
        for i in range(over):
            assert f"path/{i}" not in handler._last_event_times
        # Warning was emitted.
        assert any(
            "exceeded" in rec.message.lower() and "dropped" in rec.message.lower()
            for rec in caplog.records
        )

    def test_eviction_does_not_drop_still_debouncing_entries(self) -> None:
        """An entry whose debounce window is still active must NOT be
        evicted by the TTL pass — it's not stale yet."""
        config = WatcherConfig(debounce_seconds=30.0)
        queue = EventQueue()
        handler = FileEventHandler(config, queue)

        # Entry added 5s ago; debounce window is 30s → still active.
        now = time.monotonic()
        with handler._debounce_lock:
            handler._last_event_times["active"] = now - 5.0

        handler._should_process("new/path")

        assert "active" in handler._last_event_times
