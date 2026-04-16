"""Redis Streams event system for the file organizer.

Provides event-driven architecture support using Redis Streams
for publishing and consuming file organization events, with a
pub/sub layer for topic-based routing, middleware support,
event replay, monitoring, audit logging, service bus, service
discovery, and health checking capabilities.

Example:
    >>> from events import EventPublisher, EventType
    >>> publisher = EventPublisher()
    >>> publisher.connect()
    >>> publisher.publish_file_event(
    ...     EventType.FILE_CREATED, "/path/to/file.txt"
    ... )

    >>> from events import PubSubManager
    >>> pubsub = PubSubManager()
    >>> pubsub.connect()
    >>> pubsub.subscribe("file.*", lambda data: print(data))
    >>> pubsub.publish("file.created", {"path": "/tmp/hello.txt"})

    >>> from events import ServiceBus
    >>> bus = ServiceBus(name="gateway")
    >>> bus.register_service("echo", lambda req: {"echo": req.payload})
"""

from __future__ import annotations

from events.audit import AuditEntry, AuditFilter, AuditLogger
from events.config import EventConfig
from events.consumer import EventConsumer
from events.discovery import ServiceDiscovery, ServiceInfo
from events.health import HealthChecker, HealthStatus, ServiceHealth
from events.middleware import (
    LoggingMiddleware,
    MetricsMiddleware,
    MiddlewarePipeline,
    RetryMiddleware,
)
from events.monitor import ConsumerLag, EventMonitor, StreamStats
from events.publisher import EventPublisher
from events.pubsub import PubSubManager
from events.replay import EventReplayManager, ReplayConfig
from events.service_bus import ServiceBus, ServiceRequest, ServiceResponse
from events.stream import Event, RedisStreamManager
from events.subscription import Subscription, SubscriptionRegistry
from events.types import EventType, FileEvent, ScanEvent

__all__ = [
    "AuditEntry",
    "AuditFilter",
    "AuditLogger",
    "ConsumerLag",
    "Event",
    "EventConfig",
    "EventConsumer",
    "EventMonitor",
    "EventPublisher",
    "EventReplayManager",
    "EventType",
    "FileEvent",
    "HealthChecker",
    "HealthStatus",
    "LoggingMiddleware",
    "MetricsMiddleware",
    "MiddlewarePipeline",
    "PubSubManager",
    "RedisStreamManager",
    "ReplayConfig",
    "RetryMiddleware",
    "ScanEvent",
    "ServiceBus",
    "ServiceDiscovery",
    "ServiceHealth",
    "ServiceInfo",
    "ServiceRequest",
    "ServiceResponse",
    "StreamStats",
    "Subscription",
    "SubscriptionRegistry",
]
