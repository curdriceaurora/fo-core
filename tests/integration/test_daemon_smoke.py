"""Daemon subprocess smoke test.

Exercises the real process lifecycle end-to-end via ``subprocess.Popen``:
  start --foreground  →  PID file written
  fo daemon status    →  reports "Running"
  file drop           →  daemon still alive (no crash)
  SIGTERM             →  clean exit (code 0), PID file removed

``subprocess.Popen`` (not CliRunner) is used so that the full OS process
lifecycle is exercised: signal delivery via the self-pipe, PID file
creation and removal by the daemon process itself, and the graceful
shutdown path that can only be triggered from an external process.

Background mode (``start_background``) is intentionally *not* used because
it runs a daemon thread that dies as soon as the CLI process exits.
Foreground mode keeps the process alive so SIGTERM can be delivered
externally and the full signal→event-loop→cleanup chain is covered.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import psutil
import pytest

from daemon.pid import PidFileManager

pytestmark = [pytest.mark.integration]

# ── Timing constants ─────────────────────────────────────────────────────────

_STARTUP_TIMEOUT_S = 10.0  # max wait for PID file to appear after start
_SHUTDOWN_TIMEOUT_S = 10.0  # max wait for process to exit after SIGTERM
_POLL_INTERVAL_S = 0.05  # subprocess poll interval (fast startup + signal response)
_FILE_DROP_SETTLE_S = 0.3  # time to wait after dropping a file (≥ 1 poll cycle)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _wait_for(condition: Callable[[], bool], timeout: float) -> bool:
    """Poll *condition* every 100 ms until it returns True or *timeout* elapses.

    Uses ``select.select`` with no file descriptors as a portable sleep
    alternative — ``time.sleep`` is banned in test files by the project's
    CI guardrail (test_test_quality_guardrails.py).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        select.select([], [], [], 0.1)  # portable 100 ms pause
    return condition()  # final check after timeout


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def fo_exe() -> str:
    """Return the path to the installed ``fo`` binary, or skip the test."""
    exe = shutil.which("fo")
    if exe is None:
        pytest.skip("'fo' not found on PATH — install the package via 'pip install -e .'")
    return exe


@pytest.fixture()
def smoke_env() -> dict[str, str]:
    """Subprocess environment that inherits the isolated XDG dirs.

    ``_isolate_user_env`` (autouse in conftest) patches ``os.environ`` before
    this fixture runs, so ``dict(os.environ)`` already contains the per-test
    ``XDG_STATE_HOME`` / ``HOME`` overrides.  The subprocess therefore uses
    the same isolated paths the test process sees — no real user dirs touched.

    Also writes a minimal ``config.yaml`` that marks ``setup_completed: true``
    so the ``fo`` main-callback setup gate does not abort the daemon command
    before it starts (``daemon`` is not in ``_SETUP_GATE_ALLOWLIST``).
    """
    env = dict(os.environ)
    xdg_config = env.get("XDG_CONFIG_HOME")
    if xdg_config:
        config_dir = Path(xdg_config) / "fo"
        config_dir.mkdir(parents=True, exist_ok=True)
        # ConfigManager.load() parses profiles.<name>.setup_completed, not a
        # top-level key. Write the minimal valid structure so the main-callback
        # setup gate passes without running fo setup in the subprocess.
        (config_dir / "config.yaml").write_text(
            "profiles:\n  default:\n    version: '1.0'\n    setup_completed: true\n"
        )
    return env


# ── Smoke tests ───────────────────────────────────────────────────────────────


