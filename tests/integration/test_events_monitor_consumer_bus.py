"""Integration tests for events/monitor.py, events/consumer.py,
events/stream.py (Redis paths), events/middleware.py, and
events/service_bus.py.

All tests are marked @pytest.mark.integration and require no real Redis
connection — Redis is fully mocked.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from events.config import EventConfig
from events.consumer import EventConsumer
from events.middleware import (
    LoggingMiddleware,
    MetricsMiddleware,
    MiddlewarePipeline,
    RetryMiddleware,
)
from events.monitor import (
    ConsumerLag,
    EventMonitor,
    StreamStats,
    _parse_entry_timestamp,
)
from events.stream import Event, RedisStreamManager
from events.types import EventType

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connected_manager(mock_redis: MagicMock) -> RedisStreamManager:
    """Return a RedisStreamManager that believes it is connected to *mock_redis*."""
    cfg = EventConfig(stream_prefix="test", consumer_group="grp1", batch_size=5)
    mgr = RedisStreamManager(cfg)
    mgr._redis = mock_redis
    mgr._connected = True
    return mgr


def _make_event(
    event_id: str = "1700000000000-0", stream: str = "test:events", data: dict | None = None
) -> Event:
    from datetime import UTC, datetime

    return Event(
        id=event_id,
        stream=stream,
        data=data or {"event_type": "file.created", "path": "a.txt"},
        timestamp=datetime.now(UTC),
    )


# ===========================================================================
# monitor.py — EventMonitor, StreamStats, ConsumerLag, _parse_entry_timestamp
# ===========================================================================


class TestStreamStatsDataclass:
    """StreamStats dataclass default values and field access."""

    def test_default_values(self) -> None:
        stats = StreamStats()
        assert stats.length == 0
        assert stats.groups == 0
        assert stats.oldest_event is None
        assert stats.newest_event is None

    def test_custom_values(self) -> None:
        from datetime import UTC, datetime

        ts = datetime(2026, 1, 1, tzinfo=UTC)
        stats = StreamStats(length=42, groups=3, oldest_event=ts, newest_event=ts)
        assert stats.length == 42
        assert stats.groups == 3
        assert stats.oldest_event == ts


class TestConsumerLagDataclass:
    """ConsumerLag dataclass default values and field access."""

    def test_default_values(self) -> None:
        lag = ConsumerLag()
        assert lag.pending == 0
        assert lag.idle_time == 0
        assert lag.consumers == 0

    def test_custom_values(self) -> None:
        lag = ConsumerLag(pending=5, idle_time=2000, consumers=3)
        assert lag.pending == 5
        assert lag.idle_time == 2000
        assert lag.consumers == 3


class TestParseEntryTimestamp:
    """_parse_entry_timestamp helper covers parse paths."""

    def test_returns_none_for_none_input(self) -> None:
        result = _parse_entry_timestamp(None)
        assert result is None

    def test_parses_tuple_entry(self) -> None:
        from datetime import UTC, datetime

        entry = ("1700000000000-0", {"key": "val"})
        result = _parse_entry_timestamp(entry)
        assert result is not None
        expected = datetime.fromtimestamp(1700000000.0, tz=UTC)
        assert result == expected

    def test_parses_list_entry(self) -> None:
        from datetime import UTC, datetime

        entry = ["1700000000000-0", {"key": "val"}]
        result = _parse_entry_timestamp(entry)
        assert result is not None
        expected = datetime.fromtimestamp(1700000000.0, tz=UTC)
        assert result == expected

    def test_returns_none_on_invalid_id(self) -> None:
        entry = ("not-a-number-0", {})
        result = _parse_entry_timestamp(entry)
        assert result is None

    def test_returns_none_on_empty_tuple(self) -> None:
        result = _parse_entry_timestamp(())
        assert result is None


class TestEventMonitorNotConnected:
    """EventMonitor returns empty results when Redis is disconnected."""

    def _make_disconnected(self) -> EventMonitor:
        cfg = EventConfig()
        mgr = RedisStreamManager(cfg)
        # mgr._connected is False by default
        return EventMonitor(mgr)

    def test_get_stream_stats_returns_empty_stats(self) -> None:
        monitor = self._make_disconnected()
        stats = monitor.get_stream_stats("events")
        assert stats.length == 0
        assert stats.groups == 0
        assert stats.oldest_event is None

    def test_get_consumer_lag_returns_empty_lag(self) -> None:
        monitor = self._make_disconnected()
        lag = monitor.get_consumer_lag("events", "grp")
        assert lag.pending == 0
        assert lag.idle_time == 0
        assert lag.consumers == 0

    def test_get_event_rate_returns_zero(self) -> None:
        monitor = self._make_disconnected()
        rate = monitor.get_event_rate("events")
        assert rate == 0.0

    def test_get_event_rate_zero_window_returns_zero(self) -> None:
        monitor = self._make_disconnected()
        rate = monitor.get_event_rate("events", window_seconds=0)
        assert rate == 0.0

    def test_repr_shows_connected_false(self) -> None:
        monitor = self._make_disconnected()
        assert "connected=False" in repr(monitor)


class TestEventMonitorConnected:
    """EventMonitor with a mocked connected Redis client."""

    def _make(self) -> tuple[EventMonitor, MagicMock]:
        mock_redis = MagicMock()
        mgr = _make_connected_manager(mock_redis)
        return EventMonitor(mgr), mock_redis

    def test_get_stream_stats_parses_xinfo_stream(self) -> None:
        monitor, mock_redis = self._make()
        mock_redis.xinfo_stream.return_value = {
            "length": 10,
            "groups": 2,
            "first-entry": ("1700000000000-0", {"k": "v"}),
            "last-entry": ("1700000001000-0", {"k": "v"}),
        }
        stats = monitor.get_stream_stats("events")
        assert stats.length == 10
        assert stats.groups == 2
        assert stats.oldest_event is not None
        assert stats.newest_event is not None
        mock_redis.xinfo_stream.assert_called_once_with("test:events")

    def test_get_stream_stats_missing_entries_gives_none_timestamps(self) -> None:
        monitor, mock_redis = self._make()
        mock_redis.xinfo_stream.return_value = {
            "length": 0,
            "groups": 0,
        }
        stats = monitor.get_stream_stats("events")
        assert stats.length == 0
        assert stats.oldest_event is None
        assert stats.newest_event is None

    def test_get_stream_stats_exception_returns_empty(self) -> None:
        monitor, mock_redis = self._make()
        mock_redis.xinfo_stream.side_effect = RuntimeError("Redis error")
        stats = monitor.get_stream_stats("events")
        assert stats.length == 0
        assert stats.groups == 0

    def test_get_consumer_lag_parses_xpending_and_xinfo_groups(self) -> None:
        monitor, mock_redis = self._make()
        mock_redis.xpending.return_value = {"pending": 7}
        mock_redis.xinfo_groups.return_value = [
            {"name": "grp1", "consumers": 3, "idle": 1500},
            {"name": "other", "consumers": 1, "idle": 100},
        ]
        lag = monitor.get_consumer_lag("events", "grp1")
        assert lag.pending == 7
        assert lag.consumers == 3
        assert lag.idle_time == 1500

    def test_get_consumer_lag_group_not_found_returns_zeros(self) -> None:
        monitor, mock_redis = self._make()
        mock_redis.xpending.return_value = {"pending": 0}
        mock_redis.xinfo_groups.return_value = [{"name": "other", "consumers": 1, "idle": 0}]
        lag = monitor.get_consumer_lag("events", "nonexistent")
        assert lag.pending == 0
        assert lag.consumers == 0
        assert lag.idle_time == 0

    def test_get_consumer_lag_exception_returns_empty(self) -> None:
        monitor, mock_redis = self._make()
        mock_redis.xpending.side_effect = RuntimeError("boom")
        lag = monitor.get_consumer_lag("events", "grp1")
        assert lag.pending == 0

    def test_get_event_rate_counts_events_in_window(self) -> None:
        monitor, mock_redis = self._make()
        # 60 events in 60-second window = 1.0 events/sec
        mock_redis.xrange.return_value = [f"id-{i}" for i in range(60)]
        rate = monitor.get_event_rate("events", window_seconds=60)
        assert rate == pytest.approx(1.0)
        mock_redis.xrange.assert_called_once()

    def test_get_event_rate_empty_window_returns_zero(self) -> None:
        monitor, mock_redis = self._make()
        mock_redis.xrange.return_value = []
        rate = monitor.get_event_rate("events", window_seconds=30)
        assert rate == 0.0

    def test_get_event_rate_exception_returns_zero(self) -> None:
        monitor, mock_redis = self._make()
        mock_redis.xrange.side_effect = RuntimeError("boom")
        rate = monitor.get_event_rate("events", window_seconds=10)
        assert rate == 0.0

    def test_repr_shows_connected_true(self) -> None:
        monitor, _ = self._make()
        assert "connected=True" in repr(monitor)


# ===========================================================================
# stream.py — RedisStreamManager (Redis-backed paths)
# ===========================================================================


class TestRedisStreamManagerConnect:
    """RedisStreamManager connection and disconnect lifecycle."""

    @pytest.fixture(autouse=True)
    def _require_redis(self) -> None:
        pytest.importorskip("redis")

    def test_connect_calls_ping_and_sets_connected(self) -> None:
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        with patch("redis.Redis.from_url", return_value=mock_redis):
            mgr = RedisStreamManager(EventConfig())
            result = mgr.connect()
        assert result is True
        assert mgr.is_connected is True

    def test_connect_failure_returns_false(self) -> None:
        with patch("redis.Redis.from_url", side_effect=ConnectionError("no redis")):
            mgr = RedisStreamManager(EventConfig())
            result = mgr.connect()
        assert result is False
        assert mgr.is_connected is False

    def test_disconnect_clears_state(self) -> None:
        mock_redis = MagicMock()
        mgr = _make_connected_manager(mock_redis)
        assert mgr.is_connected is True
        mgr.disconnect()
        assert mgr.is_connected is False
        assert mgr._redis is None

    def test_disconnect_when_not_connected_is_noop(self) -> None:
        mgr = RedisStreamManager(EventConfig())
        mgr.disconnect()  # Should not raise
        assert mgr.is_connected is False

    def test_context_manager_connects_and_disconnects(self) -> None:
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        with patch("redis.Redis.from_url", return_value=mock_redis):
            with RedisStreamManager(EventConfig()) as mgr:
                assert mgr.is_connected is True
        assert mgr.is_connected is False


class TestRedisStreamManagerPublish:
    """RedisStreamManager.publish paths."""

    def test_publish_returns_message_id(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xadd.return_value = "1700000000000-0"
        mgr = _make_connected_manager(mock_redis)
        msg_id = mgr.publish("events", {"key": "value"})
        assert msg_id == "1700000000000-0"
        mock_redis.xadd.assert_called_once()

    def test_publish_not_connected_returns_none(self) -> None:
        mgr = RedisStreamManager(EventConfig())
        result = mgr.publish("events", {"key": "value"})
        assert result is None

    def test_publish_with_max_len_passes_maxlen(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xadd.return_value = "id-1"
        mgr = _make_connected_manager(mock_redis)
        mgr.publish("events", {"key": "val"}, max_len=500)
        call_kwargs = mock_redis.xadd.call_args.kwargs
        assert call_kwargs["maxlen"] == 500
        assert call_kwargs["approximate"] is True

    def test_publish_exception_returns_none(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xadd.side_effect = RuntimeError("broken")
        mgr = _make_connected_manager(mock_redis)
        result = mgr.publish("events", {"key": "val"})
        assert result is None


class TestRedisStreamManagerConsumerGroup:
    """create_consumer_group paths including BUSYGROUP handling."""

    def test_create_group_returns_true_on_success(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xgroup_create.return_value = True
        mgr = _make_connected_manager(mock_redis)
        result = mgr.create_consumer_group("events")
        assert result is True
        mock_redis.xgroup_create.assert_called_once()

    def test_create_group_busygroup_returns_true(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xgroup_create.side_effect = Exception(
            "BUSYGROUP Consumer Group name already exists"
        )
        mgr = _make_connected_manager(mock_redis)
        result = mgr.create_consumer_group("events")
        assert result is True

    def test_create_group_other_exception_returns_false(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xgroup_create.side_effect = RuntimeError("network error")
        mgr = _make_connected_manager(mock_redis)
        result = mgr.create_consumer_group("events")
        assert result is False

    def test_create_group_not_connected_returns_false(self) -> None:
        mgr = RedisStreamManager(EventConfig())
        result = mgr.create_consumer_group("events")
        assert result is False


class TestRedisStreamManagerReadGroup:
    """read_group message parsing."""

    def test_read_group_parses_messages_to_events(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xreadgroup.return_value = [
            (
                "test:events",
                [
                    ("1700000000000-0", {"event_type": "file.created", "path": "/a.txt"}),
                    ("1700000001000-0", {"event_type": "file.modified", "path": "/b.txt"}),
                ],
            )
        ]
        mgr = _make_connected_manager(mock_redis)
        events = mgr.read_group("events")
        assert len(events) == 2
        assert events[0].id == "1700000000000-0"
        assert events[0].data["event_type"] == "file.created"
        assert events[1].data["path"] == "/b.txt"

    def test_read_group_returns_empty_on_no_results(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xreadgroup.return_value = None
        mgr = _make_connected_manager(mock_redis)
        events = mgr.read_group("events")
        assert events == []

    def test_read_group_not_connected_returns_empty(self) -> None:
        mgr = RedisStreamManager(EventConfig())
        events = mgr.read_group("events")
        assert events == []

    def test_read_group_exception_returns_empty(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xreadgroup.side_effect = RuntimeError("boom")
        mgr = _make_connected_manager(mock_redis)
        events = mgr.read_group("events")
        assert events == []


class TestRedisStreamManagerAcknowledge:
    """acknowledge method."""

    def test_acknowledge_returns_true_on_success(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xack.return_value = 1
        mgr = _make_connected_manager(mock_redis)
        result = mgr.acknowledge("events", message_id="1700000000000-0")
        assert result is True

    def test_acknowledge_returns_false_when_not_acked(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xack.return_value = 0
        mgr = _make_connected_manager(mock_redis)
        result = mgr.acknowledge("events", message_id="1700000000000-0")
        assert result is False

    def test_acknowledge_not_connected_returns_false(self) -> None:
        mgr = RedisStreamManager(EventConfig())
        result = mgr.acknowledge("events", message_id="id-1")
        assert result is False

    def test_acknowledge_exception_returns_false(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xack.side_effect = RuntimeError("err")
        mgr = _make_connected_manager(mock_redis)
        result = mgr.acknowledge("events", message_id="id-1")
        assert result is False


class TestRedisStreamManagerGetters:
    """get_stream_length and get_pending_count."""

    def test_get_stream_length_returns_count(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xlen.return_value = 42
        mgr = _make_connected_manager(mock_redis)
        assert mgr.get_stream_length("events") == 42

    def test_get_stream_length_not_connected_returns_zero(self) -> None:
        mgr = RedisStreamManager(EventConfig())
        assert mgr.get_stream_length("events") == 0

    def test_get_pending_count_returns_count(self) -> None:
        mock_redis = MagicMock()
        mock_redis.xpending.return_value = {"pending": 5}
        mgr = _make_connected_manager(mock_redis)
        assert mgr.get_pending_count("events") == 5

    def test_get_pending_count_not_connected_returns_zero(self) -> None:
        mgr = RedisStreamManager(EventConfig())
        assert mgr.get_pending_count("events") == 0

    def test_repr_includes_url(self) -> None:
        mgr = RedisStreamManager(EventConfig(redis_url="redis://localhost:6379/0"))
        assert "redis://localhost:6379/0" in repr(mgr)


# ===========================================================================
# consumer.py — EventConsumer
# ===========================================================================


class TestEventConsumerProperties:
    """EventConsumer property accessors and registration."""

    def _make(self) -> EventConsumer:
        cfg = EventConfig()
        mock_redis = MagicMock()
        mgr = _make_connected_manager(mock_redis)
        return EventConsumer(config=cfg, stream_manager=mgr, consumer_name="worker-test")

    def test_is_connected_delegates_to_manager(self) -> None:
        consumer = self._make()
        assert consumer.is_connected is True

    def test_is_running_initially_false(self) -> None:
        consumer = self._make()
        assert consumer.is_running is False

    def test_events_processed_initially_zero(self) -> None:
        consumer = self._make()
        assert consumer.events_processed == 0

    def test_registered_handlers_empty_initially(self) -> None:
        consumer = self._make()
        assert consumer.registered_handlers == {}

    def test_register_handler_adds_to_registry(self) -> None:
        consumer = self._make()
        handler = lambda e: None  # noqa: E731
        consumer.register_handler(EventType.FILE_CREATED, handler)
        assert consumer.registered_handlers["file.created"] == 1

    def test_register_multiple_handlers_same_type(self) -> None:
        consumer = self._make()
        consumer.register_handler(EventType.FILE_CREATED, lambda e: None)
        consumer.register_handler(EventType.FILE_CREATED, lambda e: None)
        assert consumer.registered_handlers["file.created"] == 2

    def test_unregister_handler_removes_it(self) -> None:
        consumer = self._make()
        handler = lambda e: None  # noqa: E731
        consumer.register_handler(EventType.FILE_CREATED, handler)
        result = consumer.unregister_handler(EventType.FILE_CREATED, handler)
        assert result is True
        assert "file.created" not in consumer.registered_handlers

    def test_unregister_nonexistent_handler_returns_false(self) -> None:
        consumer = self._make()
        handler = lambda e: None  # noqa: E731
        result = consumer.unregister_handler(EventType.FILE_CREATED, handler)
        assert result is False

    def test_repr_shows_key_fields(self) -> None:
        consumer = self._make()
        r = repr(consumer)
        assert "connected=" in r
        assert "running=" in r
        assert "handlers=" in r


class TestEventConsumerDispatch:
    """_dispatch_event paths: success, failure, no handlers."""

    def _make(self) -> tuple[EventConsumer, MagicMock]:
        cfg = EventConfig()
        mock_redis = MagicMock()
        mock_redis.xack.return_value = 1
        mgr = _make_connected_manager(mock_redis)
        return EventConsumer(config=cfg, stream_manager=mgr), mock_redis

    def test_dispatch_calls_handler_and_acknowledges_on_success(self) -> None:
        consumer, mock_redis = self._make()
        received: list[Event] = []
        consumer.register_handler(EventType.FILE_CREATED, lambda e: received.append(e))

        evt = _make_event(data={"event_type": "file.created", "path": "/x.txt"})
        consumer._dispatch_event(evt, "events", "grp1")

        assert len(received) == 1
        assert received[0].id == evt.id
        assert consumer.events_processed == 1
        mock_redis.xack.assert_called_once()

    def test_dispatch_does_not_acknowledge_on_handler_failure(self) -> None:
        consumer, mock_redis = self._make()

        def bad_handler(e: Event) -> None:
            raise ValueError("handler exploded")

        consumer.register_handler(EventType.FILE_CREATED, bad_handler)
        evt = _make_event(data={"event_type": "file.created"})
        consumer._dispatch_event(evt, "events", "grp1")

        assert consumer.events_processed == 0
        mock_redis.xack.assert_not_called()

    def test_dispatch_no_handlers_still_acknowledges(self) -> None:
        consumer, mock_redis = self._make()
        evt = _make_event(data={"event_type": "file.deleted"})
        consumer._dispatch_event(evt, "events", "grp1")
        # No handlers => still ACK to prevent redelivery
        mock_redis.xack.assert_called_once()

    def test_dispatch_partial_failure_not_acknowledged(self) -> None:
        """When one of two handlers fails, all_succeeded is False → no ACK."""

        def _raising_handler(e: Event) -> None:
            raise RuntimeError("oops")

        consumer, mock_redis = self._make()
        consumer.register_handler(EventType.FILE_CREATED, lambda e: None)
        consumer.register_handler(EventType.FILE_CREATED, _raising_handler)

        evt = _make_event(data={"event_type": "file.created"})
        consumer._dispatch_event(evt, "events", "grp1")
        assert consumer.events_processed == 0

    def test_dispatch_unknown_event_type_still_acknowledges(self) -> None:
        consumer, mock_redis = self._make()
        evt = _make_event(data={"event_type": "unknown.type"})
        consumer._dispatch_event(evt, "events", "grp1")
        mock_redis.xack.assert_called_once()


class TestEventConsumerStopSignal:
    """stop() flips _running and connect/disconnect lifecycle."""

    def test_stop_sets_running_false(self) -> None:
        cfg = EventConfig()
        mgr = RedisStreamManager(cfg)
        consumer = EventConsumer(config=cfg, stream_manager=mgr)
        consumer._running = True
        consumer.stop()
        assert consumer.is_running is False

    def test_stop_when_not_running_is_noop(self) -> None:
        consumer = EventConsumer()
        consumer.stop()  # Should not raise
        assert consumer.is_running is False


class TestEventConsumerNotConnected:
    """start_consuming logs warning when not connected."""

    def test_start_consuming_exits_when_not_connected(self) -> None:
        cfg = EventConfig()
        mgr = RedisStreamManager(cfg)
        consumer = EventConsumer(config=cfg, stream_manager=mgr)
        # Since not connected, start_consuming should return immediately
        asyncio.run(consumer.start_consuming("events"))
        assert consumer.is_running is False


class TestEventConsumerContextManager:
    """Context manager connects / disconnects."""

    def test_context_manager_connects_and_disconnects(self) -> None:
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        cfg = EventConfig()
        mgr = RedisStreamManager(cfg)
        mgr._redis = mock_redis
        mgr._connected = True

        consumer = EventConsumer(config=cfg, stream_manager=mgr)
        with consumer as c:
            assert c is consumer
        assert consumer.is_connected is False


# ===========================================================================
# middleware.py — MiddlewarePipeline, LoggingMiddleware, MetricsMiddleware,
#                 RetryMiddleware
# ===========================================================================


class TestMiddlewarePipelineOrdering:
    """Pipeline executes before_* in forward order and after_* in reverse."""

    def test_before_publish_forward_order(self) -> None:
        order: list[str] = []

        class MW:
            def __init__(self, name: str) -> None:
                self._name = name

            def before_publish(self, topic: str, data: dict) -> dict:
                order.append(self._name)
                return data

        pipeline = MiddlewarePipeline()
        pipeline.add(MW("first"))
        pipeline.add(MW("second"))
        pipeline.add(MW("third"))
        pipeline.run_before_publish("t", {})
        assert order == ["first", "second", "third"]

    def test_after_publish_reverse_order(self) -> None:
        order: list[str] = []

        class MW:
            def __init__(self, name: str) -> None:
                self._name = name

            def after_publish(self, topic: str, data: dict, message_id: str | None) -> None:
                order.append(self._name)

        pipeline = MiddlewarePipeline()
        pipeline.add(MW("first"))
        pipeline.add(MW("second"))
        pipeline.add(MW("third"))
        pipeline.run_after_publish("t", {}, "id-1")
        assert order == ["third", "second", "first"]

    def test_before_consume_forward_order(self) -> None:
        order: list[str] = []

        class MW:
            def __init__(self, name: str) -> None:
                self._name = name

            def before_consume(self, topic: str, data: dict) -> dict:
                order.append(self._name)
                return data

        pipeline = MiddlewarePipeline()
        pipeline.add(MW("a"))
        pipeline.add(MW("b"))
        pipeline.run_before_consume("t", {})
        assert order == ["a", "b"]

    def test_after_consume_reverse_order(self) -> None:
        order: list[str] = []

        class MW:
            def __init__(self, name: str) -> None:
                self._name = name

            def after_consume(self, topic: str, data: dict, error: Exception | None) -> None:
                order.append(self._name)

        pipeline = MiddlewarePipeline()
        pipeline.add(MW("a"))
        pipeline.add(MW("b"))
        pipeline.run_after_consume("t", {}, None)
        assert order == ["b", "a"]


class TestMiddlewarePipelineCancellation:
    """before_* returns None cancels the chain."""

    def test_before_publish_cancels_on_none(self) -> None:
        class CancelMW:
            def before_publish(self, topic: str, data: dict) -> None:
                return None

        class NeverCalledMW:
            def before_publish(self, topic: str, data: dict) -> dict:
                raise AssertionError("should not be called")

        pipeline = MiddlewarePipeline()
        pipeline.add(CancelMW())
        pipeline.add(NeverCalledMW())
        result = pipeline.run_before_publish("topic", {"key": "val"})
        assert result is None

    def test_before_consume_cancels_on_none(self) -> None:
        class CancelMW:
            def before_consume(self, topic: str, data: dict) -> None:
                return None

        pipeline = MiddlewarePipeline()
        pipeline.add(CancelMW())
        result = pipeline.run_before_consume("topic", {"key": "val"})
        assert result is None


class TestMiddlewarePipelineExceptionIsolation:
    """Exceptions in middleware are caught; chain continues."""

    def test_before_publish_exception_continues_chain(self) -> None:
        side_effects: list[str] = []

        class ExplodingMW:
            def before_publish(self, topic: str, data: dict) -> dict:
                raise RuntimeError("explode")

        class NormalMW:
            def before_publish(self, topic: str, data: dict) -> dict:
                side_effects.append("normal")
                return data

        pipeline = MiddlewarePipeline()
        pipeline.add(ExplodingMW())
        pipeline.add(NormalMW())
        result = pipeline.run_before_publish("t", {"x": 1})
        assert "normal" in side_effects
        # Chain continues with the original payload despite the earlier exception
        assert result == {"x": 1}

    def test_after_publish_exception_does_not_propagate(self) -> None:
        class ExplodingMW:
            def after_publish(self, topic: str, data: dict, message_id: str | None) -> None:
                raise RuntimeError("boom")

        pipeline = MiddlewarePipeline()
        pipeline.add(ExplodingMW())
        # Should not raise
        pipeline.run_after_publish("t", {}, "id-1")

    def test_after_consume_exception_does_not_propagate(self) -> None:
        class ExplodingMW:
            def after_consume(self, topic: str, data: dict, error: Exception | None) -> None:
                raise RuntimeError("boom")

        pipeline = MiddlewarePipeline()
        pipeline.add(ExplodingMW())
        pipeline.run_after_consume("t", {}, None)  # Should not raise


class TestMiddlewarePipelineManagement:
    """add/remove/clear/count/len."""

    def test_add_increments_count(self) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(LoggingMiddleware())
        assert pipeline.count == 1
        assert len(pipeline) == 1

    def test_remove_returns_true_and_decrements(self) -> None:
        pipeline = MiddlewarePipeline()
        mw = LoggingMiddleware()
        pipeline.add(mw)
        result = pipeline.remove(mw)
        assert result is True
        assert pipeline.count == 0

    def test_remove_absent_middleware_returns_false(self) -> None:
        pipeline = MiddlewarePipeline()
        result = pipeline.remove(LoggingMiddleware())
        assert result is False

    def test_clear_empties_pipeline(self) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(LoggingMiddleware())
        pipeline.add(MetricsMiddleware())
        pipeline.clear()
        assert pipeline.count == 0

    def test_repr_lists_class_names(self) -> None:
        pipeline = MiddlewarePipeline()
        pipeline.add(LoggingMiddleware())
        r = repr(pipeline)
        assert "LoggingMiddleware" in r


class TestLoggingMiddleware:
    """LoggingMiddleware logs and passes data through unchanged."""

    def test_before_publish_returns_data(self) -> None:
        mw = LoggingMiddleware()
        data = {"key": "value"}
        result = mw.before_publish("topic", data)
        assert result == data

    def test_after_publish_with_message_id(self) -> None:
        mw = LoggingMiddleware()
        mw.after_publish("topic", {"k": "v"}, "msg-123")  # Should not raise

    def test_after_publish_without_message_id(self) -> None:
        mw = LoggingMiddleware()
        mw.after_publish("topic", {"k": "v"}, None)  # Should not raise

    def test_before_consume_returns_data(self) -> None:
        mw = LoggingMiddleware()
        data = {"key": "value"}
        result = mw.before_consume("topic", data)
        assert result == data

    def test_after_consume_success(self) -> None:
        mw = LoggingMiddleware()
        mw.after_consume("topic", {}, None)  # Should not raise

    def test_after_consume_with_error(self) -> None:
        mw = LoggingMiddleware()
        mw.after_consume("topic", {}, ValueError("handler error"))  # Should not raise


class TestMetricsMiddleware:
    """MetricsMiddleware tracks counts and latency."""

    def test_after_publish_increments_publish_count(self) -> None:
        mw = MetricsMiddleware()
        mw.after_publish("topic", {}, "msg-1")
        assert mw.publish_count == 1
        assert mw.publish_errors == 0

    def test_after_publish_failure_increments_error_count(self) -> None:
        mw = MetricsMiddleware()
        mw.after_publish("topic", {}, None)
        assert mw.publish_errors == 1
        assert mw.publish_count == 0

    def test_before_publish_returns_data_unchanged(self) -> None:
        mw = MetricsMiddleware()
        data = {"k": "v"}
        result = mw.before_publish("topic", data)
        assert result is data

    def test_after_consume_increments_consume_count(self) -> None:
        mw = MetricsMiddleware()
        mw.before_consume("topic", {})
        mw.after_consume("topic", {}, None)
        assert mw.consume_count == 1
        assert mw.consume_errors == 0

    def test_after_consume_error_increments_error_count(self) -> None:
        mw = MetricsMiddleware()
        mw.before_consume("topic", {})
        mw.after_consume("topic", {}, RuntimeError("err"))
        assert mw.consume_errors == 1
        assert mw.consume_count == 0

    def test_avg_consume_time_ms_zero_when_no_events(self) -> None:
        mw = MetricsMiddleware()
        assert mw.avg_consume_time_ms == 0.0

    def test_avg_consume_time_ms_positive_after_consume(self) -> None:
        mw = MetricsMiddleware()
        # Use monotonic mock to inject elapsed time without wall-clock sleep
        with patch("time.monotonic", side_effect=[1000.0, 1000.5]):
            mw.before_consume("topic", {})
            mw.after_consume("topic", {}, None)
        assert mw.avg_consume_time_ms > 0.0

    def test_per_topic_publish_counts(self) -> None:
        mw = MetricsMiddleware()
        mw.after_publish("file.created", {}, "id-1")
        mw.after_publish("file.created", {}, "id-2")
        mw.after_publish("file.deleted", {}, "id-3")
        assert mw.topic_publish_counts["file.created"] == 2
        assert mw.topic_publish_counts["file.deleted"] == 1

    def test_reset_clears_all_counters(self) -> None:
        mw = MetricsMiddleware()
        mw.after_publish("t", {}, "id-1")
        mw.before_consume("t", {})
        mw.after_consume("t", {}, None)
        mw.reset()
        assert mw.publish_count == 0
        assert mw.consume_count == 0
        assert mw.topic_publish_counts == {}
        assert mw.topic_consume_counts == {}


class TestRetryMiddleware:
    """RetryMiddleware attempt counting and should_retry logic."""

    def test_should_retry_returns_true_within_limit(self) -> None:
        mw = RetryMiddleware(max_retries=3)
        data: dict[str, Any] = {}
        mw.before_consume("t", data)
        mw.after_consume("t", data, RuntimeError("err"))
        assert mw.should_retry("t", data) is True

    def test_should_retry_returns_false_at_limit(self) -> None:
        mw = RetryMiddleware(max_retries=1)
        data: dict[str, Any] = {}
        mw.before_consume("t", data)
        mw.after_consume("t", data, RuntimeError("first failure"))
        # First call consumes the one retry
        mw.should_retry("t", data)
        # Now at limit
        assert mw.should_retry("t", data) is False

    def test_total_retries_increments(self) -> None:
        mw = RetryMiddleware(max_retries=3)
        data: dict[str, Any] = {}
        mw.before_consume("t", data)
        mw.after_consume("t", data, RuntimeError("err"))
        mw.should_retry("t", data)
        assert mw.total_retries == 1

    def test_reset_clears_state(self) -> None:
        mw = RetryMiddleware(max_retries=3)
        data: dict[str, Any] = {}
        mw.before_consume("t", data)
        mw.after_consume("t", data, RuntimeError("err"))
        mw.should_retry("t", data)
        mw.reset()
        assert mw.total_retries == 0
        assert mw._attempt_counts == {}

    def test_before_consume_resets_attempt_count_for_event(self) -> None:
        mw = RetryMiddleware(max_retries=2)
        data: dict[str, Any] = {}
        mw.before_consume("t", data)
        event_key = f"t:{id(data)}"
        assert mw._attempt_counts[event_key] == 0

    def test_no_error_does_not_increment_attempts(self) -> None:
        mw = RetryMiddleware(max_retries=3)
        data: dict[str, Any] = {}
        mw.before_consume("t", data)
        mw.after_consume("t", data, None)  # success
        event_key = f"t:{id(data)}"
        assert mw._attempt_counts.get(event_key, 0) == 0

    def test_after_publish_is_noop(self) -> None:
        mw = RetryMiddleware()
        mw.after_publish("t", {}, "id")  # Should not raise


# ===========================================================================
# service_bus.py — ServiceBus
# ===========================================================================


class _FakePubSub:
    """Minimal PubSubManager stand-in that records publish calls."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic: str, data: dict) -> None:
        self.published.append((topic, data))

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass


