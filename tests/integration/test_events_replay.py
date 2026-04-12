from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from file_organizer.events.replay import EventReplayManager, ReplayConfig


@pytest.fixture
def mock_stream_manager():
    manager = MagicMock()
    manager.is_connected = True
    manager.config.get_stream_name.return_value = "myapp:events"
    return manager


def test_replay_config_defaults():
    config = ReplayConfig()
    assert config.batch_size == 100
    assert config.delay_between_events == 0.0
    assert config.dry_run is False


def test_replay_manager_init(mock_stream_manager):
    manager = EventReplayManager(mock_stream_manager)
    assert manager.config.batch_size == 100
    assert manager._manager == mock_stream_manager


def test_replay_range_not_connected(mock_stream_manager):
    mock_stream_manager.is_connected = False
    manager = EventReplayManager(mock_stream_manager)
    events = manager.replay_range("events", datetime.now(UTC), datetime.now(UTC))
    assert events == []


def test_replay_range_success(mock_stream_manager):
    # Setup mock returns for xrange
    # It will be called twice: first returns 2 events, second returns empty
    mock_stream_manager._redis.xrange.side_effect = [
        [("1000-0", {"event_type": "file.created"}), ("2000-0", {"event_type": "file.modified"})],
        [],
    ]

    manager = EventReplayManager(mock_stream_manager)
    events = manager.replay_range(
        "events", datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 2, tzinfo=UTC)
    )

    assert len(events) == 2
    assert events[0].id == "1000-0"
    assert events[0].data["event_type"] == "file.created"
    assert events[1].id == "2000-0"


def test_replay_by_id_success(mock_stream_manager):
    mock_stream_manager._redis.xrange.side_effect = [
        [("1000-0", {"event_type": "file.created"})],
        [("2000-0", {"event_type": "file.modified"})],
    ]

    manager = EventReplayManager(mock_stream_manager)
    events = manager.replay_by_id("events", ["1000-0", "2000-0"])

    assert len(events) == 2
    assert events[0].id == "1000-0"
    assert events[1].id == "2000-0"


def test_replay_to_consumer_dry_run(mock_stream_manager):
    mock_stream_manager._redis.xrange.return_value = [("1000-0", {"event_type": "file.created"})]
    config = ReplayConfig(dry_run=True)
    manager = EventReplayManager(mock_stream_manager, config)

    consumer = MagicMock()
    count = manager.replay_to_consumer("events", datetime(2023, 1, 1, tzinfo=UTC), consumer)

    assert count == 0


def test_replay_to_consumer(mock_stream_manager):
    # Setup xrange to return 1 event
    mock_stream_manager._redis.xrange.side_effect = [
        [("1000-0", {"event_type": "file.created"})],
        [],
    ]
    manager = EventReplayManager(mock_stream_manager)

    handler = MagicMock()
    consumer = MagicMock()
    # Mock the handler dictionary
    consumer._handlers = {"file.created": [handler]}

    count = manager.replay_to_consumer("events", datetime(2023, 1, 1, tzinfo=UTC), consumer)

    assert count == 1
    handler.assert_called_once()
