"""Integration tests covering watcher/handler, watcher/monitor, updater/*,
core/backend_detector, core/hardware_profile, daemon/service, daemon/scheduler.

Target: ≥80% integration coverage for each module.
"""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fs_event(src_path: str, is_dir: bool = False) -> MagicMock:
    """Build a minimal watchdog FileSystemEvent-like mock."""
    evt = MagicMock()
    evt.src_path = src_path
    evt.is_directory = is_dir
    return evt


def _make_moved_event(src_path: str, dest_path: str) -> MagicMock:
    evt = MagicMock()
    evt.src_path = src_path
    evt.dest_path = dest_path
    evt.is_directory = False
    return evt


# ---------------------------------------------------------------------------
# watcher/handler.py — FileEventHandler
# ---------------------------------------------------------------------------


class TestFileEventHandlerCreated:
    """on_created routes a file event through the full pipeline."""

    def test_created_event_enqueued(self, tmp_path: Path) -> None:
        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        f = tmp_path / "hello.txt"
        _make_fs_event(str(f))
        # Patch DirCreatedEvent check so it is NOT treated as a dir event
        from watchdog.events import FileCreatedEvent

        handler.on_created(FileCreatedEvent(str(f)))
        assert q.size == 1
        batch = q.dequeue_batch()
        assert batch[0].event_type == EventType.CREATED

    def test_created_tmp_file_filtered_out(self, tmp_path: Path) -> None:
        from watchdog.events import FileCreatedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        handler.on_created(FileCreatedEvent(str(tmp_path / "upload.tmp")))
        assert q.size == 0

    def test_modified_event_enqueued(self, tmp_path: Path) -> None:
        from watchdog.events import FileModifiedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        handler.on_modified(FileModifiedEvent(str(tmp_path / "data.txt")))
        assert q.size == 1
        assert q.dequeue_batch()[0].event_type == EventType.MODIFIED

    def test_deleted_event_enqueued(self, tmp_path: Path) -> None:
        from watchdog.events import FileDeletedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        handler.on_deleted(FileDeletedEvent(str(tmp_path / "gone.pdf")))
        assert q.size == 1
        assert q.dequeue_batch()[0].event_type == EventType.DELETED

    def test_moved_event_has_dest_path(self, tmp_path: Path) -> None:
        from watchdog.events import FileMovedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        src = str(tmp_path / "old.txt")
        dst = str(tmp_path / "new.txt")
        handler.on_moved(FileMovedEvent(src, dst))
        assert q.size == 1
        ev = q.dequeue_batch()[0]
        assert ev.event_type == EventType.MOVED
        assert ev.dest_path is not None
        assert ev.dest_path == Path(dst)

    def test_directory_created_event_passes_through(self, tmp_path: Path) -> None:
        from watchdog.events import DirCreatedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        handler.on_created(DirCreatedEvent(str(tmp_path / "subdir")))
        assert q.size == 1
        ev = q.dequeue_batch()[0]
        assert ev.is_directory is True
        assert ev.event_type == EventType.CREATED


class TestFileEventHandlerDebounce:
    def test_rapid_events_debounced(self, tmp_path: Path) -> None:
        from watchdog.events import FileCreatedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=10.0)  # Long window
        handler = FileEventHandler(cfg, q)

        f = str(tmp_path / "fast.txt")
        # First event passes
        handler.on_created(FileCreatedEvent(f))
        # Immediate second on same path — should be debounced
        handler.on_created(FileCreatedEvent(f))
        assert q.size == 1

    def test_debounce_expires_allows_second_event(self, tmp_path: Path) -> None:
        from watchdog.events import FileCreatedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)  # No debounce
        handler = FileEventHandler(cfg, q)

        f = str(tmp_path / "fast.txt")
        handler.on_created(FileCreatedEvent(f))
        handler.on_created(FileCreatedEvent(f))
        assert q.size == 2

    def test_debounce_dict_evicts_stale_entries(self, tmp_path: Path) -> None:
        """F3: entries older than the stale horizon are dropped on the
        next ``_should_process`` call so the dict can't grow unbounded
        over a long-running daemon's lifetime."""
        import time as _time

        from watcher.config import WatcherConfig
        from watcher.handler import _MIN_EVICTION_HORIZON_S, FileEventHandler
        from watcher.queue import EventQueue

        cfg = WatcherConfig(debounce_seconds=10.0)
        handler = FileEventHandler(cfg, EventQueue())

        horizon = max(cfg.debounce_seconds * 10, _MIN_EVICTION_HORIZON_S)
        now = _time.monotonic()
        with handler._debounce_lock:
            handler._last_event_times["stale"] = now - horizon - 1.0
            handler._last_event_times["recent"] = now - 0.1

        handler._should_process("new")
        assert "stale" not in handler._last_event_times
        assert "recent" in handler._last_event_times
        assert "new" in handler._last_event_times

    def test_debounce_dict_hard_cap_prevents_unbounded_growth(self, tmp_path: Path) -> None:
        """F3: even if nothing is stale, the dict is capped at
        ``_MAX_DEBOUNCE_ENTRIES``. Oldest entries are dropped in bulk
        on the next ``_should_process`` call.

        Also exercises the one-shot warning latch: a second over-cap
        call skips the WARNING (``272->exit`` branch) because
        ``_debounce_cap_warned`` is already True. The gate exists so
        a sustained over-cap pattern doesn't flood logs.
        """
        import time as _time

        from watcher.config import WatcherConfig
        from watcher.handler import _MAX_DEBOUNCE_ENTRIES, FileEventHandler
        from watcher.queue import EventQueue

        cfg = WatcherConfig(debounce_seconds=60.0)
        handler = FileEventHandler(cfg, EventQueue())

        now = _time.monotonic()
        overflow = 10
        with handler._debounce_lock:
            for i in range(_MAX_DEBOUNCE_ENTRIES + overflow):
                handler._last_event_times[f"p{i}"] = now - (overflow - i) * 0.0001

        # First over-cap call: logs the warning, sets the latch, evicts.
        handler._should_process("trigger")
        assert len(handler._last_event_times) <= _MAX_DEBOUNCE_ENTRIES + 1
        assert handler._debounce_cap_warned is True

        # Push back over cap and call again — the latch must suppress
        # the warning on this second eviction (exercises line 272->exit).
        with handler._debounce_lock:
            for i in range(overflow):
                handler._last_event_times[f"refill{i}"] = now - (overflow - i) * 0.0001
        handler._should_process("trigger2")
        assert handler._debounce_cap_warned is True, (
            "latch must stay True while we're still breaching the cap"
        )

    def test_pending_paths_tracks_state(self, tmp_path: Path) -> None:
        from watchdog.events import FileCreatedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        handler.on_created(FileCreatedEvent(str(tmp_path / "a.txt")))
        assert handler.pending_paths >= 1

    def test_clear_debounce_state_resets(self, tmp_path: Path) -> None:
        from watchdog.events import FileCreatedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        handler.on_created(FileCreatedEvent(str(tmp_path / "b.txt")))
        handler.clear_debounce_state()
        assert handler.pending_paths == 0


