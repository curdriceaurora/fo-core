"""File system event handler with debouncing and filtering.

Extends watchdog's FileSystemEventHandler to add debounce logic,
configurable pattern filtering, and event queuing for batch processing.
"""

from __future__ import annotations

import heapq
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)

from .config import WatcherConfig
from .queue import EventQueue, EventType, FileEvent

logger = logging.getLogger(__name__)

# F3 (hardening roadmap #159): debounce-dict eviction tunables.
#
# ``_STALE_MULTIPLIER``: entries older than ``debounce_seconds *
# _STALE_MULTIPLIER`` are dropped on every ``_should_process`` call —
# after that horizon the entry no longer gates anything, so keeping it
# only wastes memory.
#
# ``_MIN_EVICTION_HORIZON_S``: floor on the stale horizon. Without it,
# a ``debounce_seconds=0.0`` config produces a zero stale horizon and
# evicts every entry (including the one we just wrote). The 60s floor
# means eviction kicks in no sooner than one minute regardless of
# config — enough for any realistic debouncing, loose enough to avoid
# thrashing the lock on busy watchers.
#
# ``_MAX_DEBOUNCE_ENTRIES``: hard ceiling against pathological cases
# (e.g. very large debounce_seconds making the age horizon enormous).
# When exceeded, the oldest entries are dropped in bulk.
_STALE_MULTIPLIER = 10
_MIN_EVICTION_HORIZON_S = 60.0
_MAX_DEBOUNCE_ENTRIES = 10_000


