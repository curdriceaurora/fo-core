"""Tests for daemon signal handler deadlock fix (self-pipe pattern) and coverage gaps."""

from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path

import pytest

from daemon.config import DaemonConfig
from daemon.service import DaemonService

from .conftest import wired_pipe

pytestmark = pytest.mark.unit


def _make_config(**kwargs) -> DaemonConfig:
    """Create a test DaemonConfig with sensible defaults.

    Args:
        **kwargs: Overrides for config fields. Default values:
            - watch_directories: []
            - output_directory: Path("tmp/organized")
            - pid_file: None
            - poll_interval: 0.05

    Returns:
        A DaemonConfig instance with specified or default values.
    """
    defaults = {
        "watch_directories": [],
        "output_directory": Path("tmp/organized"),
        "pid_file": None,
        "poll_interval": 0.05,
    }
    defaults.update(kwargs)
    return DaemonConfig(**defaults)


# ---------------------------------------------------------------------------
# Self-pipe signal handler tests
# ---------------------------------------------------------------------------


@pytest.mark.ci
class TestSignalHandlerWritesToPipe:
    """TestSignalHandlerWritesToPipe test suite."""

    def test_signal_handler_writes_byte_to_pipe(self) -> None:
        """Verify signal handler writes a byte to the wakeup pipe."""
        daemon = DaemonService(_make_config())
        with wired_pipe(daemon) as (r, _w):
            daemon._handle_signal(signal.SIGTERM, None)
            data = os.read(r, 1024)
            assert data == b"\x00", f"Signal handler should write null byte to pipe, got {data!r}"

    def test_signal_handler_tolerates_closed_pipe(self) -> None:
        """Verify signal handler gracefully handles a closed write pipe."""
        daemon = DaemonService(_make_config())
        _r, w = os.pipe()
        os.close(_r)
        os.close(w)
        daemon._sig_wakeup_w = w

        # Should not raise — OSError is caught internally
        try:
            daemon._handle_signal(signal.SIGTERM, None)
        except OSError:
            pytest.fail("Signal handler should tolerate closed pipe")

    def test_signal_handler_tolerates_none_pipe(self) -> None:
        """Verify signal handler works correctly when no pipe is initialized."""
        daemon = DaemonService(_make_config())
        assert daemon._sig_wakeup_w is None, "Initial pipe write FD should be None"
        # Should not raise when pipe is None
        try:
            daemon._handle_signal(signal.SIGTERM, None)
        except TypeError:
            pytest.fail("Signal handler should tolerate None pipe")


@pytest.mark.ci
class TestRunLoopExitsOnPipeSignal:
    """TestRunLoopExitsOnPipeSignal test suite."""

    @pytest.mark.skipif(sys.platform == "win32", reason="signal pipe not available on Windows")
    def test_run_loop_exits_on_pipe_signal(self) -> None:
        """Verify run loop exits when signal is written to pipe."""
        daemon = DaemonService(_make_config())
        with wired_pipe(daemon) as (_r, w):
            # Write a byte to simulate a signal arrival
            os.write(w, b"\x00")
            daemon._run_loop()
            assert daemon._stop_event.is_set(), "Run loop should set stop event after pipe signal"

    def test_run_loop_falls_back_to_event_wait(self) -> None:
        """Verify run loop falls back to event.wait when no pipe available.

        This tests the Windows-compatibility path where select() is not available
        and the loop waits on the stop event directly.
        """
        daemon = DaemonService(_make_config())
        assert daemon._sig_wakeup_r is None, "No pipe should be created initially"

        # Signal ready from *inside* the stop-event wait so there is no race
        # between setting ready and actually entering the blocking wait.
        ready = threading.Event()
        original_wait = daemon._stop_event.wait

        def _wait_and_signal(timeout: float | None = None) -> bool:
            ready.set()
            return original_wait(timeout)

        daemon._stop_event.wait = _wait_and_signal  # type: ignore[method-assign]

        def stop_later() -> None:
            """Set stop event after the run loop is waiting."""
            ready.wait(timeout=5.0)
            daemon._stop_event.set()

        t = threading.Thread(target=stop_later)
        t.start()
        daemon._run_loop()
        t.join(timeout=2.0)
        assert daemon._stop_event.is_set(), "Stop event should be set after event.wait path"