class TestFileEventHandlerCallbacks:
    def test_callback_invoked_on_created(self, tmp_path: Path) -> None:
        from watchdog.events import FileCreatedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        received: list[object] = []
        handler.register_callback(EventType.CREATED, lambda ev: received.append(ev))
        handler.on_created(FileCreatedEvent(str(tmp_path / "doc.txt")))

        assert len(received) == 1

    def test_callback_invoked_on_deleted(self, tmp_path: Path) -> None:
        from watchdog.events import FileDeletedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        received: list[object] = []
        handler.register_callback(EventType.DELETED, lambda ev: received.append(ev))
        handler.on_deleted(FileDeletedEvent(str(tmp_path / "gone.txt")))

        assert len(received) == 1

    def test_callback_invoked_on_modified(self, tmp_path: Path) -> None:
        from watchdog.events import FileModifiedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        received: list[object] = []
        handler.register_callback(EventType.MODIFIED, lambda ev: received.append(ev))
        handler.on_modified(FileModifiedEvent(str(tmp_path / "edit.txt")))

        assert len(received) == 1

    def test_callback_invoked_on_moved(self, tmp_path: Path) -> None:
        from watchdog.events import FileMovedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        received: list[object] = []
        handler.register_callback(EventType.MOVED, lambda ev: received.append(ev))
        handler.on_moved(FileMovedEvent(str(tmp_path / "a.txt"), str(tmp_path / "b.txt")))

        assert len(received) == 1

    def test_failing_callback_does_not_propagate(self, tmp_path: Path) -> None:
        from watchdog.events import FileCreatedEvent

        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        q = EventQueue()
        cfg = WatcherConfig(debounce_seconds=0.0)
        handler = FileEventHandler(cfg, q)

        def bad_cb(ev: object) -> None:
            raise RuntimeError("callback boom")

        handler.register_callback(EventType.CREATED, bad_cb)
        # Should not raise
        handler.on_created(FileCreatedEvent(str(tmp_path / "x.txt")))
        assert q.size == 1


# ---------------------------------------------------------------------------
# watcher/monitor.py — FileMonitor
# ---------------------------------------------------------------------------


class TestFileMonitorLifecycle:
    def test_start_and_stop(self, tmp_path: Path) -> None:
        from watcher.config import WatcherConfig
        from watcher.monitor import FileMonitor

        cfg = WatcherConfig(watch_directories=[tmp_path], debounce_seconds=0.0)
        monitor = FileMonitor(config=cfg)
        monitor.start()
        try:
            assert monitor.is_running is True
            assert monitor.observer_type in {"native", "polling"}
        finally:
            monitor.stop()
        assert monitor.is_running is False

    def test_start_twice_raises(self, tmp_path: Path) -> None:
        from watcher.config import WatcherConfig
        from watcher.monitor import FileMonitor

        cfg = WatcherConfig(watch_directories=[tmp_path], debounce_seconds=0.0)
        monitor = FileMonitor(config=cfg)
        monitor.start()
        try:
            with pytest.raises(RuntimeError):
                monitor.start()
        finally:
            monitor.stop()

    def test_stop_when_not_running_is_safe(self) -> None:
        from watcher.monitor import FileMonitor

        monitor = FileMonitor()
        monitor.stop()  # must not raise

    def test_default_observer_type_before_start(self) -> None:
        from watcher.monitor import FileMonitor

        monitor = FileMonitor()
        assert monitor.observer_type == "none"

    def test_is_running_false_before_start(self) -> None:
        from watcher.monitor import FileMonitor

        monitor = FileMonitor()
        assert monitor.is_running is False

    def test_polling_fallback_when_native_fails(self, tmp_path: Path) -> None:
        """FileMonitor falls back to PollingObserver when native observer raises."""

        from watcher.config import WatcherConfig
        from watcher.monitor import FileMonitor

        cfg = WatcherConfig(watch_directories=[tmp_path], debounce_seconds=0.0)

        with patch("watcher.monitor.Observer", side_effect=Exception("unsupported")):
            monitor = FileMonitor(config=cfg)
            monitor.start()
            try:
                assert monitor.observer_type == "polling"
                assert monitor.is_running is True
            finally:
                monitor.stop()


class TestFileMonitorDirectoryManagement:
    def test_add_directory_before_start(self, tmp_path: Path) -> None:
        from watcher.monitor import FileMonitor

        monitor = FileMonitor()
        sub = tmp_path / "sub"
        sub.mkdir()
        monitor.add_directory(sub)
        assert sub.resolve() in monitor.config.watch_directories

    def test_add_directory_while_running(self, tmp_path: Path) -> None:
        from watcher.config import WatcherConfig
        from watcher.monitor import FileMonitor

        cfg = WatcherConfig(watch_directories=[tmp_path], debounce_seconds=0.0)
        monitor = FileMonitor(config=cfg)
        monitor.start()
        sub = tmp_path / "extra"
        sub.mkdir()
        try:
            monitor.add_directory(sub)
            assert sub.resolve() in monitor.watched_directories
        finally:
            monitor.stop()

    def test_add_duplicate_directory_raises(self, tmp_path: Path) -> None:
        from watcher.config import WatcherConfig
        from watcher.monitor import FileMonitor

        cfg = WatcherConfig(watch_directories=[tmp_path], debounce_seconds=0.0)
        monitor = FileMonitor(config=cfg)
        monitor.start()
        try:
            with pytest.raises(ValueError):
                monitor.add_directory(tmp_path)
        finally:
            monitor.stop()

    def test_remove_directory_while_running(self, tmp_path: Path) -> None:
        from watcher.config import WatcherConfig
        from watcher.monitor import FileMonitor

        cfg = WatcherConfig(watch_directories=[tmp_path], debounce_seconds=0.0)
        monitor = FileMonitor(config=cfg)
        monitor.start()
        try:
            monitor.remove_directory(tmp_path)
            assert tmp_path.resolve() not in monitor.watched_directories
        finally:
            monitor.stop()

    def test_remove_unwatched_directory_raises(self, tmp_path: Path) -> None:
        from watcher.monitor import FileMonitor

        monitor = FileMonitor()
        with pytest.raises(ValueError):
            monitor.remove_directory(tmp_path / "nonexistent")

    def test_add_nonexistent_directory_while_running_raises(self, tmp_path: Path) -> None:
        from watcher.config import WatcherConfig
        from watcher.monitor import FileMonitor

        cfg = WatcherConfig(watch_directories=[tmp_path], debounce_seconds=0.0)
        monitor = FileMonitor(config=cfg)
        monitor.start()
        try:
            with pytest.raises(FileNotFoundError):
                monitor.add_directory(tmp_path / "does_not_exist")
        finally:
            monitor.stop()


