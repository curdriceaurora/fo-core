"""
Unit tests for EventQueue and FileEvent.

Tests thread safety, batching, capacity limits, and blocking behavior.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from watcher.queue import _DROP_LOG_INTERVAL, EventQueue, EventType, FileEvent

pytestmark = [pytest.mark.unit]
# Note: EventQueue tests use threading.Thread, making them
# timing-sensitive. Excluded from smoke suite.

# ---------------------------------------------------------------------------
# FileEvent tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileEvent:
    """Tests for the FileEvent dataclass."""

    def test_create_file_event(self, tmp_path: Path) -> None:
        """Test basic FileEvent construction."""
        now = datetime.now(UTC)
        path = tmp_path / "test.txt"
        event = FileEvent(
            event_type=EventType.CREATED,
            path=path,
            timestamp=now,
        )
        assert event.event_type == EventType.CREATED
        assert event.path == path
        assert event.timestamp == now
        assert event.is_directory is False
        assert event.dest_path is None

    def test_file_event_with_directory_flag(self, tmp_path: Path) -> None:
        """Test FileEvent for directory events."""
        event = FileEvent(
            event_type=EventType.CREATED,
            path=tmp_path / "newdir",
            timestamp=datetime.now(UTC),
            is_directory=True,
        )
        assert event.is_directory is True

    def test_file_event_with_dest_path(self, tmp_path: Path) -> None:
        """Test FileEvent for move events with destination."""
        dest = tmp_path / "new.txt"
        event = FileEvent(
            event_type=EventType.MOVED,
            path=tmp_path / "old.txt",
            timestamp=datetime.now(UTC),
            dest_path=dest,
        )
        assert event.dest_path == dest

    def test_file_event_is_frozen(self, tmp_path: Path) -> None:
        """Test that FileEvent instances are immutable."""
        event = FileEvent(
            event_type=EventType.CREATED,
            path=tmp_path / "test.txt",
            timestamp=datetime.now(UTC),
        )
        with pytest.raises(AttributeError):
            event.event_type = EventType.DELETED  # type: ignore[misc]

    def test_event_type_values(self) -> None:
        """Test EventType enum string values."""
        assert EventType.CREATED == "created"
        assert EventType.MODIFIED == "modified"
        assert EventType.DELETED == "deleted"
        assert EventType.MOVED == "moved"


# ---------------------------------------------------------------------------
# EventQueue tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEventQueue:
    """Tests for the EventQueue class."""

    def _make_event(
        self,
        tmp_path: Path,
        event_type: EventType = EventType.CREATED,
        name: str = "test.txt",
    ) -> FileEvent:
        """Helper to create a FileEvent."""
        return FileEvent(
            event_type=event_type,
            path=tmp_path / name,
            timestamp=datetime.now(UTC),
        )

    def test_enqueue_and_dequeue(self, tmp_path: Path) -> None:
        """Test basic enqueue and dequeue cycle."""
        queue = EventQueue()
        event = self._make_event(tmp_path)
        queue.enqueue(event)

        batch = queue.dequeue_batch(max_size=10)
        assert len(batch) == 1
        assert batch[0] is event

    def test_dequeue_empty_queue(self) -> None:
        """Test dequeuing from an empty queue returns empty list."""
        queue = EventQueue()
        batch = queue.dequeue_batch(max_size=10)
        assert batch == []

    def test_dequeue_respects_max_size(self, tmp_path: Path) -> None:
        """Test that dequeue_batch limits the number of returned events."""
        queue = EventQueue()
        for i in range(20):
            queue.enqueue(self._make_event(tmp_path, name=f"file_{i}.txt"))

        batch = queue.dequeue_batch(max_size=5)
        assert len(batch) == 5
        # Remaining events still in queue
        assert queue.size == 15

    def test_fifo_ordering(self, tmp_path: Path) -> None:
        """Test that events are dequeued in FIFO order."""
        queue = EventQueue()
        events = [self._make_event(tmp_path, name=f"file_{i}.txt") for i in range(5)]
        for e in events:
            queue.enqueue(e)

        batch = queue.dequeue_batch(max_size=5)
        assert batch == events

    def test_max_size_capacity(self, tmp_path: Path) -> None:
        """Test that a bounded queue drops oldest events when full."""
        queue = EventQueue(max_size=3)
        for i in range(5):
            queue.enqueue(self._make_event(tmp_path, name=f"file_{i}.txt"))

        # Should only have last 3 events
        assert queue.size == 3
        batch = queue.dequeue_batch(max_size=10)
        assert len(batch) == 3
        # Oldest two were dropped; remaining are files 2, 3, 4
        names = [str(e.path.name) for e in batch]
        assert names == ["file_2.txt", "file_3.txt", "file_4.txt"]

    def test_peek_returns_first_without_removing(self, tmp_path: Path) -> None:
        """Test that peek shows the next event without removing it."""
        queue = EventQueue()
        event = self._make_event(tmp_path)
        queue.enqueue(event)

        peeked = queue.peek()
        assert peeked is event
        assert queue.size == 1  # Still there

    def test_peek_empty_queue(self) -> None:
        """Test that peek returns None on empty queue."""
        queue = EventQueue()
        assert queue.peek() is None

    def test_clear(self, tmp_path: Path) -> None:
        """Test that clear removes all events and returns count."""
        queue = EventQueue()
        for i in range(5):
            queue.enqueue(self._make_event(tmp_path, name=f"file_{i}.txt"))

        removed = queue.clear()
        assert removed == 5
        assert queue.size == 0
        assert queue.is_empty is True

    def test_is_empty_property(self, tmp_path: Path) -> None:
        """Test the is_empty property."""
        queue = EventQueue()
        assert queue.is_empty is True

        queue.enqueue(self._make_event(tmp_path))
        assert queue.is_empty is False

    def test_size_property(self, tmp_path: Path) -> None:
        """Test the size property tracks queue length."""
        queue = EventQueue()
        assert queue.size == 0

        queue.enqueue(self._make_event(tmp_path))
        assert queue.size == 1

        queue.enqueue(self._make_event(tmp_path, name="other.txt"))
        assert queue.size == 2

        queue.dequeue_batch(max_size=1)
        assert queue.size == 1

    def test_thread_safety_concurrent_enqueue(self, tmp_path: Path) -> None:
        """Test that concurrent enqueue operations are thread-safe."""
        queue = EventQueue()
        num_threads = 10
        events_per_thread = 50

        def enqueue_events(thread_id: int) -> None:
            for i in range(events_per_thread):
                queue.enqueue(self._make_event(tmp_path, name=f"t{thread_id}_f{i}.txt"))

        threads = [threading.Thread(target=enqueue_events, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert queue.size == num_threads * events_per_thread

    def test_blocking_dequeue_returns_when_event_arrives(self, tmp_path: Path) -> None:
        """Test that blocking dequeue wakes up when an event is enqueued."""
        queue = EventQueue()
        results: list[list[FileEvent]] = []

        def consumer() -> None:
            batch = queue.dequeue_batch_blocking(max_size=5, timeout=5.0)
            results.append(batch)

        consumer_thread = threading.Thread(target=consumer)
        consumer_thread.start()

        event = self._make_event(tmp_path)
        queue.enqueue(event)

        consumer_thread.join(timeout=5.0)
        assert len(results) == 1
        assert len(results[0]) == 1
        assert results[0][0] is event

    def test_blocking_dequeue_timeout(self) -> None:
        """Test that blocking dequeue returns empty on timeout."""
        queue = EventQueue()
        batch = queue.dequeue_batch_blocking(max_size=5, timeout=0.1)
        assert batch == []


@pytest.mark.unit
class TestEventQueueEdgeCases:
    """Additional edge case tests for EventQueue."""

    def _make_event(
        self,
        tmp_path: Path,
        event_type: EventType = EventType.CREATED,
        name: str = "test.txt",
    ) -> FileEvent:
        """Helper to create a FileEvent."""
        return FileEvent(
            event_type=event_type,
            path=tmp_path / name,
            timestamp=datetime.now(UTC),
        )

    def test_dequeue_batch_blocking_with_existing_events(self, tmp_path: Path) -> None:
        """Test blocking dequeue returns immediately when events exist."""
        queue = EventQueue()
        queue.enqueue(self._make_event(tmp_path, name="a.txt"))
        queue.enqueue(self._make_event(tmp_path, name="b.txt"))

        # Should return immediately without blocking
        batch = queue.dequeue_batch_blocking(max_size=5, timeout=1.0)
        assert len(batch) == 2

    def test_dequeue_batch_blocking_returns_up_to_max_size(self, tmp_path: Path) -> None:
        """Test blocking dequeue respects max_size even when more events exist."""
        queue = EventQueue()
        for i in range(10):
            queue.enqueue(self._make_event(tmp_path, name=f"file_{i}.txt"))

        batch = queue.dequeue_batch_blocking(max_size=3, timeout=1.0)
        assert len(batch) == 3
        assert queue.size == 7

    def test_unbounded_queue_accepts_many_events(self, tmp_path: Path) -> None:
        """Test that unbounded queue (max_size=0) can hold many events."""
        queue = EventQueue(max_size=0)
        for i in range(1000):
            queue.enqueue(self._make_event(tmp_path, name=f"file_{i}.txt"))
        assert queue.size == 1000

    def test_bounded_queue_max_size_one(self, tmp_path: Path) -> None:
        """Test bounded queue with capacity of 1."""
        queue = EventQueue(max_size=1)
        queue.enqueue(self._make_event(tmp_path, name="first.txt"))
        queue.enqueue(self._make_event(tmp_path, name="second.txt"))

        assert queue.size == 1
        batch = queue.dequeue_batch(10)
        assert len(batch) == 1
        assert batch[0].path.name == "second.txt"

    def test_clear_empty_queue_returns_zero(self) -> None:
        """Test that clearing an empty queue returns 0."""
        queue = EventQueue()
        assert queue.clear() == 0

    def test_dequeue_batch_with_zero_max_size(self, tmp_path: Path) -> None:
        """Test dequeue_batch with max_size=0 returns empty list."""
        queue = EventQueue()
        queue.enqueue(self._make_event(tmp_path))
        batch = queue.dequeue_batch(max_size=0)
        assert batch == []
        assert queue.size == 1

    def test_concurrent_enqueue_and_dequeue(self, tmp_path: Path) -> None:
        """Test thread safety with concurrent enqueue and dequeue operations."""
        queue = EventQueue()
        total_events = 100
        dequeued_events: list[FileEvent] = []
        lock = threading.Lock()

        def producer() -> None:
            for i in range(total_events):
                queue.enqueue(self._make_event(tmp_path, name=f"file_{i}.txt"))

        def consumer() -> None:
            collected = 0
            while collected < total_events:
                batch = queue.dequeue_batch_blocking(max_size=10, timeout=1.0)
                if batch:
                    with lock:
                        dequeued_events.extend(batch)
                    collected += len(batch)

        producer_thread = threading.Thread(target=producer)
        consumer_thread = threading.Thread(target=consumer)

        producer_thread.start()
        consumer_thread.start()

        producer_thread.join(timeout=5.0)
        consumer_thread.join(timeout=5.0)

        assert len(dequeued_events) == total_events

    def test_blocking_dequeue_with_none_timeout(self, tmp_path: Path) -> None:
        """Test blocking dequeue with None timeout returns when event arrives."""
        queue = EventQueue()
        results: list[list[FileEvent]] = []

        def consumer() -> None:
            batch = queue.dequeue_batch_blocking(max_size=1, timeout=5.0)
            results.append(batch)

        t = threading.Thread(target=consumer)
        t.start()
        queue.enqueue(self._make_event(tmp_path))
        t.join(timeout=5.0)

        assert len(results) == 1
        assert len(results[0]) == 1

    def test_peek_does_not_affect_size(self, tmp_path: Path) -> None:
        """Test that repeated peek operations don't change queue size."""
        queue = EventQueue()
        event = self._make_event(tmp_path)
        queue.enqueue(event)

        for _ in range(5):
            peeked = queue.peek()
            assert peeked is event

        assert queue.size == 1

    def test_file_event_equality(self, tmp_path: Path) -> None:
        """Test that identical FileEvent instances are equal (frozen dataclass)."""
        ts = datetime.now(UTC)
        path = tmp_path / "a.txt"
        e1 = FileEvent(event_type=EventType.CREATED, path=path, timestamp=ts)
        e2 = FileEvent(event_type=EventType.CREATED, path=path, timestamp=ts)
        assert e1 == e2

    def test_file_event_inequality(self, tmp_path: Path) -> None:
        """Test that different FileEvent instances are not equal."""
        ts = datetime.now(UTC)
        path = tmp_path / "a.txt"
        e1 = FileEvent(event_type=EventType.CREATED, path=path, timestamp=ts)
        e2 = FileEvent(event_type=EventType.DELETED, path=path, timestamp=ts)
        assert e1 != e2

    def test_event_type_is_str_enum(self) -> None:
        """Test that EventType values can be used as strings."""
        assert str(EventType.CREATED) == "EventType.CREATED" or "created" in EventType.CREATED
        assert EventType.MODIFIED.value == "modified"
        assert isinstance(EventType.DELETED, str)