@pytest.mark.skipif(sys.platform == "win32", reason="signal pipe not available on Windows")
class TestPipeClosedOnRestore:
    """TestPipeClosedOnRestore test suite."""

    def test_pipe_closed_on_restore(self) -> None:
        """Verify pipes are properly closed when signal handlers are restored."""
        daemon = DaemonService(_make_config())

        # Call from main thread so it doesn't skip
        daemon._install_signal_handlers()

        # Verify pipe was created
        assert daemon._sig_wakeup_r is not None, (
            "Read FD should be created after installing handlers"
        )
        assert daemon._sig_wakeup_w is not None, (
            "Write FD should be created after installing handlers"
        )
        r_fd = daemon._sig_wakeup_r
        w_fd = daemon._sig_wakeup_w

        daemon._restore_signal_handlers()

        # Verify pipe fds are cleared
        assert daemon._sig_wakeup_r is None, "Read FD should be None after restoring handlers"
        assert daemon._sig_wakeup_w is None, "Write FD should be None after restoring handlers"

        # Verify fds are actually closed (os.fstat should fail)
        with pytest.raises(OSError):
            os.fstat(r_fd)
        with pytest.raises(OSError):
            os.fstat(w_fd)


# ---------------------------------------------------------------------------
# Coverage gap tests (lines 215, 301-304, 316-320)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="signal pipe not available on Windows")
class TestInstallSignalHandlersMainThread:
    """TestInstallSignalHandlersMainThread test suite."""

    def test_install_signal_handlers_success_main_thread(self):
        """Cover lines 301-304: signal handler installation in main thread."""
        daemon = DaemonService(_make_config())

        try:
            daemon._install_signal_handlers()
            assert daemon._original_sigterm is not None, "Original SIGTERM handler should be saved"
            assert daemon._original_sigint is not None, "Original SIGINT handler should be saved"
            assert daemon._sig_wakeup_r is not None, "Pipe read FD should be created"
            assert daemon._sig_wakeup_w is not None, "Pipe write FD should be created"
        finally:
            daemon._restore_signal_handlers()


class TestRestoreSignalHandlersMainThread:
    """TestRestoreSignalHandlersMainThread test suite."""

    def test_restore_signal_handlers_success_main_thread(self):
        """Cover lines 316-320: signal handler restoration in main thread."""
        daemon = DaemonService(_make_config())

        # Set up state as if handlers were installed
        r, w = os.pipe()
        daemon._original_sigterm = signal.SIG_DFL
        daemon._original_sigint = signal.SIG_DFL
        daemon._sig_wakeup_r = r
        daemon._sig_wakeup_w = w

        daemon._restore_signal_handlers()

        assert daemon._original_sigterm is None, (
            "Original SIGTERM handler should be cleared after restore"
        )
        assert daemon._original_sigint is None, (
            "Original SIGINT handler should be cleared after restore"
        )
        assert daemon._sig_wakeup_r is None, "Pipe read FD should be closed and cleared"
        assert daemon._sig_wakeup_w is None, "Pipe write FD should be closed and cleared"