class TestFileMonitorEventAccess:
    def test_get_events_returns_empty_when_no_events(self, tmp_path: Path) -> None:
        from watcher.config import WatcherConfig
        from watcher.monitor import FileMonitor

        cfg = WatcherConfig(watch_directories=[tmp_path], debounce_seconds=0.0)
        monitor = FileMonitor(config=cfg)
        monitor.start()
        try:
            events = monitor.get_events(max_size=5)
            assert events == []
        finally:
            monitor.stop()

    def test_event_count_zero_initially(self, tmp_path: Path) -> None:
        from watcher.config import WatcherConfig
        from watcher.monitor import FileMonitor

        cfg = WatcherConfig(watch_directories=[tmp_path], debounce_seconds=0.0)
        monitor = FileMonitor(config=cfg)
        assert monitor.event_count == 0

    def test_callback_registration(self, tmp_path: Path) -> None:
        from watcher.monitor import FileMonitor

        monitor = FileMonitor()
        cb = MagicMock()
        monitor.on_created(cb)
        monitor.on_modified(cb)
        monitor.on_deleted(cb)
        monitor.on_moved(cb)
        # No assertion needed — just verifying no exception raised


# ---------------------------------------------------------------------------
# updater/checker.py — UpdateChecker
# ---------------------------------------------------------------------------


class TestUpdateCheckerVersionParsing:
    """_parse_version helper and UpdateChecker.check() logic."""

    def test_parse_simple_version(self) -> None:
        from updater.checker import _parse_version

        assert _parse_version("2.0.0") == (2, 0, 0)

    def test_parse_version_with_v_prefix(self) -> None:
        from updater.checker import _parse_version

        assert _parse_version("v1.2.3") == (1, 2, 3)

    def test_parse_version_with_prerelease(self) -> None:
        from updater.checker import _parse_version

        assert _parse_version("2.0.0-alpha.1") == (2, 0, 0)

    def test_parse_version_single_segment(self) -> None:
        from updater.checker import _parse_version

        assert _parse_version("3") == (3,)

    def test_parse_empty_string_returns_zero(self) -> None:
        from updater.checker import _parse_version

        assert _parse_version("") == (0,)


class TestUpdateCheckerCheck:
    def test_check_returns_none_when_up_to_date(self) -> None:
        from updater.checker import ReleaseInfo, UpdateChecker

        checker = UpdateChecker(current_version="2.0.0")

        with patch.object(checker, "_fetch_latest_release") as mock_fetch:
            mock_fetch.return_value = ReleaseInfo(tag="v2.0.0", version="2.0.0")
            result = checker.check()

        assert result is None

    def test_check_returns_release_when_newer(self) -> None:
        from updater.checker import ReleaseInfo, UpdateChecker

        checker = UpdateChecker(current_version="1.0.0")

        with patch.object(checker, "_fetch_latest_release") as mock_fetch:
            mock_fetch.return_value = ReleaseInfo(tag="v2.0.0", version="2.0.0")
            result = checker.check()

        assert result is not None
        assert result.version == "2.0.0"

    def test_check_returns_none_on_network_error(self) -> None:
        from updater.checker import UpdateChecker

        checker = UpdateChecker(current_version="1.0.0")

        with patch.object(checker, "_fetch_latest_release", side_effect=Exception("network")):
            result = checker.check()

        assert result is None

    def test_check_returns_none_when_fetch_returns_none(self) -> None:
        from updater.checker import UpdateChecker

        checker = UpdateChecker(current_version="1.0.0")

        with patch.object(checker, "_fetch_latest_release", return_value=None):
            result = checker.check()

        assert result is None

    def test_get_latest_release_returns_none_on_error(self) -> None:
        from updater.checker import UpdateChecker

        checker = UpdateChecker(current_version="1.0.0")

        with patch.object(checker, "_fetch_latest_release", side_effect=Exception("err")):
            result = checker.get_latest_release()

        assert result is None

    def test_get_latest_release_returns_release_info(self) -> None:
        from updater.checker import ReleaseInfo, UpdateChecker

        checker = UpdateChecker(current_version="1.0.0")
        expected = ReleaseInfo(tag="v2.0.0", version="2.0.0")

        with patch.object(checker, "_fetch_latest_release", return_value=expected):
            result = checker.get_latest_release()

        assert result is not None
        assert result.version == "2.0.0"

    def test_current_version_property(self) -> None:
        from updater.checker import UpdateChecker

        checker = UpdateChecker(current_version="3.1.0")
        assert checker.current_version == "3.1.0"

    def test_fetch_latest_release_404_returns_none(self) -> None:
        from updater.checker import UpdateChecker

        checker = UpdateChecker(current_version="1.0.0")

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.Client") as MockClient:
            mock_client = MockClient.return_value.__enter__.return_value
            mock_client.get.return_value = mock_resp

            result = checker._fetch_latest_release()

        assert result is None

    def test_fetch_latest_release_parses_response(self) -> None:
        from updater.checker import UpdateChecker

        checker = UpdateChecker(current_version="1.0.0")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tag_name": "v2.5.0",
            "prerelease": False,
            "body": "Release notes",
            "assets": [],
            "published_at": "2026-01-01T00:00:00Z",
            "html_url": "https://example.com",
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            mock_client = MockClient.return_value.__enter__.return_value
            mock_client.get.return_value = mock_resp

            result = checker._fetch_latest_release()

        assert result is not None
        assert result.version == "2.5.0"
        assert result.tag == "v2.5.0"

    def test_fetch_prerelease_included(self) -> None:
        from updater.checker import UpdateChecker

        checker = UpdateChecker(current_version="1.0.0", include_prereleases=True)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "tag_name": "v2.0.0-beta.1",
                "prerelease": True,
                "draft": False,
                "body": "",
                "assets": [],
                "published_at": "",
                "html_url": "",
            }
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            mock_client = MockClient.return_value.__enter__.return_value
            mock_client.get.return_value = mock_resp

            result = checker._fetch_latest_release()

        assert result is not None
        assert "2.0.0" in result.version

    def test_parse_release_with_assets(self) -> None:
        from updater.checker import UpdateChecker

        data = {
            "tag_name": "v1.5.0",
            "prerelease": False,
            "body": "notes",
            "assets": [
                {
                    "name": "app-linux-x86_64",
                    "browser_download_url": "https://example.com/app",
                    "size": 10240,
                    "content_type": "application/octet-stream",
                }
            ],
            "published_at": "2026-01-01",
            "html_url": "https://example.com",
        }
        release = UpdateChecker._parse_release(data)
        assert release.version == "1.5.0"
        assert len(release.assets) == 1
        assert release.assets[0].name == "app-linux-x86_64"
        assert release.assets[0].size == 10240


# ---------------------------------------------------------------------------
# updater/installer.py — UpdateInstaller helpers
# ---------------------------------------------------------------------------