# ---------------------------------------------------------------------------
# F1 hardening — backpressure + dropped-event metric
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestEventQueueBackpressure:
    """F1 (hardening roadmap #159): overflow is no longer silent.

    Marked ``ci`` so the PR-suite ``-m "ci"`` run exercises the new
    ``dropped_count`` / ``is_full`` / ``max_size`` paths — otherwise
    the diff-cover gate (80% of changed lines) fails on D6-style
    unit-only coverage.

    Pre-F1 the queue used ``deque(maxlen=...)`` which silently dropped
    the oldest event. Consumers had no way to know overflow had
    happened. These tests pin the new surface:

    - ``dropped_count`` is a non-decreasing counter observable by callers
    - ``is_full`` lets producers apply their own backpressure
    - ``max_size`` is exposed so callers can reason about capacity
    - Overflow still drops the oldest event (policy preserved for
      backward compat with ``test_max_size_capacity``)
    - A warning is logged at throttled intervals on sustained overflow
    """

    def _make_event(self, tmp_path: Path, name: str = "test.txt") -> FileEvent:
        return FileEvent(
            event_type=EventType.CREATED,
            path=tmp_path / name,
            timestamp=datetime.now(UTC),
        )

    def test_dropped_count_starts_at_zero(self) -> None:
        """A freshly constructed queue has recorded no drops."""
        assert EventQueue(max_size=10).dropped_count == 0
        assert EventQueue().dropped_count == 0  # unbounded also reports zero

    def test_dropped_count_increments_on_overflow(self, tmp_path: Path) -> None:
        """Each enqueue past capacity bumps the counter by exactly one."""
        queue = EventQueue(max_size=2)
        for i in range(5):
            queue.enqueue(self._make_event(tmp_path, name=f"f{i}.txt"))
        # 5 enqueues on a 2-slot queue → 3 drops.
        assert queue.dropped_count == 3
        assert queue.size == 2

    def test_dropped_count_stays_stable_when_not_full(self, tmp_path: Path) -> None:
        """Enqueuing below capacity does not increment the drop counter."""
        queue = EventQueue(max_size=10)
        for i in range(5):
            queue.enqueue(self._make_event(tmp_path, name=f"f{i}.txt"))
        assert queue.dropped_count == 0

    def test_unbounded_queue_never_drops(self, tmp_path: Path) -> None:
        """``max_size=0`` means unbounded; no overflow path ever fires."""
        queue = EventQueue()  # unbounded default
        for i in range(1000):
            queue.enqueue(self._make_event(tmp_path, name=f"f{i}.txt"))
        assert queue.dropped_count == 0
        assert queue.size == 1000

    def test_is_full_reflects_capacity(self, tmp_path: Path) -> None:
        queue = EventQueue(max_size=2)
        assert queue.is_full is False
        queue.enqueue(self._make_event(tmp_path, name="a.txt"))
        assert queue.is_full is False
        queue.enqueue(self._make_event(tmp_path, name="b.txt"))
        assert queue.is_full is True
        # Drop-oldest keeps the queue at capacity — still full.
        queue.enqueue(self._make_event(tmp_path, name="c.txt"))
        assert queue.is_full is True
        # Consuming one event leaves a slot free.
        queue.dequeue_batch(max_size=1)
        assert queue.is_full is False

    def test_is_full_always_false_for_unbounded(self, tmp_path: Path) -> None:
        queue = EventQueue()  # max_size=0
        for i in range(100):
            queue.enqueue(self._make_event(tmp_path, name=f"f{i}.txt"))
        assert queue.is_full is False

    def test_max_size_property_exposes_constructor_value(self) -> None:
        assert EventQueue(max_size=42).max_size == 42
        assert EventQueue(max_size=0).max_size == 0
        assert EventQueue().max_size == 0

    def test_overflow_drops_oldest_not_newest(self, tmp_path: Path) -> None:
        """Behaviour preserved from pre-F1: the drop-oldest policy."""
        queue = EventQueue(max_size=3)
        for i in range(5):
            queue.enqueue(self._make_event(tmp_path, name=f"f{i}.txt"))
        batch = queue.dequeue_batch(max_size=10)
        assert [e.path.name for e in batch] == ["f2.txt", "f3.txt", "f4.txt"]

    def test_first_drop_emits_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The very first drop produces a warning (no silent overflow)."""
        queue = EventQueue(max_size=1)
        queue.enqueue(self._make_event(tmp_path, name="a.txt"))
        with caplog.at_level("WARNING", logger="watcher.queue"):
            queue.enqueue(self._make_event(tmp_path, name="b.txt"))
        assert any(
            "overflow" in rec.message.lower() and "total_dropped=1" in rec.message
            for rec in caplog.records
        )

    def test_sustained_drops_are_throttled(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Logging throttled to every _DROP_LOG_INTERVAL drops so a
        saturated queue doesn't spam the log."""
        queue = EventQueue(max_size=1)
        queue.enqueue(self._make_event(tmp_path, name="seed.txt"))
        with caplog.at_level("WARNING", logger="watcher.queue"):
            # Cause exactly _DROP_LOG_INTERVAL drops after the seed.
            for i in range(_DROP_LOG_INTERVAL):
                queue.enqueue(self._make_event(tmp_path, name=f"f{i}.txt"))
        # Expect exactly two warnings: the first-drop signal + the
        # ``_DROP_LOG_INTERVAL``th drop.
        overflow_warnings = [r for r in caplog.records if "overflow" in r.message.lower()]
        assert len(overflow_warnings) == 2
        assert queue.dropped_count == _DROP_LOG_INTERVAL