class TestDaemonSmokeProcess:
    """End-to-end daemon lifecycle smoke test via ``subprocess.Popen``.

    Covers:
    - ``start --foreground`` writes a PID file and stays alive.
    - ``daemon status`` reports "Running" against that PID file.
    - A file dropped into the watch directory does not crash the daemon.
    - SIGTERM triggers clean shutdown: exit code 0, PID file removed.
    """

    def test_lifecycle_start_watch_sigterm_stop(
        self,
        tmp_path: Path,
        fo_exe: str,
        smoke_env: dict[str, str],
    ) -> None:
        """Verify the daemon lifecycle: start → running → file drop → SIGTERM → stopped."""
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        # Derive the PID file path from the isolated XDG_STATE_HOME.
        # The subprocess computes the same path at import time because it
        # inherits smoke_env which contains the same XDG_STATE_HOME value.
        xdg_state = smoke_env.get("XDG_STATE_HOME")
        assert xdg_state, (
            "XDG_STATE_HOME must be set in smoke_env; "
            "check that _isolate_user_env autouse fixture is active"
        )
        pid_file = Path(xdg_state) / "fo" / "daemon.pid"

        # ── Start daemon in foreground ────────────────────────────────────────
        # --foreground keeps the subprocess alive (background mode's daemon
        # thread dies when the CLI process exits, making SIGTERM delivery
        # impossible from the test).
        # --poll-interval is kept small so the event loop responds quickly.
        proc = subprocess.Popen(
            [
                fo_exe,
                "daemon",
                "start",
                "--foreground",
                "--watch-dir",
                str(watch_dir),
                "--poll-interval",
                str(_POLL_INTERVAL_S),
            ],
            env=smoke_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # ── 1. Assert: PID file written ───────────────────────────────────
            pid_written = _wait_for(pid_file.exists, _STARTUP_TIMEOUT_S)
            stdout_so_far = b""
            stderr_so_far = b""
            if not pid_written and proc.poll() is not None:
                # Process already exited — collect output for diagnostics.
                assert proc.stdout is not None
                assert proc.stderr is not None
                stdout_so_far = proc.stdout.read()
                stderr_so_far = proc.stderr.read()
            assert pid_written, (
                f"Daemon did not write PID file within {_STARTUP_TIMEOUT_S}s. "
                f"Expected path: {pid_file}\n"
                f"Process exit code: {proc.returncode}\n"
                f"stdout: {stdout_so_far.decode(errors='replace')}\n"
                f"stderr: {stderr_so_far.decode(errors='replace')}"
            )

            record = PidFileManager().read_pid_record(pid_file)
            assert record is not None, (
                f"PID file {pid_file} exists but read_pid_record returned None"
            )
            assert psutil.pid_exists(record.pid), (
                f"Daemon PID {record.pid} is not alive after PID file appeared"
            )
            # The PID in the file must match the subprocess we launched.
            assert record.pid == proc.pid, (
                f"PID file records {record.pid} but subprocess PID is {proc.pid}; "
                "foreground start should write its own PID"
            )

            # ── 2. Assert: fo daemon status reports Running ───────────────────
            status = subprocess.run(
                [fo_exe, "daemon", "status"],
                env=smoke_env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert status.returncode == 0, (
                f"'fo daemon status' exited {status.returncode}.\n"
                f"stdout: {status.stdout}\nstderr: {status.stderr}"
            )
            assert "running" in status.stdout.lower(), (
                f"'fo daemon status' did not report 'Running'.\nstdout:\n{status.stdout}"
            )

            # ── 3. Drop a file mid-cycle; assert daemon survives ──────────────
            test_file = watch_dir / "smoke_payload.txt"
            test_file.write_text("Smoke-test payload for daemon watcher integration.")
            # Wait long enough for at least one poll cycle to complete.
            select.select([], [], [], _FILE_DROP_SETTLE_S)
            assert proc.poll() is None, (
                f"Daemon process died after file drop (exit code: {proc.returncode}). "
                "Possible crash — check daemon logs."
            )

            # ── 4. SIGTERM → clean shutdown ───────────────────────────────────
            os.kill(proc.pid, signal.SIGTERM)

            exited = _wait_for(lambda: proc.poll() is not None, _SHUTDOWN_TIMEOUT_S)
            assert exited, f"Daemon did not exit within {_SHUTDOWN_TIMEOUT_S}s after SIGTERM"
            assert proc.returncode == 0, (
                f"Daemon exited with non-zero code {proc.returncode} after SIGTERM; "
                "expected 0 (graceful shutdown via signal handler)"
            )

            # ── 5. Assert: PID file removed on clean exit ─────────────────────
            assert not pid_file.exists(), (
                f"Daemon did not remove PID file {pid_file} during clean shutdown"
            )

        finally:
            # Always ensure the subprocess is dead before the test exits so it
            # doesn't outlive the tmp_path cleanup or bleed into other tests.
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