class TestInstallerPlatformHelpers:
    def test_get_platform_hints_darwin(self) -> None:
        from updater.installer import _get_platform_hints

        with patch("platform.system", return_value="Darwin"):
            hints = _get_platform_hints()
        assert "macos" in hints or "darwin" in hints

    def test_get_platform_hints_windows(self) -> None:
        from updater.installer import _get_platform_hints

        with patch("platform.system", return_value="Windows"):
            hints = _get_platform_hints()
        assert "windows" in hints or "win" in hints

    def test_get_platform_hints_linux(self) -> None:
        from updater.installer import _get_platform_hints

        with patch("platform.system", return_value="Linux"):
            hints = _get_platform_hints()
        assert "linux" in hints

    def test_get_arch_hints_x86(self) -> None:
        from updater.installer import _get_arch_hints

        with (
            patch("platform.machine", return_value="x86_64"),
            patch("platform.system", return_value="Linux"),
        ):
            hints = _get_arch_hints()
        assert "x86_64" in hints or "amd64" in hints

    def test_get_arch_hints_arm64(self) -> None:
        from updater.installer import _get_arch_hints

        with (
            patch("platform.machine", return_value="arm64"),
            patch("platform.system", return_value="Linux"),
        ):
            hints = _get_arch_hints()
        assert "arm64" in hints or "aarch64" in hints

    def test_is_checksum_file_sha256(self) -> None:
        from updater.installer import _is_checksum_file

        assert _is_checksum_file("sha256sums.sha256") is True
        assert _is_checksum_file("app.sig") is True
        assert _is_checksum_file("app.asc") is True
        assert _is_checksum_file("app-linux") is False

    def test_score_asset_linux_appimage(self) -> None:
        from updater.installer import _score_asset

        with patch("platform.system", return_value="Linux"):
            score = _score_asset("app.appimage")
        assert score > 0

    def test_score_asset_windows_exe(self) -> None:
        from updater.installer import _score_asset

        with patch("platform.system", return_value="Windows"):
            score = _score_asset("app.exe")
        assert score > 0


class TestUpdateInstallerSelectAsset:
    def _make_release_with_assets(self) -> object:
        from updater.checker import AssetInfo, ReleaseInfo

        return ReleaseInfo(
            tag="v2.0.0",
            version="2.0.0",
            assets=[
                AssetInfo(name="app-linux-x86_64", url="https://example.com/linux", size=1000),
                AssetInfo(name="app-macos-arm64", url="https://example.com/mac", size=1000),
                AssetInfo(name="checksums.sha256", url="https://example.com/sums", size=100),
            ],
        )

    def test_select_asset_linux(self) -> None:
        from updater.installer import UpdateInstaller

        installer = UpdateInstaller(install_dir="/tmp")
        release = self._make_release_with_assets()

        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
        ):
            asset = installer.select_asset(release)

        assert asset is not None
        assert "linux" in asset.name.lower()

    def test_select_asset_no_match_returns_none(self) -> None:
        from updater.checker import AssetInfo, ReleaseInfo
        from updater.installer import UpdateInstaller

        installer = UpdateInstaller(install_dir="/tmp")
        release = ReleaseInfo(
            tag="v2.0.0",
            version="2.0.0",
            assets=[AssetInfo(name="checksums.sha256", url="https://x.com", size=50)],
        )

        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
        ):
            asset = installer.select_asset(release)

        assert asset is None

    def test_rollback_returns_false_when_no_backup(self, tmp_path: Path) -> None:
        from updater.installer import UpdateInstaller

        installer = UpdateInstaller(install_dir=tmp_path)
        result = installer.rollback("no-binary")
        assert result is False

    def test_rollback_succeeds_when_backup_exists(self, tmp_path: Path) -> None:
        from updater.installer import UpdateInstaller

        installer = UpdateInstaller(install_dir=tmp_path)
        backup = tmp_path / "fo.bak"
        backup.write_bytes(b"backup content")

        result = installer.rollback("fo")
        assert result is True
        target = tmp_path / "fo"
        assert target.exists()

    def test_install_creates_executable(self, tmp_path: Path) -> None:
        from updater.installer import UpdateInstaller

        installer = UpdateInstaller(install_dir=tmp_path)
        downloaded = tmp_path / "fo-update-new.bin"
        downloaded.write_bytes(b"new binary content")

        result = installer.install(downloaded, target_name="fo")

        assert result.success is True
        assert (tmp_path / "fo").exists()

    def test_download_asset_sha256_mismatch_returns_none(self, tmp_path: Path) -> None:
        from updater.checker import AssetInfo
        from updater.installer import UpdateInstaller

        installer = UpdateInstaller(install_dir=tmp_path)
        asset = AssetInfo(name="binary", url="https://example.com/binary", size=100)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_bytes.return_value = [b"fake content"]
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("httpx.stream", return_value=mock_resp):
            result = installer.download_asset(asset, expected_sha256="deadbeef" * 8)

        assert result is None

    def test_download_asset_success(self, tmp_path: Path) -> None:
        import hashlib

        from updater.checker import AssetInfo
        from updater.installer import UpdateInstaller

        installer = UpdateInstaller(install_dir=tmp_path)
        content = b"valid binary content"
        expected = hashlib.sha256(content).hexdigest()
        asset = AssetInfo(name="binary", url="https://example.com/binary", size=len(content))

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_bytes.return_value = [content]
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("httpx.stream", return_value=mock_resp):
            result = installer.download_asset(asset, expected_sha256=expected)

        assert result is not None
        assert result.exists()
        result.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# updater/manager.py — UpdateManager
# ---------------------------------------------------------------------------