# ---------------------------------------------------------------------------
# F4 hardening — signal-write failure counter + drain
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestSignalWriteFailureTracking:
    """F4 (hardening roadmap #159): the signal handler used to swallow
    ``OSError`` silently. If the pipe was saturated or closed, shutdown
    signals could be lost with no observability. Post-F4 failures bump
    ``_signal_write_failures`` and the run loop logs them.
    """

    def test_counter_starts_at_zero(self) -> None:
        daemon = DaemonService(_make_config())
        assert daemon._signal_write_failures == 0
        assert daemon._last_logged_write_failures == 0

    def test_closed_pipe_increments_failure_counter(self) -> None:
        """Pre-F4: OSError was pass-ed silently. Post-F4: counter bumps."""
        daemon = DaemonService(_make_config())
        r, w = os.pipe()
        os.close(r)
        os.close(w)
        daemon._sig_wakeup_w = w

        assert daemon._signal_write_failures == 0
        daemon._handle_signal(signal.SIGTERM, None)
        assert daemon._signal_write_failures == 1
        daemon._handle_signal(signal.SIGTERM, None)
        assert daemon._signal_write_failures == 2

    def test_successful_write_does_not_bump_counter(self) -> None:
        daemon = DaemonService(_make_config())
        with wired_pipe(daemon) as (r, _w):
            daemon._handle_signal(signal.SIGTERM, None)
            os.read(r, 1024)  # drain
        assert daemon._signal_write_failures == 0

    def test_log_helper_emits_once_per_delta(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_log_signal_write_failures_if_new`` is idempotent between
        observed counter values — no log spam on every loop iteration
        when the counter is stable.

        The helper also enforces a minimum interval between emits (F4
        rate limiting). We disable that interval here so the per-delta
        contract is tested independently; the rate-limit guarantee is
        exercised in ``test_log_helper_rate_limits_bursts`` below.
        """
        monkeypatch.setattr("daemon.service._SIGNAL_LOG_MIN_INTERVAL_S", 0.0)
        daemon = DaemonService(_make_config())
        daemon._signal_write_failures = 3
        with caplog.at_level("WARNING", logger="daemon.service"):
            daemon._log_signal_write_failures_if_new()
            daemon._log_signal_write_failures_if_new()  # no counter change
            daemon._log_signal_write_failures_if_new()

        warnings = [r for r in caplog.records if "write failures" in r.message]
        assert len(warnings) == 1
        assert "(total: 3)" in warnings[0].message
        assert daemon._last_logged_write_failures == 3

        # New delta after the stable run — a second WARNING must fire
        # and its payload must include the new total, not just flip
        # internal state.
        daemon._signal_write_failures = 5
        with caplog.at_level("WARNING", logger="daemon.service"):
            daemon._log_signal_write_failures_if_new()
        warnings = [r for r in caplog.records if "write failures" in r.message]
        assert len(warnings) == 2, "second WARNING did not fire after counter increased from 3 to 5"
        assert "(total: 5)" in warnings[1].message
        assert daemon._last_logged_write_failures == 5

    def test_log_helper_rate_limits_bursts(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """F4 rate limit: back-to-back deltas inside the min interval
        window produce only one WARNING. When the window has elapsed,
        a subsequent delta logs again."""
        daemon = DaemonService(_make_config())

        fake_now = [100.0]

        def _now() -> float:
            return fake_now[0]

        monkeypatch.setattr("daemon.service.time.monotonic", _now)
        monkeypatch.setattr("daemon.service._SIGNAL_LOG_MIN_INTERVAL_S", 1.0)

        with caplog.at_level("WARNING", logger="daemon.service"):
            daemon._signal_write_failures = 1
            daemon._log_signal_write_failures_if_new()  # emits
            fake_now[0] = 100.5  # inside the 1.0s window
            daemon._signal_write_failures = 4
            daemon._log_signal_write_failures_if_new()  # suppressed
            fake_now[0] = 101.6  # past the window
            daemon._signal_write_failures = 7
            daemon._log_signal_write_failures_if_new()  # emits

        warnings = [r for r in caplog.records if "write failures" in r.message]
        assert len(warnings) == 2, "rate limit did not suppress the burst"
        assert "(total: 1)" in warnings[0].message
        assert "(total: 7)" in warnings[1].message


@pytest.mark.unit
@pytest.mark.ci
class TestSignalPipeDrain:
    """F4: the run loop drains the signal pipe in a loop until EAGAIN,
    instead of the pre-F4 fixed 1024-byte read. Prevents stale wakeup
    bytes from lingering after a signal storm."""

    def test_drain_empties_pipe(self) -> None:
        """Multiple wakeup bytes are all consumed in one drain."""
        daemon = DaemonService(_make_config())
        with wired_pipe(daemon) as (r, w):
            # Fill the pipe with many wakeup bytes.
            for _ in range(10):
                os.write(w, b"\x00")
            daemon._drain_signal_pipe(r)
            # Pipe is now empty — next read raises BlockingIOError.
            with pytest.raises(BlockingIOError):
                os.read(r, 1)

    def test_drain_tolerates_closed_fd(self) -> None:
        """Reading from a closed fd returns cleanly (logged at DEBUG,
        no raise) so the run loop keeps turning.

        Uses a sentinel fd via ``os.dup`` so that after closing, the fd
        number stays invalid for the duration of this test: ``os.dup``
        allocates a new descriptor number we own, and closing only that
        one leaves the original pipe ends intact. Under xdist, plain
        ``os.close(r)`` + ``os.read(r, ...)`` races against the Python
        runtime reallocating ``r``'s integer value to an unrelated open
        file — the assertion "must not raise" would then pass for the
        wrong reason (or consume bytes from an unrelated fd).
        """
        daemon = DaemonService(_make_config())
        r, w = os.pipe()
        try:
            sentinel = os.dup(r)
            os.close(sentinel)
            # ``sentinel`` is now a stable closed fd number — the runtime
            # can still reuse it, but because we never expose ``r`` or
            # ``w`` to the read path, the test doesn't consume their
            # buffers on a race.
            daemon._drain_signal_pipe(sentinel)
        finally:
            os.close(r)
            os.close(w)

    def test_drain_handles_empty_pipe(self) -> None:
        """A drain on an empty pipe returns immediately."""
        daemon = DaemonService(_make_config())
        with wired_pipe(daemon) as (r, _w):
            daemon._drain_signal_pipe(r)  # must not hang or raise
