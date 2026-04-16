"""Integration tests for small utility/config/model modules.

Covers:
  - daemon/config.py               — DaemonConfig
  - events/config.py               — EventConfig
  - models/_openai_response.py     — is_openai_token_exhausted
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from daemon.config import DaemonConfig
from events.config import EventConfig
from models._openai_response import is_openai_token_exhausted

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DaemonConfig
# ---------------------------------------------------------------------------


class TestDaemonConfigInit:
    def test_default_created(self) -> None:
        cfg = DaemonConfig()
        assert cfg is not None

    def test_default_dry_run(self) -> None:
        assert DaemonConfig().dry_run is True

    def test_default_poll_interval(self) -> None:
        assert DaemonConfig().poll_interval == 1.0

    def test_default_max_concurrent(self) -> None:
        assert DaemonConfig().max_concurrent == 4

    def test_watch_dirs_normalized(self, tmp_path: Path) -> None:
        cfg = DaemonConfig(watch_directories=[str(tmp_path)])
        assert isinstance(cfg.watch_directories[0], Path)

    def test_output_dir_normalized(self, tmp_path: Path) -> None:
        cfg = DaemonConfig(output_directory=str(tmp_path))
        assert isinstance(cfg.output_directory, Path)

    def test_pid_file_normalized(self, tmp_path: Path) -> None:
        cfg = DaemonConfig(pid_file=str(tmp_path / "daemon.pid"))
        assert isinstance(cfg.pid_file, Path)

    def test_log_file_normalized(self, tmp_path: Path) -> None:
        cfg = DaemonConfig(log_file=str(tmp_path / "daemon.log"))
        assert isinstance(cfg.log_file, Path)

    def test_zero_poll_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="poll_interval"):
            DaemonConfig(poll_interval=0)

    def test_negative_poll_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="poll_interval"):
            DaemonConfig(poll_interval=-1.0)

    def test_zero_max_concurrent_raises(self) -> None:
        with pytest.raises(ValueError, match="max_concurrent"):
            DaemonConfig(max_concurrent=0)

    def test_negative_max_concurrent_raises(self) -> None:
        with pytest.raises(ValueError, match="max_concurrent"):
            DaemonConfig(max_concurrent=-1)


# ---------------------------------------------------------------------------
# EventConfig
# ---------------------------------------------------------------------------


class TestEventConfigInit:
    def test_created(self) -> None:
        cfg = EventConfig()
        assert cfg is not None

    def test_default_redis_url(self) -> None:
        cfg = EventConfig()
        assert "redis" in cfg.redis_url.lower()

    def test_default_stream_prefix(self) -> None:
        cfg = EventConfig()
        assert isinstance(cfg.stream_prefix, str)
        assert len(cfg.stream_prefix) > 0

    def test_custom_values(self) -> None:
        cfg = EventConfig(redis_url="redis://custom:6380/1", batch_size=20)
        assert cfg.batch_size == 20
        assert "custom" in cfg.redis_url


class TestEventConfigGetStreamName:
    def test_returns_prefixed_name(self) -> None:
        cfg = EventConfig(stream_prefix="myapp")
        result = cfg.get_stream_name("events")
        assert result == "myapp:events"

    def test_custom_prefix(self) -> None:
        cfg = EventConfig(stream_prefix="test")
        result = cfg.get_stream_name("files")
        assert result == "test:files"

    def test_returns_string(self) -> None:
        cfg = EventConfig()
        result = cfg.get_stream_name("anything")
        assert result == "fileorg:anything"


# ---------------------------------------------------------------------------
# is_openai_token_exhausted
# ---------------------------------------------------------------------------


def _make_response(finish_reason: str, content: str) -> object:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(finish_reason=finish_reason, message=message)
    return SimpleNamespace(choices=[choice])


class TestIsOpenAITokenExhausted:
    def test_length_reason_short_content_returns_true(self) -> None:
        response = _make_response("length", "ok")
        assert is_openai_token_exhausted(response) is True

    def test_stop_reason_returns_false(self) -> None:
        response = _make_response("stop", "ok")
        assert is_openai_token_exhausted(response) is False

    def test_length_reason_long_content_returns_false(self) -> None:
        response = _make_response("length", "x" * 200)
        assert is_openai_token_exhausted(response) is False

    def test_empty_choices_returns_false(self) -> None:
        response = SimpleNamespace(choices=[])
        assert is_openai_token_exhausted(response) is False

    def test_none_content_short(self) -> None:
        message = SimpleNamespace(content=None)
        choice = SimpleNamespace(finish_reason="length", message=message)
        response = SimpleNamespace(choices=[choice])
        assert is_openai_token_exhausted(response) is True

    def test_custom_min_length(self) -> None:
        response = _make_response("length", "x" * 10)
        assert is_openai_token_exhausted(response, min_length=5) is False
        assert is_openai_token_exhausted(response, min_length=20) is True