class TestUpdateManager:
    def test_check_returns_status_not_available_when_no_release(self) -> None:
        from updater.manager import UpdateManager

        mgr = UpdateManager(current_version="2.0.0")

        with patch.object(mgr._checker, "check", return_value=None):
            status = mgr.check()

        assert status.available is False
        assert status.current_version == "2.0.0"

    def test_check_returns_available_when_newer(self) -> None:
        from updater.checker import ReleaseInfo
        from updater.manager import UpdateManager

        mgr = UpdateManager(current_version="1.0.0")
        release = ReleaseInfo(tag="v2.0.0", version="2.0.0")

        with patch.object(mgr._checker, "check", return_value=release):
            status = mgr.check()

        assert status.available is True
        assert status.latest_version == "2.0.0"
        assert status.release is release

    def test_current_version_property(self) -> None:
        from updater.manager import UpdateManager

        mgr = UpdateManager(current_version="3.0.0")
        assert mgr.current_version == "3.0.0"

    def test_update_no_update_available_returns_not_available(self) -> None:
        from updater.manager import UpdateManager

        mgr = UpdateManager(current_version="2.0.0")

        with patch.object(mgr._checker, "check", return_value=None):
            status = mgr.update()

        assert status.available is False

    def test_update_dry_run(self, tmp_path: Path) -> None:
        from updater.checker import AssetInfo, ReleaseInfo
        from updater.manager import UpdateManager

        mgr = UpdateManager(current_version="1.0.0", install_dir=tmp_path)
        release = ReleaseInfo(
            tag="v2.0.0",
            version="2.0.0",
            assets=[AssetInfo(name="app-linux-x86_64", url="https://example.com/app", size=100)],
        )
        downloaded_file = tmp_path / "downloaded"
        downloaded_file.write_bytes(b"content")

        with (
            patch.object(mgr._checker, "check", return_value=release),
            patch.object(mgr._installer, "select_asset", return_value=release.assets[0]),
            patch.object(mgr._installer, "find_checksum", return_value=""),
            patch.object(mgr._installer, "download_asset", return_value=downloaded_file),
        ):
            status = mgr.update(dry_run=True)

        assert status.install_result is not None
        assert status.install_result.success is True
        assert "dry run" in status.install_result.message.lower()

    def test_update_no_matching_asset_returns_failure(self) -> None:
        from updater.checker import ReleaseInfo
        from updater.manager import UpdateManager

        mgr = UpdateManager(current_version="1.0.0")
        release = ReleaseInfo(tag="v2.0.0", version="2.0.0", assets=[])

        with (
            patch.object(mgr._checker, "check", return_value=release),
            patch.object(mgr._installer, "select_asset", return_value=None),
        ):
            status = mgr.update()

        assert status.install_result is not None
        assert status.install_result.success is False
        assert "compatible" in status.install_result.message.lower()

    def test_update_download_failure_returns_failure(self) -> None:
        from updater.checker import AssetInfo, ReleaseInfo
        from updater.manager import UpdateManager

        mgr = UpdateManager(current_version="1.0.0")
        release = ReleaseInfo(
            tag="v2.0.0",
            version="2.0.0",
            assets=[AssetInfo(name="app", url="https://x.com", size=100)],
        )

        with (
            patch.object(mgr._checker, "check", return_value=release),
            patch.object(mgr._installer, "select_asset", return_value=release.assets[0]),
            patch.object(mgr._installer, "find_checksum", return_value=""),
            patch.object(mgr._installer, "download_asset", return_value=None),
        ):
            status = mgr.update()

        assert status.install_result is not None
        assert status.install_result.success is False

    def test_rollback_delegates_to_installer(self) -> None:
        from updater.manager import UpdateManager

        mgr = UpdateManager(current_version="2.0.0")

        with patch.object(mgr._installer, "rollback", return_value=True) as mock_rb:
            result = mgr.rollback()

        assert result is True
        mock_rb.assert_called_once()

    def test_message_up_to_date(self) -> None:
        from updater.manager import UpdateStatus

        status = UpdateStatus(available=False, current_version="1.0.0")
        assert "1.0.0" in status.message

    def test_message_update_available(self) -> None:
        from updater.manager import UpdateStatus

        status = UpdateStatus(available=True, current_version="1.0.0", latest_version="2.0.0")
        assert "2.0.0" in status.message


# ---------------------------------------------------------------------------
# updater/background.py — maybe_check_for_updates
# ---------------------------------------------------------------------------


class TestMaybeCheckForUpdates:
    def test_returns_none_when_env_var_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from updater.background import maybe_check_for_updates

        monkeypatch.setenv("FO_DISABLE_UPDATE_CHECK", "1")
        result = maybe_check_for_updates()
        assert result is None

    def test_returns_none_when_pytest_test_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from updater.background import maybe_check_for_updates

        monkeypatch.delenv("FO_DISABLE_UPDATE_CHECK", raising=False)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_something")
        result = maybe_check_for_updates()
        assert result is None

    def test_returns_none_when_check_on_startup_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from updater.background import maybe_check_for_updates

        monkeypatch.delenv("FO_DISABLE_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

        mock_policy = MagicMock()
        mock_policy.check_on_startup = False

        mock_cfg = MagicMock()
        mock_cfg.updates = mock_policy

        with patch("updater.background.ConfigManager") as MockCM:
            MockCM.return_value.load.return_value = mock_cfg
            result = maybe_check_for_updates()

        assert result is None

    def test_returns_none_when_not_due(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from updater.background import maybe_check_for_updates

        monkeypatch.delenv("FO_DISABLE_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

        mock_policy = MagicMock()
        mock_policy.check_on_startup = True
        mock_policy.interval_hours = 24

        mock_cfg = MagicMock()
        mock_cfg.updates = mock_policy

        mock_state = MagicMock()
        mock_state.due.return_value = False

        mock_store = MagicMock()
        mock_store.load.return_value = mock_state

        with patch("updater.background.ConfigManager") as MockCM:
            MockCM.return_value.load.return_value = mock_cfg
            result = maybe_check_for_updates(state_store=mock_store)

        assert result is None

    def test_performs_check_when_due(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from updater.background import maybe_check_for_updates
        from updater.manager import UpdateStatus

        monkeypatch.delenv("FO_DISABLE_UPDATE_CHECK", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

        mock_policy = MagicMock()
        mock_policy.check_on_startup = True
        mock_policy.interval_hours = 0
        mock_policy.repo = "owner/repo"
        mock_policy.include_prereleases = False

        mock_cfg = MagicMock()
        mock_cfg.updates = mock_policy

        mock_state = MagicMock()
        mock_state.due.return_value = True

        mock_store = MagicMock()
        mock_store.load.return_value = mock_state

        expected_status = UpdateStatus(available=False, current_version="1.0.0")

        with (
            patch("updater.background.ConfigManager") as MockCM,
            patch("updater.background.UpdateManager") as MockMgr,
        ):
            MockCM.return_value.load.return_value = mock_cfg
            MockMgr.return_value.check.return_value = expected_status

            result = maybe_check_for_updates(state_store=mock_store)

        assert result is not None
        assert result.available is False


# ---------------------------------------------------------------------------
# core/backend_detector.py — detect_ollama, list_installed_models
# ---------------------------------------------------------------------------


class TestDetectOllamaNotAvailable:
    """When the ollama package is absent, detect_ollama returns installed=False."""

    def test_detect_ollama_package_not_available(self) -> None:
        from core import backend_detector

        original = backend_detector.OLLAMA_AVAILABLE
        try:
            backend_detector.OLLAMA_AVAILABLE = False
            status = backend_detector.detect_ollama()
        finally:
            backend_detector.OLLAMA_AVAILABLE = original

        assert status.installed is False
        assert status.running is False

    def test_list_installed_models_package_not_available(self) -> None:
        from core import backend_detector

        original = backend_detector.OLLAMA_AVAILABLE
        try:
            backend_detector.OLLAMA_AVAILABLE = False
            models = backend_detector.list_installed_models()
        finally:
            backend_detector.OLLAMA_AVAILABLE = original

        assert models == []


class TestDetectOllamaAvailable:
    """When ollama package is available, exercise various code paths."""

    def test_detect_ollama_cli_installed_service_running(self) -> None:
        from core import backend_detector

        if not backend_detector.OLLAMA_AVAILABLE:
            pytest.skip("ollama not installed")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ollama version 0.1.0"

        mock_models = MagicMock()
        mock_models.models = []

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("ollama.Client") as MockClient,
        ):
            MockClient.return_value.list.return_value = mock_models
            status = backend_detector.detect_ollama()

        assert status.running is True
        assert status.installed is True

    def test_detect_ollama_cli_not_found_service_running(self) -> None:
        from core import backend_detector

        if not backend_detector.OLLAMA_AVAILABLE:
            pytest.skip("ollama not installed")

        mock_models = MagicMock()
        mock_models.models = [MagicMock(), MagicMock()]

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("ollama.Client") as MockClient,
        ):
            MockClient.return_value.list.return_value = mock_models
            status = backend_detector.detect_ollama()

        assert status.running is True
        assert status.models_count == 2

    def test_detect_ollama_service_not_running(self) -> None:
        from core import backend_detector

        if not backend_detector.OLLAMA_AVAILABLE:
            pytest.skip("ollama not installed")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("ollama.Client") as MockClient,
        ):
            MockClient.return_value.list.side_effect = ConnectionError("refused")
            status = backend_detector.detect_ollama()

        assert status.running is False

    def test_detect_ollama_count_dict_response(self) -> None:
        from core import backend_detector

        if not backend_detector.OLLAMA_AVAILABLE:
            pytest.skip("ollama not installed")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ollama 0.1.0"

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("ollama.Client") as MockClient,
        ):
            MockClient.return_value.list.return_value = {"models": [{}, {}]}
            status = backend_detector.detect_ollama()

        assert status.models_count == 2

    def test_detect_ollama_count_list_response(self) -> None:
        from core import backend_detector

        if not backend_detector.OLLAMA_AVAILABLE:
            pytest.skip("ollama not installed")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ollama 0.1.0"

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("ollama.Client") as MockClient,
        ):
            MockClient.return_value.list.return_value = [MagicMock()]
            status = backend_detector.detect_ollama()

        assert status.models_count == 1