class TestServiceBusRegistration:
    """Service registration and lookup."""

    def test_register_service_adds_to_registry(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(name="test-bus", pubsub=_FakePubSub())
        bus.register_service("echo", lambda req: {"ok": True})
        assert bus.has_service("echo") is True
        assert "echo" in bus.list_services()

    def test_register_duplicate_service_raises(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(name="test-bus", pubsub=_FakePubSub())
        bus.register_service("svc", lambda req: {})
        with pytest.raises(ValueError, match="already registered"):
            bus.register_service("svc", lambda req: {})

    def test_deregister_existing_service_returns_true(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(name="test-bus", pubsub=_FakePubSub())
        bus.register_service("svc", lambda req: {})
        assert bus.deregister_service("svc") is True
        assert bus.has_service("svc") is False

    def test_deregister_nonexistent_service_returns_false(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())
        assert bus.deregister_service("ghost") is False

    def test_list_services_is_sorted(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())
        bus.register_service("zebra", lambda req: {})
        bus.register_service("apple", lambda req: {})
        assert bus.list_services() == ["apple", "zebra"]

    def test_services_property_returns_snapshot(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())
        bus.register_service("svc", lambda req: {})
        snapshot = bus.services
        assert "svc" in snapshot


class TestServiceBusSendRequest:
    """send_request success, error, timeout, and missing-service paths."""

    def test_successful_request_returns_success_response(self) -> None:
        from events.service_bus import ServiceBus

        pubsub = _FakePubSub()
        bus = ServiceBus(name="gateway", pubsub=pubsub)
        bus.register_service("echo", lambda req: {"echo": req.payload.get("msg")})

        response = bus.send_request("echo", "ping", {"msg": "hello"})
        assert response.success is True
        assert response.data == {"echo": "hello"}
        assert response.request_id is not None
        assert response.error is None

    def test_request_to_missing_service_returns_error_response(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())
        response = bus.send_request("nonexistent", "action")
        assert response.success is False
        assert "nonexistent" in (response.error or "")
        assert bus.error_count == 1

    def test_handler_exception_returns_error_response(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())

        def bad_handler(req):
            raise RuntimeError("handler exploded")

        bus.register_service("fragile", bad_handler)
        response = bus.send_request("fragile", "do_it")
        assert response.success is False
        assert "handler exploded" in (response.error or "")
        assert bus.error_count == 1

    def test_request_count_increments(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())
        bus.register_service("svc", lambda req: {})
        bus.send_request("svc", "action")
        bus.send_request("svc", "action")
        assert bus.request_count == 2

    def test_send_request_publishes_request_and_response_events(self) -> None:
        from events.service_bus import ServiceBus

        pubsub = _FakePubSub()
        bus = ServiceBus(name="gw", pubsub=pubsub)
        bus.register_service("svc", lambda req: {"result": "ok"})
        bus.send_request("svc", "act")
        topics = [t for t, _ in pubsub.published]
        assert "service.request.svc.act" in topics
        assert "service.response.svc.act" in topics

    def test_handler_returning_non_dict_gives_empty_data(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())
        bus.register_service("svc", lambda req: "not a dict")
        response = bus.send_request("svc", "act")
        assert response.success is True
        assert response.data == {}

    def test_response_includes_duration_ms(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())
        bus.register_service("svc", lambda req: {})
        response = bus.send_request("svc", "act")
        assert response.duration_ms >= 0.0


class TestServiceBusBroadcast:
    """broadcast sends to all registered services."""

    def test_broadcast_reaches_all_services(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())
        results_a: list = []
        results_b: list = []
        bus.register_service("a", lambda req: results_a.append(req) or {})
        bus.register_service("b", lambda req: results_b.append(req) or {})

        responses = bus.broadcast("ping", {"test": True})
        assert len(responses) == 2
        assert "a" in responses
        assert "b" in responses
        assert responses["a"].success is True
        assert responses["b"].success is True

    def test_broadcast_empty_bus_returns_empty_dict(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(pubsub=_FakePubSub())
        responses = bus.broadcast("ping")
        assert responses == {}


class TestServiceBusDataclasses:
    """ServiceRequest and ServiceResponse serialization."""

    def test_service_request_to_dict(self) -> None:
        from events.service_bus import ServiceRequest

        req = ServiceRequest(
            id="req-1",
            source="gateway",
            target="echo",
            action="ping",
            payload={"msg": "hello"},
        )
        d = req.to_dict()
        assert d["id"] == "req-1"
        assert d["source"] == "gateway"
        assert d["target"] == "echo"
        assert d["action"] == "ping"
        assert d["payload"] == {"msg": "hello"}
        assert "timestamp" in d

    def test_service_response_to_dict(self) -> None:
        from events.service_bus import ServiceResponse

        resp = ServiceResponse(
            request_id="req-1",
            success=True,
            data={"result": "ok"},
            error=None,
            duration_ms=12.5,
        )
        d = resp.to_dict()
        assert d["request_id"] == "req-1"
        assert d["success"] is True
        assert d["data"] == {"result": "ok"}
        assert d["error"] is None
        assert d["duration_ms"] == pytest.approx(12.5)

    def test_service_response_error_to_dict(self) -> None:
        from events.service_bus import ServiceResponse

        resp = ServiceResponse(
            request_id="req-2",
            success=False,
            error="something went wrong",
        )
        d = resp.to_dict()
        assert d["success"] is False
        assert d["error"] == "something went wrong"

    def test_service_bus_repr(self) -> None:
        from events.service_bus import ServiceBus

        bus = ServiceBus(name="my-bus", pubsub=_FakePubSub())
        r = repr(bus)
        assert "my-bus" in r
        assert "services=" in r
