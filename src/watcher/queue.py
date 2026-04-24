"""Event queue for batching file system events.

Provides a thread-safe queue that collects FileEvent instances and
supports batch dequeuing for efficient processing.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from _compat import StrEnum

logger = logging.getLogger(__name__)

# Log a throttled warning every ``_DROP_LOG_INTERVAL`` drops so the signal
# is visible without spamming at sustained overflow rates.
_DROP_LOG_INTERVAL = 100


class EventType(StrEnum):
    """Types of file system events."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"


@dataclass(frozen=True)
class FileEvent:
    """Represents a single file system event.

    Attributes:
        event_type: The type of file system event.
        path: The path of the affected file or directory.
        timestamp: When the event occurred.
        is_directory: Whether the event target is a directory.
        dest_path: Destination path for move events, None otherwise.
    """

    event_type: EventType
    path: Path
    timestamp: datetime
    is_directory: bool = False
    dest_path: Path | None = None


class EventQueue:
    """Thread-safe queue for file system events with batch dequeue support.

    Events are enqueued individually and can be dequeued in configurable
    batch sizes for efficient downstream processing.

    F1 (hardening roadmap #159): overflow is no longer silent. When the
    queue is full, the oldest event is dropped (preserving the pre-F1
    behaviour), a throttled warning is logged, and
    :attr:`dropped_count` is incremented so callers can observe
    saturation. The :attr:`is_full` property lets producers apply
    their own backpressure (e.g. coalesce events or skip debouncing)
    before enqueue.

    Attributes:
        max_size: Maximum number of events the queue can hold.
            When exceeded, oldest events are dropped. 0 means unlimited.
    """

    def __init__(self, max_size: int = 0) -> None:
        """Initialize the event queue.

        Args:
            max_size: Maximum queue capacity. 0 means unlimited.
        """
        # No ``maxlen`` here: overflow is handled explicitly in
        # ``enqueue`` so we can count and log drops instead of silently
        # losing events. Pre-F1 used ``deque(maxlen=...)``.
        self._queue: deque[FileEvent] = deque()
        self._max_size = max_size if max_size > 0 else 0
        self._dropped_count = 0
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)

    def enqueue(self, event: FileEvent) -> None:
        """Add an event to the queue.

        Thread-safe. If the queue is at max capacity, the oldest event
        is dropped to make room (drop-oldest policy). Each drop
        increments :attr:`dropped_count`, and every
        :data:`_DROP_LOG_INTERVAL` drops emit a throttled warning so
        sustained overflow is observable without flooding the log.

        Args:
            event: The file event to enqueue.
        """
        with self._not_empty:
            if self._max_size > 0 and len(self._queue) >= self._max_size:
                self._queue.popleft()
                self._dropped_count += 1
                if self._dropped_count == 1 or self._dropped_count % _DROP_LOG_INTERVAL == 0:
                    logger.warning(
                        "EventQueue overflow at max_size=%d; dropped oldest event "
                        "(total_dropped=%d). Consider increasing max_size or "
                        "slowing the event producer.",
                        self._max_size,
                        self._dropped_count,
                    )
            self._queue.append(event)
            self._not_empty.notify()

    def dequeue_batch(self, max_size: int = 10) -> list[FileEvent]:
        """Remove and return up to max_size events from the queue.

        Returns immediately with whatever events are available (may be
        fewer than max_size, or an empty list if the queue is empty).

        Args:
            max_size: Maximum number of events to return.

        Returns:
            A list of FileEvent instances, up to max_size.
        """
        with self._lock:
            batch: list[FileEvent] = []
            count = min(max_size, len(self._queue))
            for _ in range(count):
                batch.append(self._queue.popleft())
            return batch

    def dequeue_batch_blocking(
        self, max_size: int = 10, timeout: float | None = None
    ) -> list[FileEvent]:
        """Remove and return up to max_size events, blocking if empty.

        Waits until at least one event is available (or timeout expires),
        then returns up to max_size events.

        Args:
            max_size: Maximum number of events to return.
            timeout: Maximum seconds to wait. None means wait forever.

        Returns:
            A list of FileEvent instances. Empty list if timeout expired
            with no events available.
        """
        with self._not_empty:
            if len(self._queue) == 0:
                self._not_empty.wait(timeout=timeout)

            batch: list[FileEvent] = []
            count = min(max_size, len(self._queue))
            for _ in range(count):
                batch.append(self._queue.popleft())
            return batch

    def peek(self) -> FileEvent | None:
        """Return the next event without removing it.

        Returns:
            The next FileEvent, or None if the queue is empty.
        """
        with self._lock:
            if self._queue:
                return self._queue[0]
            return None

    def clear(self) -> int:
        """Remove all events from the queue.

        Returns:
            The number of events that were removed.
        """
        with self._lock:
            count = len(self._queue)
            self._queue.clear()
            return count

    @property
    def size(self) -> int:
        """Return the current number of events in the queue."""
        with self._lock:
            return len(self._queue)

    @property
    def is_empty(self) -> bool:
        """Return True if the queue contains no events."""
        with self._lock:
            return len(self._queue) == 0

    @property
    def is_full(self) -> bool:
        """Return True if a bounded queue is at capacity.

        Producers can check this before enqueue to apply their own
        backpressure — e.g. coalescing events, skipping debouncing, or
        logging at the producer side. Always False for an unbounded
        queue (``max_size == 0``).
        """
        with self._lock:
            return self._max_size > 0 and len(self._queue) >= self._max_size

    @property
    def dropped_count(self) -> int:
        """Number of events dropped due to overflow since construction.

        F1 metric: non-decreasing counter of drop events. A rising value
        indicates the queue is under sustained pressure; a stable value
        means overflow has stopped (even if it was hit earlier).
        """
        with self._lock:
            return self._dropped_count

    @property
    def max_size(self) -> int:
        """The configured maximum size (0 for unbounded)."""
        return self._max_size