class TestListInstalledModelsAvailable:
    def test_list_models_via_client_dict_response(self) -> None:
        from core import backend_detector

        if not backend_detector.OLLAMA_AVAILABLE:
            pytest.skip("ollama not installed")

        mock_model = MagicMock()
        mock_model.get = MagicMock(
            side_effect=lambda k, default=None: {"name": "llama3"}.get(k, default)
        )
        del mock_model.model  # Make it use dict path

        with patch("ollama.Client") as MockClient:
            MockClient.return_value.list.return_value = {
                "models": [{"name": "llama3", "size": 4000000000}]
            }
            models = backend_detector.list_installed_models()

        assert len(models) >= 1
        assert models[0].name == "llama3"

    def test_list_models_via_client_fallback_cli(self) -> None:
        from core import backend_detector

        if not backend_detector.OLLAMA_AVAILABLE:
            pytest.skip("ollama not installed")

        mock_cli_result = MagicMock()
        mock_cli_result.returncode = 0
        mock_cli_result.stdout = '{"models": [{"name": "phi3", "size": 2000000000}]}'

        with (
            patch("ollama.Client") as MockClient,
            patch("subprocess.run", return_value=mock_cli_result),
        ):
            MockClient.return_value.list.side_effect = ConnectionError("refused")
            models = backend_detector.list_installed_models()

        assert len(models) >= 1

    def test_list_models_cli_not_found_returns_empty(self) -> None:
        from core import backend_detector

        if not backend_detector.OLLAMA_AVAILABLE:
            pytest.skip("ollama not installed")

        with (
            patch("ollama.Client") as MockClient,
            patch("subprocess.run", side_effect=FileNotFoundError),
        ):
            MockClient.return_value.list.side_effect = ConnectionError("refused")
            models = backend_detector.list_installed_models()

        assert models == []


# ---------------------------------------------------------------------------
# core/hardware_profile.py — detect_hardware, HardwareProfile
# ---------------------------------------------------------------------------


class TestHardwareProfileDataclass:
    def _make_profile(
        self,
        gpu_type: str = "none",
        vram_bytes: int = 0,
        ram_bytes: int = 16 * 1024**3,
        cpu_cores: int = 8,
    ) -> object:
        from core.hardware_profile import GpuType, HardwareProfile

        return HardwareProfile(
            gpu_type=GpuType(gpu_type),
            gpu_name=None,
            vram_bytes=vram_bytes,
            ram_bytes=ram_bytes,
            cpu_cores=cpu_cores,
            os_name="Linux",
            arch="x86_64",
        )

    def test_vram_gb_zero_when_no_gpu(self) -> None:
        p = self._make_profile(vram_bytes=0)
        assert p.vram_gb == 0.0

    def test_vram_gb_nonzero(self) -> None:
        p = self._make_profile(vram_bytes=8 * 1024**3)
        assert p.vram_gb == 8.0

    def test_ram_gb(self) -> None:
        p = self._make_profile(ram_bytes=16 * 1024**3)
        assert p.ram_gb == 16.0

    def test_recommended_text_model_small_ram(self) -> None:
        p = self._make_profile(ram_bytes=8 * 1024**3)
        model = p.recommended_text_model()
        assert "3b" in model

    def test_recommended_text_model_large_ram(self) -> None:
        p = self._make_profile(ram_bytes=32 * 1024**3)
        model = p.recommended_text_model()
        assert "7b" in model

    def test_recommended_workers_minimum_one(self) -> None:
        p = self._make_profile(cpu_cores=1)
        assert p.recommended_workers() == 1

    def test_recommended_workers_half_cores(self) -> None:
        p = self._make_profile(cpu_cores=8)
        assert p.recommended_workers() == 4

    def test_to_dict_has_expected_keys(self) -> None:
        p = self._make_profile()
        d = p.to_dict()
        assert "gpu_type" in d
        assert "ram_gb" in d
        assert "cpu_cores" in d
        assert "recommended_text_model" in d
        assert "recommended_workers" in d

    def test_to_dict_gpu_type_value(self) -> None:
        p = self._make_profile(gpu_type="nvidia")
        d = p.to_dict()
        assert d["gpu_type"] == "nvidia"


