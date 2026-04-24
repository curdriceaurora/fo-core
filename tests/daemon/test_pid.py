"""
Unit tests for PidFileManager.

Tests PID file creation, reading, removal, and liveness checking
with both real and synthetic PID values.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psutil
import pytest

from daemon.pid import _CREATE_TIME_TOLERANCE_S, PidFileManager

pytestmark = [pytest.mark.unit, pytest.mark.smoke, pytest.mark.ci]


@pytest.fixture
def pid_manager() -> PidFileManager:
    """Create a PidFileManager instance."""
    return PidFileManager()


@pytest.fixture
def pid_file(tmp_path: Path) -> Path:
    """Return a temporary PID file path (not yet created)."""
    return tmp_path / "test_daemon.pid"


@pytest.mark.unit
class TestWritePid:
    """Tests for PidFileManager.write_pid."""

    def test_write_current_pid(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """write_pid writes the current PID by default."""
        pid_manager.write_pid(pid_file)

        assert pid_file.exists()
        content = pid_file.read_text().strip()
        assert int(content) == os.getpid()

    def test_write_specific_pid(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """write_pid writes the provided PID when given."""
        pid_manager.write_pid(pid_file, pid=12345)

        content = pid_file.read_text().strip()
        assert content == "12345"

    def test_write_creates_parent_directories(
        self, pid_manager: PidFileManager, tmp_path: Path
    ) -> None:
        """write_pid creates parent directories if missing."""
        nested = tmp_path / "a" / "b" / "c" / "daemon.pid"
        pid_manager.write_pid(nested)

        assert nested.exists()

    def test_write_overwrites_existing(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """write_pid overwrites an existing PID file."""
        pid_manager.write_pid(pid_file, pid=111)
        pid_manager.write_pid(pid_file, pid=222)

        assert pid_manager.read_pid(pid_file) == 222


@pytest.mark.unit
class TestReadPid:
    """Tests for PidFileManager.read_pid."""

    def test_read_existing_pid(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """read_pid returns the PID from an existing file."""
        pid_file.write_text("42")
        assert pid_manager.read_pid(pid_file) == 42

    def test_read_nonexistent_returns_none(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """read_pid returns None when the file does not exist."""
        assert pid_manager.read_pid(pid_file) is None

    def test_read_empty_file_returns_none(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """read_pid returns None for an empty file."""
        pid_file.write_text("")
        assert pid_manager.read_pid(pid_file) is None

    def test_read_invalid_content_returns_none(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """read_pid returns None when the file contains non-numeric data."""
        pid_file.write_text("not-a-pid")
        assert pid_manager.read_pid(pid_file) is None

    def test_read_whitespace_stripped(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """read_pid strips whitespace before parsing."""
        pid_file.write_text("  99  \n")
        assert pid_manager.read_pid(pid_file) == 99


@pytest.mark.unit
class TestRemovePid:
    """Tests for PidFileManager.remove_pid."""

    def test_remove_existing_file(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """remove_pid deletes the PID file and returns True."""
        pid_file.write_text("42")
        assert pid_manager.remove_pid(pid_file) is True
        assert not pid_file.exists()

    def test_remove_nonexistent_returns_false(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """remove_pid returns False for a nonexistent file."""
        assert pid_manager.remove_pid(pid_file) is False


@pytest.mark.unit
class TestIsRunning:
    """Tests for PidFileManager.is_running."""

    def test_current_process_is_running(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """is_running returns True for the current process."""
        pid_manager.write_pid(pid_file)
        assert pid_manager.is_running(pid_file) is True

    def test_nonexistent_file_not_running(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """is_running returns False when the PID file does not exist."""
        assert pid_manager.is_running(pid_file) is False

    def test_dead_pid_not_running(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """is_running returns False for a PID that can never be valid.

        Uses PID -1, which psutil.pid_exists() treats as False on all
        platforms (psutil returns False for any negative PID before making
        any OS call). Previously os.kill(pid, 0) was used which maps to
        CTRL_C_EVENT=0 on Windows and would fire a real KeyboardInterrupt
        into the pytest process.
        """
        pid_file.write_text("-1")
        assert pid_manager.is_running(pid_file) is False

    def test_empty_file_not_running(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """is_running returns False for an empty PID file."""
        pid_file.write_text("")
        assert pid_manager.is_running(pid_file) is False


# ---------------------------------------------------------------------------
# F2 hardening — PID-reuse race closed by create_time validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestPidRecord:
    """F2 (hardening roadmap #159): ``write_pid_record`` / ``read_pid_record``
    capture both PID and process start-time so ``is_running`` can detect
    and reject PID recycling after daemon death.

    Pre-F2: a crashed daemon's PID gets reused by the OS for an unrelated
    process, and ``is_running`` reports the daemon as alive. Post-F2:
    start-time mismatch is caught and treated as not-running.
    """

    def test_write_pid_record_captures_current_process_time(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:

        record = pid_manager.write_pid_record(pid_file)
        assert record.pid == os.getpid()
        assert record.create_time is not None
        # Must match the actual create_time of this process (within tolerance).
        # Use the same constant ``is_running`` compares against so the test
        # stays aligned if the tolerance is ever retuned.
        expected = psutil.Process(os.getpid()).create_time()
        assert abs(record.create_time - expected) < _CREATE_TIME_TOLERANCE_S

    def test_record_is_json_format(self, pid_manager: PidFileManager, pid_file: Path) -> None:

        pid_manager.write_pid_record(pid_file)
        content = pid_file.read_text()
        data = json.loads(content)
        assert "pid" in data and "create_time" in data
        assert data["pid"] == os.getpid()

    def test_read_pid_record_round_trips(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        written = pid_manager.write_pid_record(pid_file)
        read = pid_manager.read_pid_record(pid_file)
        assert read is not None
        assert read.pid == written.pid
        assert read.create_time == written.create_time

    def test_read_pid_record_legacy_integer_format(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """Legacy PID files (written by ``write_pid``, which is text-only)
        still parse — create_time is None, caller falls back to
        pid-only liveness check."""
        pid_file.write_text(str(os.getpid()))
        record = pid_manager.read_pid_record(pid_file)
        assert record is not None
        assert record.pid == os.getpid()
        assert record.create_time is None

    def test_read_pid_record_nonexistent_returns_none(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        assert pid_manager.read_pid_record(pid_file) is None

    def test_read_pid_record_empty_file_returns_none(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        pid_file.write_text("")
        assert pid_manager.read_pid_record(pid_file) is None

    def test_read_pid_record_malformed_json_returns_none(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """Corrupted JSON falls through to legacy-int parsing; if that
        also fails, returns None (graceful) rather than raising."""
        pid_file.write_text("{ not json and not int")
        assert pid_manager.read_pid_record(pid_file) is None

    def test_is_running_detects_pid_recycling(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """The core F2 case: a PID file whose recorded create_time
        doesn't match the running process with that PID (= PID was
        recycled by the OS after the original daemon died). Must
        return False.

        Simulates recycling by writing a record for the current
        process's PID but with a create_time shifted 1 hour back —
        as if the pid_file was written by an old daemon that
        crashed, and the OS handed the PID to a new process.
        """

        pid = os.getpid()
        actual_create_time = psutil.Process(pid).create_time()
        # Pretend the daemon that wrote this record started an hour before us.
        fake_create_time = actual_create_time - 3600.0
        pid_file.write_text(json.dumps({"pid": pid, "create_time": fake_create_time}))

        assert pid_manager.is_running(pid_file) is False

    def test_is_running_accepts_matching_create_time(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """The happy path: record was written by this process, create_time
        matches, so ``is_running`` returns True."""
        pid_manager.write_pid_record(pid_file)
        assert pid_manager.is_running(pid_file) is True

    def test_is_running_legacy_format_falls_back_to_pid_exists(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """Legacy text-only PID files (no create_time) can't be validated
        for recycling — fall back to the pre-F2 behaviour of
        ``psutil.pid_exists``. Documented degradation, not silent."""
        pid_file.write_text(str(os.getpid()))
        assert pid_manager.is_running(pid_file) is True

    def test_is_running_dead_pid_returns_false_with_record(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """Dead PIDs are caught by ``psutil.pid_exists`` before the
        create_time check — still False."""

        pid_file.write_text(json.dumps({"pid": -1, "create_time": 0.0}))
        assert pid_manager.is_running(pid_file) is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX file-mode semantics")
class TestWritePidRecordPermissions:
    """F2 permission preservation (codex P2 PRRT_kwDOR_Rkws59bl3J).

    The atomic-write path uses ``tempfile.NamedTemporaryFile`` whose
    default mode is 0o600. Left as-is, ``os.replace`` would silently
    narrow the PID file's mode from the pre-F2 ``open(path, "w")``
    default (typically 0o644) to owner-only, breaking cross-account
    ``daemon status``/``stop`` readers. These tests lock in the
    pre-F2 mode semantics.
    """

    def test_new_pid_file_is_0o644(self, pid_manager: PidFileManager, pid_file: Path) -> None:
        """First-time writes get the standard 0o644 daemon PID file
        mode — independent of the caller's umask.

        Hardcoding avoids probing umask (which would require mutating
        process-global state and race with concurrent file creation in
        other threads; codex P2 PRRT_kwDOR_Rkws59bvEf). 0o644 matches
        the ``/var/run/*.pid`` convention and the default-umask-022
        result of the pre-F2 ``open(path, "w")``.
        """
        pid_manager.write_pid_record(pid_file)
        mode = pid_file.stat().st_mode & 0o7777
        assert mode == 0o644, f"expected standard daemon PID file mode 0o644, got {mode:#o}"

    def test_write_does_not_depend_on_or_mutate_process_umask(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """Regression guard for codex P2 PRRT_kwDOR_Rkws59bvEf.

        Setting an unusual caller-side umask (0o077) and then writing
        must:
          1. Leave umask unchanged afterwards (no probe-and-restore).
          2. Still produce a 0o644 PID file (mode independent of
             umask).

        If the code regresses to ``os.umask(0)`` + restore, the
        process-global umask would flip during the call — we can't
        observe the window from a single-threaded test, but paired
        with the mode-is-0o644 assertion we prove the code cannot be
        reading umask at all.
        """
        prior_umask = os.umask(0o077)
        try:
            pid_manager.write_pid_record(pid_file)
            current = os.umask(0o077)
            assert current == 0o077, (
                f"write_pid_record must not leak a umask change; got {current:#o}"
            )
            mode = pid_file.stat().st_mode & 0o7777
            assert mode == 0o644, (
                f"PID file mode must be 0o644 regardless of caller's "
                f"umask (0o077 set here); got {mode:#o}"
            )
        finally:
            os.umask(prior_umask)

    def test_rotation_preserves_existing_mode(
        self, pid_manager: PidFileManager, pid_file: Path
    ) -> None:
        """Overwriting an existing PID file keeps its current mode.

        Without this, restarting a daemon could silently drop custom
        modes an operator set on the PID file (e.g. group-readable
        `0o640` for a multi-user deployment).
        """
        # Seed with an existing file and an explicit non-default mode.
        pid_file.write_text("0")
        os.chmod(pid_file, 0o640)

        pid_manager.write_pid_record(pid_file)

        mode = pid_file.stat().st_mode & 0o7777
        assert mode == 0o640, (
            f"pre-existing mode 0o640 must be preserved on rotation; got {mode:#o}"
        )
