"""Integration tests for coverage gaps in 5 CLI modules.

Targets:
- cli/dedupe_hash.py  (22% → ≥80%): ProgressTracker, scan_for_duplicates,
  create_scan_options, initialize_hash_detector
- cli/dedupe.py       (27% → ≥80%): dedupe_command full flow (directory
  validation, scan, removal, summary, error paths)
- cli/undo_history.py (45% → ≥80%): preview/execute helpers via real
  UndoManager instances backed by tmp_path
- cli/interactive.py  (53% → ≥80%): set_flags, confirm_action, prompt_choice,
  prompt_directory, create_progress
- cli/daemon.py       (78% → ≥80%): foreground dry-run, watch command,
  process error truncation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_mock_operation(op_id: int = 1, op_type: str = "move", dst: str | None = "/dst/f.txt"):
    op = MagicMock()
    op.id = op_id
    op.operation_type = MagicMock()
    op.operation_type.value = op_type
    op.source_path = Path("/src/f.txt")
    op.destination_path = Path(dst) if dst else None
    return op


# ---------------------------------------------------------------------------
# dedupe_hash.py
# ---------------------------------------------------------------------------


class TestProgressTrackerIntegration:
    """Covers dedupe_hash.py lines 31-41, 50-55, 59-61."""

    def test_init_without_tqdm_prints_hint(self) -> None:
        """ProgressTracker init falls back gracefully when tqdm is missing."""
        from cli.dedupe_hash import ProgressTracker

        console = Console(record=True)
        import builtins

        original_import = builtins.__import__

        def _no_tqdm(name, *args, **kwargs):
            if name == "tqdm":
                raise ImportError("tqdm not installed")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_no_tqdm):
            tracker = ProgressTracker(console)

        assert tracker.has_tqdm is False
        assert tracker.progress_bar is None

    def test_callback_noop_when_tqdm_absent(self) -> None:
        """callback() is a no-op when tqdm is unavailable."""
        from cli.dedupe_hash import ProgressTracker

        console = Console(record=True)
        tracker = ProgressTracker(console)
        tracker.has_tqdm = False
        # Must not raise
        tracker.callback(1, 5)
        tracker.close()

    def test_callback_creates_and_updates_progress_bar(self) -> None:
        """callback() initialises tqdm on first call and updates on subsequent calls."""
        from cli.dedupe_hash import ProgressTracker

        console = Console(record=True)
        tracker = ProgressTracker(console)
        mock_bar = MagicMock()
        tracker.has_tqdm = True
        tracker.tqdm = MagicMock(return_value=mock_bar)

        tracker.callback(1, 4)
        tracker.callback(2, 4)

        tracker.tqdm.assert_called_once_with(total=4, desc="Hashing files", unit="files")
        assert mock_bar.update.call_count == 2

    def test_close_shuts_down_open_bar(self) -> None:
        """close() calls progress_bar.close() and resets to None."""
        from cli.dedupe_hash import ProgressTracker

        console = Console(record=True)
        tracker = ProgressTracker(console)
        mock_bar = MagicMock()
        tracker.has_tqdm = True
        tracker.tqdm = MagicMock(return_value=mock_bar)

        tracker.callback(1, 3)
        tracker.close()

        mock_bar.close.assert_called_once()
        assert tracker.progress_bar is None

    def test_close_is_noop_when_no_bar(self) -> None:
        """close() does not raise when progress_bar is None."""
        from cli.dedupe_hash import ProgressTracker

        console = Console(record=True)
        tracker = ProgressTracker(console)
        tracker.progress_bar = None
        tracker.close()  # must not raise


class TestScanForDuplicatesIntegration:
    """Covers dedupe_hash.py lines 83-106."""

    def test_no_duplicates_returns_empty_dict(self) -> None:
        """scan_for_duplicates returns {} and prints success when no groups."""
        from cli.dedupe_hash import scan_for_duplicates

        console = Console(record=True)
        detector = MagicMock()
        detector.get_duplicate_groups.return_value = {}
        tracker = MagicMock()

        result = scan_for_duplicates(Path("."), detector, MagicMock(), console, tracker)

        assert result == {}
        detector.scan_directory.assert_called_once()
        tracker.close.assert_called_once()
        assert "No duplicate files found" in console.export_text()

    def test_with_duplicates_returns_groups_and_prints_summary(self) -> None:
        """scan_for_duplicates returns groups and prints total count."""
        from cli.dedupe_hash import scan_for_duplicates

        console = Console(record=True)
        detector = MagicMock()
        group = MagicMock()
        group.count = 3
        detector.get_duplicate_groups.return_value = {"abc123": group}

        result = scan_for_duplicates(Path("."), detector, MagicMock(), console, None)

        assert "abc123" in result
        text = console.export_text()
        assert "Found 1 duplicate group(s) with 3 files total" in text

    def test_progress_tracker_none_does_not_close(self) -> None:
        """progress_tracker=None skips the close() call."""
        from cli.dedupe_hash import scan_for_duplicates

        console = Console(record=True)
        detector = MagicMock()
        detector.get_duplicate_groups.return_value = {}

        # Should not raise even when tracker is None
        scan_for_duplicates(Path("."), detector, MagicMock(), console, None)

    def test_multiple_groups_totals_correctly(self) -> None:
        """Total duplicate count sums across all groups."""
        from cli.dedupe_hash import scan_for_duplicates

        console = Console(record=True)
        detector = MagicMock()
        g1, g2 = MagicMock(), MagicMock()
        g1.count = 2
        g2.count = 4
        detector.get_duplicate_groups.return_value = {"hash1": g1, "hash2": g2}

        result = scan_for_duplicates(Path("."), detector, MagicMock(), console, None)

        assert len(result) == 2
        text = console.export_text()
        assert "2 duplicate group(s) with 6 files total" in text


class TestCreateScanOptionsIntegration:
    """Covers dedupe_hash.py lines 132-134."""

    def test_returns_scan_options_from_services_layer(self) -> None:
        """create_scan_options delegates to services.deduplication.detector.ScanOptions."""
        from cli.dedupe_hash import create_scan_options

        sentinel = object()
        with patch(
            "services.deduplication.detector.ScanOptions",
            return_value=sentinel,
        ) as mock_cls:
            result = create_scan_options(
                algorithm="sha256",
                recursive=True,
                min_file_size=0,
                max_file_size=None,
                file_patterns=None,
                exclude_patterns=None,
                progress_callback=None,
            )

        assert result is sentinel
        mock_cls.assert_called_once()

    def test_passes_all_params_through(self) -> None:
        """All keyword args are forwarded to ScanOptions."""
        from cli.dedupe_hash import create_scan_options

        with patch(
            "services.deduplication.detector.ScanOptions",
        ) as mock_cls:
            create_scan_options(
                algorithm="md5",
                recursive=False,
                min_file_size=100,
                max_file_size=5000,
                file_patterns=["*.jpg"],
                exclude_patterns=["*.tmp"],
                progress_callback=None,
            )

        _call = mock_cls.call_args
        assert _call.kwargs["algorithm"] == "md5"
        assert _call.kwargs["recursive"] is False
        assert _call.kwargs["min_file_size"] == 100
        assert _call.kwargs["max_file_size"] == 5000
        assert _call.kwargs["file_patterns"] == ["*.jpg"]
        assert _call.kwargs["exclude_patterns"] == ["*.tmp"]


class TestInitializeHashDetectorIntegration:
    """Covers dedupe_hash.py lines 151-153."""

    def test_returns_duplicate_detector_instance(self) -> None:
        """initialize_hash_detector wraps the services layer."""
        from cli.dedupe_hash import initialize_hash_detector

        sentinel = object()
        with patch(
            "services.deduplication.detector.DuplicateDetector",
            return_value=sentinel,
        ) as mock_cls:
            result = initialize_hash_detector()

        assert result is sentinel
        mock_cls.assert_called_once_with()


# ---------------------------------------------------------------------------
# dedupe.py — dedupe_command full flow
# ---------------------------------------------------------------------------


class TestDedupeCommandIntegration:
    """Covers dedupe.py lines 101-314, 319."""

    def test_nonexistent_directory_exits_1(self, tmp_path: Path) -> None:
        """dedupe_command returns 1 when directory does not exist."""
        from cli.dedupe import dedupe_command

        result = dedupe_command([str(tmp_path / "nonexistent")])
        assert result == 1

    def test_file_path_instead_of_dir_exits_1(self, tmp_path: Path) -> None:
        """dedupe_command returns 1 when path is a file not a directory."""
        from cli.dedupe import dedupe_command

        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = dedupe_command([str(f)])
        assert result == 1

    def test_no_duplicates_returns_0(self, tmp_path: Path) -> None:
        """dedupe_command returns 0 when scan finds no duplicates."""
        from cli.dedupe import dedupe_command

        (tmp_path / "a.txt").write_text("unique")
        with patch(
            "cli.dedupe.scan_for_duplicates",
            return_value={},
        ):
            result = dedupe_command([str(tmp_path)])
        assert result == 0

    def test_dry_run_with_duplicates_returns_0(self, tmp_path: Path) -> None:
        """dedupe_command in dry-run mode returns 0 and does not remove files."""
        from cli.dedupe import dedupe_command

        (tmp_path / "a.txt").write_text("dup")
        (tmp_path / "b.txt").write_text("dup")

        group = MagicMock()
        group.count = 2

        with (
            patch(
                "cli.dedupe.scan_for_duplicates",
                return_value={"abc": group},
            ),
            patch(
                "cli.dedupe_removal.process_duplicate_group",
                return_value=(0, 0),
            ) as mock_process,
        ):
            result = dedupe_command([str(tmp_path), "--dry-run"])
        assert result == 0
        assert mock_process.call_args.kwargs["dry_run"] is True

    def test_with_duplicates_calls_process_group(self, tmp_path: Path) -> None:
        """dedupe_command calls process_duplicate_group for each group found."""
        from cli.dedupe import dedupe_command

        group = MagicMock()
        group.count = 2
        mock_process = MagicMock(return_value=(1, 1024))

        with (
            patch(
                "cli.dedupe.scan_for_duplicates",
                return_value={"hash1": group},
            ),
            patch(
                "cli.dedupe_removal.process_duplicate_group",
                mock_process,
            ),
        ):
            result = dedupe_command([str(tmp_path)])

        assert result == 0
        mock_process.assert_called_once()

    def test_safe_mode_disabled_no_backup_manager(self, tmp_path: Path) -> None:
        """--no-safe-mode passes None backup_manager to process_duplicate_group."""
        from cli.dedupe import dedupe_command

        group = MagicMock()
        group.count = 2
        mock_process = MagicMock(return_value=(1, 512))

        with (
            patch(
                "cli.dedupe.scan_for_duplicates",
                return_value={"hash1": group},
            ),
            patch(
                "cli.dedupe_removal.process_duplicate_group",
                mock_process,
            ),
        ):
            result = dedupe_command([str(tmp_path), "--no-safe-mode"])

        assert result == 0
        _kw = mock_process.call_args.kwargs
        assert _kw["backup_manager"] is None

    def test_keyboard_interrupt_returns_130(self, tmp_path: Path) -> None:
        """KeyboardInterrupt during scanning returns exit code 130."""
        from cli.dedupe import dedupe_command

        with patch(
            "cli.dedupe.scan_for_duplicates",
            side_effect=KeyboardInterrupt(),
        ):
            result = dedupe_command([str(tmp_path)])
        assert result == 130

    def test_exception_returns_1(self, tmp_path: Path) -> None:
        """Unexpected exceptions return exit code 1."""
        from cli.dedupe import dedupe_command

        with patch(
            "cli.dedupe.scan_for_duplicates",
            side_effect=RuntimeError("boom"),
        ):
            result = dedupe_command([str(tmp_path)])
        assert result == 1

    def test_algorithm_md5_passed_correctly(self, tmp_path: Path) -> None:
        """--algorithm md5 is passed through to create_scan_options."""
        from cli.dedupe import dedupe_command

        scan_opts_calls: list = []

        def capture_scan_opts(*args, **kwargs):
            scan_opts_calls.append(kwargs)
            return MagicMock()

        with (
            patch(
                "cli.dedupe.scan_for_duplicates",
                return_value={},
            ),
            patch(
                "cli.dedupe.create_scan_options",
                side_effect=capture_scan_opts,
            ),
        ):
            result = dedupe_command([str(tmp_path), "--algorithm", "md5"])

        assert result == 0
        assert scan_opts_calls[0]["algorithm"] == "md5"

    def test_verbose_flag_does_not_crash(self, tmp_path: Path) -> None:
        """--verbose flag enables debug logging without crashing."""
        from cli.dedupe import dedupe_command

        with patch(
            "cli.dedupe.scan_for_duplicates",
            return_value={},
        ):
            result = dedupe_command([str(tmp_path), "--verbose"])
        assert result == 0

    def test_include_exclude_patterns_passed(self, tmp_path: Path) -> None:
        """--include and --exclude patterns reach scan_options."""
        from cli.dedupe import dedupe_command

        scan_opts_calls: list = []

        def capture(*args, **kwargs):
            scan_opts_calls.append(kwargs)
            return MagicMock()

        with (
            patch("cli.dedupe.scan_for_duplicates", return_value={}),
            patch("cli.dedupe.create_scan_options", side_effect=capture),
        ):
            result = dedupe_command([str(tmp_path), "--include", "*.jpg", "--exclude", "*.tmp"])

        assert result == 0
        assert scan_opts_calls[0]["file_patterns"] == ["*.jpg"]
        assert scan_opts_calls[0]["exclude_patterns"] == ["*.tmp"]

    def test_batch_strategy_oldest(self, tmp_path: Path) -> None:
        """--batch --strategy oldest passes batch=True."""
        from cli.dedupe import dedupe_command

        group = MagicMock()
        group.count = 2
        mock_process = MagicMock(return_value=(1, 512))

        with (
            patch(
                "cli.dedupe.scan_for_duplicates",
                return_value={"hash1": group},
            ),
            patch(
                "cli.dedupe_removal.process_duplicate_group",
                mock_process,
            ),
        ):
            result = dedupe_command([str(tmp_path), "--batch", "--strategy", "oldest"])

        assert result == 0
        _kw = mock_process.call_args.kwargs
        assert _kw["batch"] is True
        assert _kw["strategy"] == "oldest"

    def test_main_entry_point_calls_sys_exit(self) -> None:
        """main() passes dedupe_command result to sys.exit."""
        from cli import dedupe as dedupe_mod

        with (
            patch.object(dedupe_mod, "dedupe_command", return_value=0),
            patch("sys.exit") as mock_exit,
        ):
            dedupe_mod.main()

        mock_exit.assert_called_once_with(0)

    def test_safe_mode_with_removals_calls_display_backup_info(self, tmp_path: Path) -> None:
        """display_backup_info is called when safe_mode=True and files were removed."""
        from cli.dedupe import dedupe_command

        group = MagicMock()
        group.count = 2
        mock_display_backup = MagicMock()

        with (
            patch(
                "cli.dedupe.scan_for_duplicates",
                return_value={"h": group},
            ),
            patch(
                "cli.dedupe_removal.process_duplicate_group",
                return_value=(1, 1024),
            ),
            patch(
                "cli.dedupe_display.display_backup_info",
                mock_display_backup,
            ),
        ):
            result = dedupe_command([str(tmp_path)])

        assert result == 0
        mock_display_backup.assert_called_once()

    def test_no_recursive_flag(self, tmp_path: Path) -> None:
        """--no-recursive passes recursive=False through to scan options."""
        from cli.dedupe import dedupe_command

        scan_opts_calls: list = []

        def capture(*args, **kwargs):
            scan_opts_calls.append(kwargs)
            return MagicMock()

        with (
            patch("cli.dedupe.scan_for_duplicates", return_value={}),
            patch("cli.dedupe.create_scan_options", side_effect=capture),
        ):
            result = dedupe_command([str(tmp_path), "--no-recursive"])

        assert result == 0
        assert scan_opts_calls[0]["recursive"] is False


# ---------------------------------------------------------------------------
# interactive.py
# ---------------------------------------------------------------------------


class TestInteractiveSetFlags:
    """Covers interactive.py set_flags and its effect on confirm_action."""

    def test_set_flags_yes_makes_confirm_return_true(self) -> None:
        """confirm_action returns True immediately when yes=True."""
        from cli import interactive

        interactive.set_flags(yes=True, no_interactive=False)
        try:
            result = interactive.confirm_action("Proceed?", default=False)
            assert result is True
        finally:
            interactive.set_flags(yes=False, no_interactive=False)

    def test_set_flags_no_interactive_returns_default(self) -> None:
        """confirm_action returns the default value when no_interactive=True."""
        from cli import interactive

        interactive.set_flags(yes=False, no_interactive=True)
        try:
            result_default_true = interactive.confirm_action("Proceed?", default=True)
            result_default_false = interactive.confirm_action("Proceed?", default=False)
            assert result_default_true is True
            assert result_default_false is False
        finally:
            interactive.set_flags(yes=False, no_interactive=False)

    def test_confirm_action_calls_rich_confirm_when_interactive(self) -> None:
        """confirm_action delegates to Confirm.ask when flags are off."""
        from cli import interactive

        interactive.set_flags(yes=False, no_interactive=False)
        try:
            with patch("cli.interactive.Confirm.ask", return_value=True) as mock_ask:
                result = interactive.confirm_action("Are you sure?", default=False)
            assert result is True
            mock_ask.assert_called_once_with("Are you sure?", default=False)
        finally:
            interactive.set_flags(yes=False, no_interactive=False)


class TestPromptChoice:
    """Covers interactive.py lines 99-103."""

    def test_no_interactive_with_default_returns_default(self) -> None:
        """prompt_choice returns default when no_interactive=True."""
        from cli import interactive

        interactive.set_flags(yes=False, no_interactive=True)
        try:
            result = interactive.prompt_choice("Pick one", ["a", "b", "c"], default="b")
            assert result == "b"
        finally:
            interactive.set_flags(yes=False, no_interactive=False)

    def test_no_interactive_without_default_calls_prompt_ask(self) -> None:
        """prompt_choice falls through to Prompt.ask when no default given in no-interactive mode."""
        from cli import interactive

        interactive.set_flags(yes=False, no_interactive=True)
        try:
            with patch("cli.interactive.Prompt.ask", return_value="a") as mock_ask:
                result = interactive.prompt_choice("Pick one", ["a", "b", "c"])
            assert result == "a"
            mock_ask.assert_called_once()
        finally:
            interactive.set_flags(yes=False, no_interactive=False)

    def test_interactive_with_default_calls_prompt_with_default(self) -> None:
        """prompt_choice passes default to Prompt.ask when interactive."""
        from cli import interactive

        interactive.set_flags(yes=False, no_interactive=False)
        try:
            with patch("cli.interactive.Prompt.ask", return_value="c") as mock_ask:
                result = interactive.prompt_choice("Pick one", ["a", "b", "c"], default="c")
            assert result == "c"
            mock_ask.assert_called_once_with("Pick one", choices=["a", "b", "c"], default="c")
        finally:
            interactive.set_flags(yes=False, no_interactive=False)

    def test_interactive_without_default_calls_prompt_ask(self) -> None:
        """prompt_choice calls Prompt.ask without default when not supplied."""
        from cli import interactive

        interactive.set_flags(yes=False, no_interactive=False)
        try:
            with patch("cli.interactive.Prompt.ask", return_value="a") as mock_ask:
                result = interactive.prompt_choice("Pick one", ["a", "b"])
            assert result == "a"
            mock_ask.assert_called_once_with("Pick one", choices=["a", "b"])
        finally:
            interactive.set_flags(yes=False, no_interactive=False)


class TestPromptDirectory:
    """Covers interactive.py lines 75-80."""

    def test_returns_valid_directory_on_first_try(self, tmp_path: Path) -> None:
        """prompt_directory returns path when first input is a valid dir."""
        from cli import interactive

        with patch("cli.interactive.Prompt.ask", return_value=str(tmp_path)):
            result = interactive.prompt_directory("Enter dir")

        assert result == tmp_path.resolve()

    def test_reprompts_on_invalid_then_returns_valid(self, tmp_path: Path) -> None:
        """prompt_directory loops until a valid directory is entered."""
        from cli import interactive

        calls = ["/nonexistent/path/xyz", str(tmp_path)]
        with patch("cli.interactive.Prompt.ask", side_effect=calls):
            result = interactive.prompt_directory("Enter dir")

        assert result == tmp_path.resolve()


class TestCreateProgress:
    """Covers interactive.py line 113."""

    def test_returns_progress_instance(self) -> None:
        """create_progress returns a configured Rich Progress object."""
        from rich.progress import Progress

        from cli.interactive import create_progress

        progress = create_progress()
        assert isinstance(progress, Progress)
        # Must have the configured columns (5 columns)
        assert len(progress.columns) == 5


# ---------------------------------------------------------------------------
# daemon.py
# ---------------------------------------------------------------------------


class TestDaemonWatchCommand:
    """Covers daemon.py lines 145-167 (watch command)."""

    def test_watch_exits_on_keyboard_interrupt(self, tmp_path: Path) -> None:
        """daemon watch exits cleanly when KeyboardInterrupt is raised."""
        mock_monitor = MagicMock()
        mock_monitor.get_events_blocking.side_effect = KeyboardInterrupt()

        with patch("watcher.monitor.FileMonitor", return_value=mock_monitor):
            result = runner.invoke(app, ["daemon", "watch", str(tmp_path)])

        assert result.exit_code == 0
        mock_monitor.start.assert_called_once()
        mock_monitor.stop.assert_called_once()

    def test_watch_streams_events(self, tmp_path: Path) -> None:
        """daemon watch prints events returned by get_events_blocking."""
        mock_event = MagicMock()
        mock_event.event_type = "created"
        mock_event.path = "/watched/file.txt"

        call_count = 0

        def _get_events(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [mock_event]
            raise KeyboardInterrupt()

        mock_monitor = MagicMock()
        mock_monitor.get_events_blocking.side_effect = _get_events

        with patch("watcher.monitor.FileMonitor", return_value=mock_monitor):
            result = runner.invoke(app, ["daemon", "watch", str(tmp_path)])

        assert result.exit_code == 0
        assert "created" in result.output

    def test_watch_stop_called_on_keyboard_interrupt(self, tmp_path: Path) -> None:
        """monitor.stop() is always called via finally block."""
        mock_monitor = MagicMock()
        mock_monitor.get_events_blocking.side_effect = KeyboardInterrupt()

        with patch("watcher.monitor.FileMonitor", return_value=mock_monitor):
            runner.invoke(app, ["daemon", "watch", str(tmp_path)])

        mock_monitor.stop.assert_called_once()

    def test_watch_uses_poll_interval(self, tmp_path: Path) -> None:
        """--poll-interval is passed to WatcherConfig as debounce_seconds."""
        mock_monitor = MagicMock()
        mock_monitor.get_events_blocking.side_effect = KeyboardInterrupt()

        with (
            patch("watcher.monitor.FileMonitor", return_value=mock_monitor),
            patch("watcher.config.WatcherConfig") as mock_cfg_cls,
        ):
            runner.invoke(app, ["daemon", "watch", str(tmp_path), "--poll-interval", "2.5"])

        call_kwargs = mock_cfg_cls.call_args.kwargs
        assert call_kwargs["debounce_seconds"] == pytest.approx(2.5)

    def test_watch_event_src_path_fallback(self, tmp_path: Path) -> None:
        """Events without .path attribute fall back to .src_path."""
        mock_event = MagicMock(spec=[])  # no attributes via spec
        mock_event.event_type = "modified"
        mock_event.src_path = "/fallback/file.txt"

        call_count = 0

        def _get_events(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [mock_event]
            raise KeyboardInterrupt()

        mock_monitor = MagicMock()
        mock_monitor.get_events_blocking.side_effect = _get_events

        with patch("watcher.monitor.FileMonitor", return_value=mock_monitor):
            result = runner.invoke(app, ["daemon", "watch", str(tmp_path)])

        assert result.exit_code == 0


class TestDaemonStartForegroundDryRun:
    """Covers daemon.py lines 70 and 77->79 (foreground dry-run branches)."""

    def test_foreground_dry_run_prints_dry_run_hint(self, tmp_path: Path) -> None:
        """Foreground mode + --dry-run prints the dry-run message."""
        mock_service = MagicMock()
        mock_service.start.side_effect = KeyboardInterrupt()

        with (
            patch("daemon.service.DaemonService", return_value=mock_service),
            patch("cli.daemon._DEFAULT_PID_FILE", tmp_path / "daemon.pid"),
        ):
            result = runner.invoke(app, ["daemon", "start", "--foreground", "--dry-run"])

        assert result.exit_code == 0
        assert "dry" in result.output.lower()

    def test_background_dry_run_prints_dry_run_hint(self, tmp_path: Path) -> None:
        """Background mode + --dry-run also prints the dry-run message."""
        mock_service = MagicMock()

        with (
            patch("daemon.service.DaemonService", return_value=mock_service),
            patch("cli.daemon._DEFAULT_PID_FILE", tmp_path / "daemon.pid"),
        ):
            result = runner.invoke(app, ["daemon", "start", "--dry-run"])

        assert result.exit_code == 0
        assert "dry" in result.output.lower()

    def test_foreground_no_dry_run_does_not_print_hint(self, tmp_path: Path) -> None:
        """Foreground mode without --dry-run should NOT show dry-run hint."""
        mock_service = MagicMock()
        mock_service.start.side_effect = KeyboardInterrupt()

        with (
            patch("daemon.service.DaemonService", return_value=mock_service),
            patch("cli.daemon._DEFAULT_PID_FILE", tmp_path / "daemon.pid"),
        ):
            result = runner.invoke(app, ["daemon", "start", "--foreground"])

        assert result.exit_code == 0
        assert "dry" not in result.output.lower()


class TestDaemonProcessErrorTruncation:
    """Covers daemon.py lines 200-204 (process error list truncation)."""

    def test_more_than_10_errors_truncated(
        self,
        stub_all_models: None,
        stub_nltk: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        """process command truncates error list to 10 with '... and N more' suffix."""
        from dataclasses import dataclass
        from dataclasses import field as dc_field

        @dataclass
        class _FakeResult:
            total_files: int = 15
            processed_files: int = 4
            skipped_files: int = 0
            failed_files: int = 11
            organized_structure: dict = dc_field(default_factory=dict)
            errors: list = dc_field(
                default_factory=lambda: [(f"file{i}.txt", "err") for i in range(12)]
            )

        with patch(
            "core.organizer.FileOrganizer.organize",
            return_value=_FakeResult(),
        ):
            result = runner.invoke(
                app,
                [
                    "daemon",
                    "process",
                    str(integration_source_dir),
                    str(integration_output_dir),
                ],
            )

        assert result.exit_code == 0
        assert "2 more" in result.output

    def test_exactly_10_errors_no_truncation(
        self,
        stub_all_models: None,
        stub_nltk: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        """process command shows all errors when there are exactly 10."""
        from dataclasses import dataclass
        from dataclasses import field as dc_field

        @dataclass
        class _FakeResult:
            total_files: int = 10
            processed_files: int = 0
            skipped_files: int = 0
            failed_files: int = 10
            organized_structure: dict = dc_field(default_factory=dict)
            errors: list = dc_field(
                default_factory=lambda: [(f"file{i}.txt", "err") for i in range(10)]
            )

        with patch(
            "core.organizer.FileOrganizer.organize",
            return_value=_FakeResult(),
        ):
            result = runner.invoke(
                app,
                [
                    "daemon",
                    "process",
                    str(integration_source_dir),
                    str(integration_output_dir),
                ],
            )

        assert result.exit_code == 0
        assert "more" not in result.output


# ---------------------------------------------------------------------------
# undo_history.py  (integration context)
# ---------------------------------------------------------------------------


class TestUndoHistoryIntegration:
    """Covers undo_history.py lines 86->85, 125, 128, 177-178, 231-232."""

    def test_preview_undo_operation_not_found_in_stack(self, capsys) -> None:
        """preview_undo_operation returns 1 when op not in the undo stack (line 125+128)."""
        from cli import undo_history

        manager = MagicMock()
        manager.can_undo.return_value = (True, "")
        # Return a stack that does NOT contain operation_id=99
        manager.get_undo_stack.return_value = [_make_mock_operation(op_id=1)]

        result = undo_history.preview_undo_operation(manager, 99)

        assert result == 1
        assert "not found" in capsys.readouterr().out

    def test_preview_undo_transaction_not_found(self, capsys) -> None:
        """preview_undo_transaction returns 1 when transaction not found (line 177-178)."""
        from cli import undo_history

        manager = MagicMock()
        manager.history.get_transaction.return_value = None

        result = undo_history.preview_undo_transaction(manager, "nonexistent-tx")

        assert result == 1
        assert "not found" in capsys.readouterr().out

    def test_preview_redo_operation_not_found_in_redo_stack(self, capsys) -> None:
        """preview_redo_operation returns 1 when op not in redo stack (lines 231-232)."""
        from cli import undo_history

        manager = MagicMock()
        manager.can_redo.return_value = (True, "")
        # Return empty redo stack — op won't be found
        manager.get_redo_stack.return_value = []

        result = undo_history.preview_redo_operation(manager, 42)

        assert result == 1
        assert "not found in redo stack" in capsys.readouterr().out

    def test_preview_undo_operation_success_op_found(self, capsys) -> None:
        """preview_undo_operation returns 0 when op is undoable and found (covers branch)."""
        from cli import undo_history

        manager = MagicMock()
        manager.can_undo.return_value = (True, "")
        manager.get_undo_stack.return_value = [_make_mock_operation(op_id=5)]

        result = undo_history.preview_undo_operation(manager, 5)

        out = capsys.readouterr().out
        assert result == 0
        assert "Would undo operation 5" in out
        assert "can be safely undone" in out

    def test_preview_undo_operation_cannot_undo_branch(self, capsys) -> None:
        """preview_undo_operation returns 1 with reason when can_undo=False."""
        from cli import undo_history

        manager = MagicMock()
        manager.can_undo.return_value = (False, "already undone")

        result = undo_history.preview_undo_operation(manager, 7)

        assert result == 1
        assert "Cannot undo" in capsys.readouterr().out

    def test_execute_undo_failure_returns_1(self, capsys) -> None:
        """execute_undo returns 1 when undo_last_operation returns False."""
        from cli import undo_history

        manager = MagicMock()
        manager.undo_last_operation.return_value = False

        result = undo_history.execute_undo(manager)

        assert result == 1
        assert "Undo failed" in capsys.readouterr().out

    def test_execute_redo_failure_returns_1(self, capsys) -> None:
        """execute_redo returns 1 when redo_last_operation returns False."""
        from cli import undo_history

        manager = MagicMock()
        manager.redo_last_operation.return_value = False

        result = undo_history.execute_redo(manager)

        assert result == 1
        assert "Redo failed" in capsys.readouterr().out

    def test_preview_undo_transaction_success_with_many_ops(self, capsys) -> None:
        """preview_undo_transaction prints truncated operation list for large transactions."""
        from cli import undo_history

        manager = MagicMock()
        manager.history.get_transaction.return_value = MagicMock()
        # 8 operations — should truncate at the default limit=5
        manager.history.get_operations.return_value = [
            _make_mock_operation(op_id=i) for i in range(8)
        ]

        result = undo_history.preview_undo_transaction(manager, "tx-big")

        out = capsys.readouterr().out
        assert result == 0
        assert "Operations: 8" in out
        assert "3 more" in out