class TestDetectHardware:
    def test_detect_hardware_returns_profile(self) -> None:
        from core.hardware_profile import HardwareProfile, detect_hardware

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            profile = detect_hardware()

        assert isinstance(profile, HardwareProfile)
        assert profile.cpu_cores >= 1
        assert profile.ram_bytes >= 0

    def test_detect_hardware_nvidia_detected(self) -> None:
        from core.hardware_profile import GpuType, detect_hardware

        # nvidia-smi succeeds
        def mock_run(cmd, *args, **kwargs):
            if "nvidia-smi" in cmd:
                r = MagicMock()
                r.returncode = 0
                r.stdout = "Tesla T4, 16160\n"
                return r
            r = MagicMock()
            r.returncode = 1
            return r

        with patch("subprocess.run", side_effect=mock_run):
            profile = detect_hardware()

        assert profile.gpu_type == GpuType.NVIDIA
        assert profile.gpu_name == "Tesla T4"

    def test_detect_hardware_no_gpu(self) -> None:
        from core.hardware_profile import GpuType, detect_hardware

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            profile = detect_hardware()

        assert profile.gpu_type == GpuType.NONE

    def test_detect_nvidia_file_not_found(self) -> None:
        from core.hardware_profile import _detect_nvidia

        with patch("subprocess.run", side_effect=FileNotFoundError):
            name, vram = _detect_nvidia()

        assert name is None
        assert vram == 0

    def test_detect_amd_file_not_found(self) -> None:
        from core.hardware_profile import _detect_amd

        with patch("subprocess.run", side_effect=FileNotFoundError):
            name, vram = _detect_amd()

        assert name is None
        assert vram == 0

    def test_detect_apple_mps_non_darwin(self) -> None:
        from core.hardware_profile import _detect_apple_mps

        with patch("platform.system", return_value="Linux"):
            name, vram = _detect_apple_mps()

        assert name is None
        assert vram == 0

    def test_detect_apple_mps_darwin_non_apple_chip(self) -> None:
        from core.hardware_profile import _detect_apple_mps

        intel_result = MagicMock()
        intel_result.returncode = 0
        intel_result.stdout = "Intel(R) Core(TM) i9"

        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.run", return_value=intel_result),
        ):
            name, vram = _detect_apple_mps()

        assert name is None

    def test_detect_apple_mps_darwin_apple_chip_returns_unified_memory(self) -> None:
        from core.hardware_profile import _detect_apple_mps

        brand_result = MagicMock(returncode=0, stdout="Apple M3 Max")
        mem_result = MagicMock(returncode=0, stdout="68719476736")

        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.run", side_effect=[brand_result, mem_result]),
        ):
            name, vram = _detect_apple_mps()

        assert name == "Apple M3 Max"
        assert vram == 68719476736

    def test_detect_apple_mps_darwin_subprocess_error_returns_none(self) -> None:
        from core.hardware_profile import _detect_apple_mps

        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.run", side_effect=FileNotFoundError("sysctl missing")),
        ):
            name, vram = _detect_apple_mps()

        assert name is None
        assert vram == 0

    def test_detect_nvidia_malformed_output_returns_none(self) -> None:
        from core.hardware_profile import _detect_nvidia

        malformed = MagicMock(returncode=0, stdout="Tesla T4 only")

        with patch("subprocess.run", return_value=malformed):
            name, vram = _detect_nvidia()

        assert name is None
        assert vram == 0

    def test_get_cpu_cores_returns_positive(self) -> None:
        from core.hardware_profile import _get_cpu_cores

        cores = _get_cpu_cores()
        assert cores >= 1

    def test_get_cpu_cores_importerror_falls_back_to_os_cpu_count(self) -> None:
        from core.hardware_profile import _get_cpu_cores

        with (
            patch.dict("sys.modules", {"psutil": None}),
            patch("os.cpu_count", return_value=12),
        ):
            cores = _get_cpu_cores()

        assert cores == 12

    def test_get_system_ram_uses_psutil_when_available(self) -> None:
        from core.hardware_profile import _get_system_ram

        fake_psutil = SimpleNamespace(
            virtual_memory=MagicMock(return_value=SimpleNamespace(total=32 * 1024**3))
        )

        with patch.dict("sys.modules", {"psutil": fake_psutil}):
            ram = _get_system_ram()

        assert ram == 32 * 1024**3
        fake_psutil.virtual_memory.assert_called_once_with()

    def test_get_system_ram_darwin_fallback_uses_sysctl(self) -> None:
        from core.hardware_profile import _get_system_ram

        sysctl_result = MagicMock(returncode=0, stdout="17179869184")

        with (
            patch.dict("sys.modules", {"psutil": None}),
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.run", return_value=sysctl_result),
        ):
            ram = _get_system_ram()

        assert ram == 17179869184

    def test_get_system_ram_linux_proc_meminfo_fallback(self) -> None:
        from core.hardware_profile import _get_system_ram

        with (
            patch.dict("sys.modules", {"psutil": None}),
            patch("platform.system", return_value="Linux"),
            patch("builtins.open", return_value=io.StringIO("MemTotal: 2048 kB\n")),
        ):
            ram = _get_system_ram()

        assert ram == 2048 * 1024

    def test_detect_amd_default_name_with_missing_rows(self) -> None:
        from core.hardware_profile import _detect_amd

        name_result = MagicMock(returncode=0, stdout="GPU,Name\n")
        mem_result = MagicMock(returncode=0, stdout="VRAM\n")

        with patch("subprocess.run", side_effect=[name_result, mem_result]):
            name, vram = _detect_amd()

        assert name == "AMD GPU"
        assert vram == 0

    def test_detect_amd_invalid_vram_keeps_name_and_zero_vram(self) -> None:
        from core.hardware_profile import _detect_amd

        name_result = MagicMock(returncode=0, stdout="GPU,Name\nRadeon Pro,foo\n")
        mem_result = MagicMock(returncode=0, stdout="VRAM\nnot-a-number,foo\n")

        with patch("subprocess.run", side_effect=[name_result, mem_result]):
            name, vram = _detect_amd()

        assert name == "Radeon Pro"
        assert vram == 0

    def test_detect_hardware_apple_mps_detected(self) -> None:
        from core.hardware_profile import GpuType, detect_hardware

        with (
            patch("core.hardware_profile._detect_nvidia", return_value=(None, 0)),
            patch(
                "core.hardware_profile._detect_apple_mps",
                return_value=("Apple M2 Pro", 32 * 1024**3),
            ),
            patch(
                "core.hardware_profile._detect_amd", return_value=("unused", 1)
            ) as mock_detect_amd,
            patch("core.hardware_profile._get_system_ram", return_value=32 * 1024**3),
            patch("core.hardware_profile._get_cpu_cores", return_value=10),
            patch("platform.system", return_value="Darwin"),
            patch("platform.machine", return_value="arm64"),
        ):
            profile = detect_hardware()

        assert profile.gpu_type == GpuType.APPLE_MPS
        assert profile.gpu_name == "Apple M2 Pro"
        assert profile.vram_bytes == 32 * 1024**3
        mock_detect_amd.assert_not_called()

    def test_detect_hardware_amd_detected(self) -> None:
        from core.hardware_profile import GpuType, detect_hardware

        with (
            patch("core.hardware_profile._detect_nvidia", return_value=(None, 0)),
            patch("core.hardware_profile._detect_apple_mps", return_value=(None, 0)),
            patch(
                "core.hardware_profile._detect_amd",
                return_value=("Radeon 7900", 24 * 1024**3),
            ) as mock_detect_amd,
            patch("core.hardware_profile._get_system_ram", return_value=64 * 1024**3),
            patch("core.hardware_profile._get_cpu_cores", return_value=16),
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
        ):
            profile = detect_hardware()

        assert profile.gpu_type == GpuType.AMD
        assert profile.gpu_name == "Radeon 7900"
        assert profile.vram_bytes == 24 * 1024**3
        mock_detect_amd.assert_called_once_with()


# ---------------------------------------------------------------------------
# daemon/scheduler.py — DaemonScheduler
# ---------------------------------------------------------------------------


