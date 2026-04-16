from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from events.replay import EventReplayManager, ReplayConfig


@pytest.fixture
def mock_stream_manager() -> MagicMock:
    manager = MagicMock()
    manager.is_connected = True
    manager.config.get_stream_name.return_value = "myapp:events"
    return manager


def test_replay_config_defaults() -> None:
    config = ReplayConfig()
    assert config.batch_size == 100
    assert config.delay_between_events == 0.0
    assert config.dry_run is False


def test_replay_manager_init(mock_stream_manager: MagicMock) -> None:
    manager = EventReplayManager(mock_stream_manager)
    assert manager.config.batch_size == 100
    assert manager._manager == mock_stream_manager


def test_replay_range_not_connected(mock_stream_manager: MagicMock) -> None:
    mock_stream_manager.is_connected = False
    manager = EventReplayManager(mock_stream_manager)
    events = manager.replay_range("events", datetime.now(UTC), datetime.now(UTC))
    assert events == []


def test_replay_range_success(mock_stream_manager: MagicMock) -> None:
    # xrange is called twice: first returns 2 events (== batch_size=2, triggers pagination),
    # second returns empty, ending the loop
    mock_stream_manager._redis.xrange.side_effect = [
        [("1000-0", {"event_type": "file.created"}), ("2000-0", {"event_type": "file.modified"})],
        [],
    ]

    manager = EventReplayManager(mock_stream_manager, ReplayConfig(batch_size=2))
    events = manager.replay_range(
        "events", datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 2, tzinfo=UTC)
    )

    assert len(events) == 2
    assert events[0].id == "1000-0"
    assert events[0].data["event_type"] == "file.created"
    assert events[1].id == "2000-0"
    # Both calls must have happened (pagination exercised)
    assert mock_stream_manager._redis.xrange.call_count == 2
    # Verify first call used the correct stream name and batch size (T3)
    first_call = mock_stream_manager._redis.xrange.call_args_list[0]
    assert first_call.args[0] == "myapp:events"
    assert first_call.kwargs["count"] == 2


def test_replay_by_id_success(mock_stream_manager: MagicMock) -> None:
    mock_stream_manager._redis.xrange.side_effect = [
        [("1000-0", {"event_type": "file.created"})],
        [("2000-0", {"event_type": "file.modified"})],
    ]

    manager = EventReplayManager(mock_stream_manager)
    events = manager.replay_by_id("events", ["1000-0", "2000-0"])

    assert len(events) == 2
    assert events[0].id == "1000-0"
    assert events[1].id == "2000-0"
    # Verify exact XRANGE call args for each ID lookup (T3)
    mock_stream_manager._redis.xrange.assert_any_call(
        "myapp:events", min="1000-0", max="1000-0", count=1
    )
    mock_stream_manager._redis.xrange.assert_any_call(
        "myapp:events", min="2000-0", max="2000-0", count=1
    )


def test_replay_to_consumer_dry_run(mock_stream_manager: MagicMock) -> None:
    mock_stream_manager._redis.xrange.return_value = [("1000-0", {"event_type": "file.created"})]
    config = ReplayConfig(dry_run=True)
    manager = EventReplayManager(mock_stream_manager, config)

    consumer = MagicMock()
    count = manager.replay_to_consumer("events", datetime(2023, 1, 1, tzinfo=UTC), consumer)

    assert count == 0


def test_replay_to_consumer(mock_stream_manager: MagicMock) -> None:
    # batch_size=100 > 1 result returned → loop exits after one xrange call
    mock_stream_manager._redis.xrange.side_effect = [
        [("1000-0", {"event_type": "file.created"})],
    ]
    manager = EventReplayManager(mock_stream_manager)

    handler = MagicMock()
    consumer = MagicMock()
    consumer._handlers = {"file.created": [handler]}

    count = manager.replay_to_consumer("events", datetime(2023, 1, 1, tzinfo=UTC), consumer)

    assert count == 1
    # Verify the handler was called with the correct event payload (T3)
    handler.assert_called_once()
    dispatched_event = handler.call_args[0][0]
    assert dispatched_event.id == "1000-0"
    assert dispatched_event.data["event_type"] == "file.created"
