"""Redis Streams event system for the file organizer.

Provides event-driven architecture support using Redis Streams
for publishing and consuming file organization events.

Example:
    >>> from file_organizer.events import EventPublisher, EventType
    >>> publisher = EventPublisher()
    >>> publisher.connect()
    >>> publisher.publish_file_event(
    ...     EventType.FILE_CREATED, "/path/to/file.txt"
    ... )
"""
from __future__ import annotations


from file_organizer.events.config import EventConfig
from file_organizer.events.consumer import EventConsumer
from file_organizer.events.publisher import EventPublisher
from file_organizer.events.stream import Event, RedisStreamManager
from file_organizer.events.types import EventType, FileEvent, ScanEvent

__all__ = [
    "Event",
    "EventConfig",
    "EventConsumer",
    "EventPublisher",
    "EventType",
    "FileEvent",
    "RedisStreamManager",
    "ScanEvent",
]