class FileEventHandler(FileSystemEventHandler):
    """Watchdog event handler with debouncing, filtering, and queue integration.

    Receives raw file system events from watchdog observers, applies
    configurable filtering (exclude patterns, file type whitelist),
    debounces rapid successive events on the same file, and enqueues
    the deduplicated events for batch processing.

    Attributes:
        config: Watcher configuration controlling filter and debounce behavior.
        queue: Event queue where processed events are placed.
    """

    def __init__(self, config: WatcherConfig, queue: EventQueue) -> None:
        """Initialize the event handler.

        Args:
            config: Watcher configuration for filtering and debouncing.
            queue: Event queue for downstream processing.
        """
        super().__init__()
        self.config = config
        self.queue = queue

        # Debounce state: maps file path -> last event timestamp (monotonic)
        self._last_event_times: dict[str, float] = {}
        self._debounce_lock = threading.Lock()
        # F3: latched flag so the hard-cap warning emits at most once
        # per breach episode. Without this, a sustained traffic pattern
        # keeping the dict at cap+1 would log a WARNING on every single
        # event, burying other logs. Reset to False as soon as the dict
        # drops back under the cap so a later breach logs again.
        self._debounce_cap_warned = False

        # Callback hooks (optional, for direct notification without queue)
        self._on_created_callbacks: list[Callable[..., object]] = []
        self._on_modified_callbacks: list[Callable[..., object]] = []
        self._on_deleted_callbacks: list[Callable[..., object]] = []
        self._on_moved_callbacks: list[Callable[..., object]] = []

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file/directory creation events.

        Args:
            event: The watchdog creation event.
        """
        self._handle_event(event, EventType.CREATED)

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification events.

        Args:
            event: The watchdog modification event.
        """
        self._handle_event(event, EventType.MODIFIED)

    def on_deleted(self, event: FileSystemEvent) -> None:
        """Handle file/directory deletion events.

        Args:
            event: The watchdog deletion event.
        """
        self._handle_event(event, EventType.DELETED)

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle file/directory move events.

        Args:
            event: The watchdog move event.
        """
        dest_path: Path | None = None
        raw_dest = getattr(event, "dest_path", None)
        if raw_dest is not None:
            dest_path = Path(os.fsdecode(raw_dest))
        self._handle_event(event, EventType.MOVED, dest_path=dest_path)

    def register_callback(
        self,
        event_type: EventType,
        callback: Callable[..., object],
    ) -> None:
        """Register a callback for a specific event type.

        Callbacks are invoked after filtering and debouncing, in addition
        to the event being placed on the queue.

        Args:
            event_type: The event type to listen for.
            callback: A callable that accepts a single FileEvent argument.
        """
        callbacks_map = {
            EventType.CREATED: self._on_created_callbacks,
            EventType.MODIFIED: self._on_modified_callbacks,
            EventType.DELETED: self._on_deleted_callbacks,
            EventType.MOVED: self._on_moved_callbacks,
        }
        callbacks_map[event_type].append(callback)

    def _handle_event(
        self,
        event: FileSystemEvent,
        event_type: EventType,
        dest_path: Path | None = None,
    ) -> None:
        """Central event processing pipeline: filter, debounce, enqueue.

        Args:
            event: The raw watchdog event.
            event_type: Classified event type.
            dest_path: Destination path for move events.
        """
        raw_src = event.src_path
        path = Path(os.fsdecode(raw_src))
        is_directory = isinstance(event, (DirCreatedEvent, DirDeletedEvent, DirMovedEvent))

        # Skip directory events for non-directory-aware processing
        # (still allow them through if they pass filters)
        if not is_directory and not self.config.should_include_file(path):
            logger.debug("Filtered out event for: %s", path)
            return

        # Apply debouncing
        if not self._should_process(str(path)):
            logger.debug("Debounced event for: %s", path)
            return

        # Create the FileEvent
        file_event = FileEvent(
            event_type=event_type,
            path=path,
            timestamp=datetime.now(UTC),
            is_directory=is_directory,
            dest_path=dest_path,
        )

        # Enqueue
        self.queue.enqueue(file_event)
        logger.info("Queued %s event for: %s", event_type.value, path)

        # Fire callbacks
        self._fire_callbacks(event_type, file_event)

    def _should_process(self, path_key: str) -> bool:
        """Check if an event for this path should be processed based on debounce timing.

        Uses monotonic time for reliable interval measurement regardless
        of system clock adjustments.

        F3 (hardening roadmap #159): evicts stale debounce entries on
        every call. Pre-F3 the ``_last_event_times`` dict grew
        unbounded — a long-running daemon that observed many distinct
        paths leaked memory indefinitely. Post-F3 the dict is capped
        in two ways:

        1. Any entry older than ``debounce_seconds * _STALE_MULTIPLIER``
           is dropped (it no longer gates anything because the window
           has long since expired).
        2. If the dict exceeds ``_MAX_DEBOUNCE_ENTRIES`` the oldest
           entries are dropped in bulk — a hard ceiling against
           pathological cases where the eviction heuristic doesn't keep
           up (e.g. debounce_seconds set very high).

        Args:
            path_key: String representation of the file path.

        Returns:
            True if the event should be processed, False if within debounce window.
        """
        now = time.monotonic()

        with self._debounce_lock:
            self._evict_stale_debounce_entries_locked(now)

            last_time = self._last_event_times.get(path_key)

            if last_time is not None:
                elapsed = now - last_time
                if elapsed < self.config.debounce_seconds:
                    return False

            self._last_event_times[path_key] = now
            return True

    def _evict_stale_debounce_entries_locked(self, now: float) -> None:
        """Drop debounce entries that are older than the stale horizon.

        Called from inside ``_should_process`` under ``_debounce_lock``.
        Split into a helper so tests can assert eviction policy without
        going through the full debounce machinery.
        """
        stale_horizon = max(
            self.config.debounce_seconds * _STALE_MULTIPLIER,
            _MIN_EVICTION_HORIZON_S,
        )
        expired = [
            key
            for key, last_time in self._last_event_times.items()
            if (now - last_time) > stale_horizon
        ]
        for key in expired:
            del self._last_event_times[key]

        # Hard ceiling against pathological growth (e.g. very large
        # ``debounce_seconds`` making ``stale_horizon`` huge). Drop the
        # oldest entries in bulk until under the cap.
        if len(self._last_event_times) > _MAX_DEBOUNCE_ENTRIES:
            surplus = len(self._last_event_times) - _MAX_DEBOUNCE_ENTRIES
            # heapq.nsmallest runs in O(N log surplus) — the full
            # ``sorted`` call was O(N log N) and ran under
            # ``_debounce_lock`` on every filesystem event while the
            # dict sat at cap+1. ``surplus`` is typically 1 in steady
            # state, so this is a large constant-factor win on busy
            # watchers.
            oldest = heapq.nsmallest(surplus, self._last_event_times.items(), key=lambda kv: kv[1])
            for key, _ in oldest:
                del self._last_event_times[key]
            # One-shot log per breach: suppress repeats until the dict
            # drops back under the cap. Operators still see the first
            # warning; the subsequent eviction storm is silent.
            if not self._debounce_cap_warned:
                logger.warning(
                    "Debounce dict exceeded %d entries; dropping oldest in bulk "
                    "(further occurrences suppressed until cap is no longer hit).",
                    _MAX_DEBOUNCE_ENTRIES,
                )
                self._debounce_cap_warned = True
        else:
            self._debounce_cap_warned = False

    def _fire_callbacks(self, event_type: EventType, file_event: FileEvent) -> None:
        """Invoke registered callbacks for the given event type.

        Args:
            event_type: The type of event that occurred.
            file_event: The processed file event to pass to callbacks.
        """
        callbacks_map = {
            EventType.CREATED: self._on_created_callbacks,
            EventType.MODIFIED: self._on_modified_callbacks,
            EventType.DELETED: self._on_deleted_callbacks,
            EventType.MOVED: self._on_moved_callbacks,
        }

        for callback in callbacks_map.get(event_type, []):
            try:
                callback(file_event)
            except Exception:
                logger.exception("Error in %s callback for %s", event_type.value, file_event.path)

    def clear_debounce_state(self) -> None:
        """Clear all debounce tracking state."""
        with self._debounce_lock:
            self._last_event_times.clear()

    @property
    def pending_paths(self) -> int:
        """Return the number of paths being tracked for debouncing."""
        with self._debounce_lock:
            return len(self._last_event_times)