class TestDaemonSchedulerTasks:
    def test_schedule_task_added(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        scheduler.schedule_task("test", 60.0, lambda: None)
        assert "test" in scheduler.task_names
        assert scheduler.task_count == 1

    def test_schedule_zero_interval_raises(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        with pytest.raises(ValueError):
            scheduler.schedule_task("bad", 0.0, lambda: None)

    def test_schedule_negative_interval_raises(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        with pytest.raises(ValueError):
            scheduler.schedule_task("neg", -1.0, lambda: None)

    def test_cancel_existing_task(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        scheduler.schedule_task("task1", 5.0, lambda: None)
        result = scheduler.cancel_task("task1")
        assert result is True
        assert "task1" not in scheduler.task_names

    def test_cancel_nonexistent_task_returns_false(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        result = scheduler.cancel_task("ghost")
        assert result is False

    def test_replace_existing_task(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        cb1 = MagicMock()
        cb2 = MagicMock()
        scheduler.schedule_task("task", 5.0, cb1)
        scheduler.schedule_task("task", 10.0, cb2)  # Replaces
        assert scheduler.task_count == 1
        assert scheduler.task_names == ["task"]


class TestDaemonSchedulerRunning:
    def test_run_in_background_starts(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        scheduler.run_in_background()
        try:
            assert scheduler.is_running is True
        finally:
            scheduler.stop()
        assert scheduler.is_running is False

    def test_run_in_background_twice_raises(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        scheduler.run_in_background()
        try:
            with pytest.raises(RuntimeError):
                scheduler.run_in_background()
        finally:
            scheduler.stop()

    def test_stop_when_not_running_is_safe(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        scheduler.stop()  # must not raise

    def test_task_fired_when_interval_elapsed(self) -> None:
        from daemon.scheduler import DaemonScheduler

        called = threading.Event()

        def task_fn() -> None:
            called.set()

        scheduler = DaemonScheduler()
        scheduler.schedule_task("quick", 0.05, task_fn)
        scheduler.run_in_background()
        try:
            assert called.wait(timeout=2.0), "Task was not fired within 2s"
        finally:
            scheduler.stop()

    def test_task_exception_does_not_stop_scheduler(self) -> None:
        from daemon.scheduler import DaemonScheduler

        call_count = [0]

        def bad_task() -> None:
            call_count[0] += 1
            raise RuntimeError("task failure")

        scheduler = DaemonScheduler()
        scheduler.schedule_task("bad", 0.05, bad_task)
        scheduler.run_in_background()
        # Poll until bad_task has been called at least once (max 5s), avoiding fixed sleep
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and call_count[0] == 0:
            pass
        try:
            # Scheduler should still be running despite task failures
            assert scheduler.is_running is True
        finally:
            scheduler.stop()

    def test_blocking_run_stops_on_event(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        scheduler.schedule_task("noop", 60.0, lambda: None)

        run_finished = threading.Event()

        def run_in_thread() -> None:
            scheduler.run()
            run_finished.set()

        t = threading.Thread(target=run_in_thread, daemon=True)
        t.start()
        scheduler.stop()
        assert run_finished.wait(timeout=3.0), "run() did not stop within 3s"

    def test_task_count_zero_initially(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        assert scheduler.task_count == 0

    def test_task_names_empty_initially(self) -> None:
        from daemon.scheduler import DaemonScheduler

        scheduler = DaemonScheduler()
        assert scheduler.task_names == []

    def test_tick_skips_task_when_interval_not_elapsed(self) -> None:
        from daemon.scheduler import DaemonScheduler

        callback = MagicMock()
        scheduler = DaemonScheduler()
        scheduler.schedule_task("later", 60.0, callback)
        scheduler._tasks["later"].last_run = time.monotonic()
        scheduler._tick()

        callback.assert_not_called()


# ---------------------------------------------------------------------------
# daemon/service.py — DaemonService (additional paths)
# ---------------------------------------------------------------------------


class TestDaemonServiceAdditional:
    def test_scheduler_task_count_after_start(self) -> None:
        """DaemonService registers default tasks on start."""
        from daemon.config import DaemonConfig
        from daemon.service import DaemonService

        daemon = DaemonService(DaemonConfig())
        daemon.start_background()
        try:
            # health_check + stats_report = 2 tasks
            assert daemon.scheduler.task_count == 2
        finally:
            daemon.stop()

    def test_scheduler_task_names_registered(self) -> None:
        from daemon.config import DaemonConfig
        from daemon.service import DaemonService

        daemon = DaemonService(DaemonConfig())
        daemon.start_background()
        try:
            names = daemon.scheduler.task_names
            assert "health_check" in names
            assert "stats_report" in names
        finally:
            daemon.stop()

    def test_uptime_increases_over_time(self) -> None:
        from daemon.config import DaemonConfig
        from daemon.service import DaemonService

        daemon = DaemonService(DaemonConfig())
        daemon.start_background()
        try:
            t1 = daemon.uptime_seconds
            # Poll until uptime advances without sleeping
            deadline = time.monotonic() + 2.0
            t2 = t1
            while t2 <= t1 and time.monotonic() < deadline:
                t2 = daemon.uptime_seconds
            assert t2 > t1
        finally:
            daemon.stop()

    def test_files_processed_starts_at_zero(self) -> None:
        from daemon.config import DaemonConfig
        from daemon.service import DaemonService

        daemon = DaemonService(DaemonConfig())
        daemon.start_background()
        try:
            assert daemon.files_processed == 0
        finally:
            daemon.stop()

    def test_on_stop_callback_invoked_after_stop(self) -> None:
        from daemon.config import DaemonConfig
        from daemon.service import DaemonService

        daemon = DaemonService(DaemonConfig())
        stop_called = threading.Event()
        daemon.on_stop(lambda: stop_called.set())
        daemon.start_background()
        daemon.stop()
        assert stop_called.wait(timeout=3.0), "on_stop was not called"

    def test_handle_signal_writes_to_pipe(self) -> None:
        """_handle_signal should write to the wakeup pipe without raising."""
        import os

        from daemon.config import DaemonConfig
        from daemon.service import DaemonService

        daemon = DaemonService(DaemonConfig())
        r, w = os.pipe()
        daemon._sig_wakeup_w = w
        try:
            daemon._handle_signal(15, None)
            data = os.read(r, 1)
            assert data == b"\x00"
        finally:
            os.close(r)
            os.close(w)
            daemon._sig_wakeup_w = None

    def test_handle_signal_when_pipe_none(self) -> None:
        """_handle_signal is a no-op when no pipe is set."""
        from daemon.config import DaemonConfig
        from daemon.service import DaemonService

        daemon = DaemonService(DaemonConfig())
        daemon._sig_wakeup_w = None
        daemon._handle_signal(15, None)  # must not raise

    def test_cleanup_calls_on_stop_callback(self) -> None:
        from daemon.config import DaemonConfig
        from daemon.service import DaemonService

        daemon = DaemonService(DaemonConfig())
        cb = MagicMock()
        daemon.on_stop(cb)
        daemon.start_background()
        daemon.stop()
        cb.assert_called_once()
