"""PID file management for the daemon.

Provides the PidFileManager class for creating, reading, and removing
PID files, plus checking whether a recorded process is still alive.

F2 (hardening roadmap #159) — PID-reuse race protection:
Pre-F2 PID files stored only the integer PID. When a daemon crashed
and the OS later recycled its PID for an unrelated process, the next
call to ``is_running`` reported the daemon as alive — because the PID
still existed, just for a different process. F2 introduces a JSON
record format that also captures the process start-time
(``psutil.Process(pid).create_time()``); ``is_running`` rejects records
whose PID is alive but whose start-time doesn't match.

Backward compat: legacy text-only PID files still read and work (with
a documented degradation — no recycling detection for those).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

# Tolerance for matching create_time. psutil typically returns
# microsecond precision on Linux and millisecond precision on macOS;
# 0.5s is generous but avoids false mismatches from rounding.
_CREATE_TIME_TOLERANCE_S = 0.5


@dataclass(frozen=True)
class PidRecord:
    """A PID record loaded from disk.

    Attributes:
        pid: The recorded process ID.
        create_time: ``psutil.Process.create_time()`` of the process that
            wrote the record, in seconds since epoch. ``None`` for
            legacy text-only PID files — callers fall back to
            ``psutil.pid_exists`` only, losing recycling protection.
    """

    pid: int
    create_time: float | None


class PidFileManager:
    """Manages PID files for daemon process tracking.

    PID files record the process ID of a running daemon so that
    other processes (or the user) can detect whether the daemon is
    active and send it signals.

    Example:
        >>> manager = PidFileManager()
        >>> pid_path = Path("/tmp/daemon.pid")
        >>> manager.write_pid(pid_path)
        >>> assert manager.is_running(pid_path)
        >>> manager.remove_pid(pid_path)
    """

    def write_pid(self, pid_file: Path, pid: int | None = None) -> None:
        """Write the current (or specified) process ID to a PID file.

        Creates parent directories if they do not exist. Overwrites
        any existing PID file at the same path.

        Args:
            pid_file: Path where the PID file will be written.
            pid: Process ID to write. Defaults to the current process.

        Raises:
            OSError: If the file cannot be written.
        """
        pid_file = Path(pid_file)
        pid = pid if pid is not None else os.getpid()

        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(pid))
        logger.debug("Wrote PID %d to %s", pid, pid_file)

    def read_pid(self, pid_file: Path) -> int | None:
        """Read the process ID from a PID file.

        Args:
            pid_file: Path to the PID file.

        Returns:
            The process ID as an integer, or None if the file does not
            exist or contains invalid content.
        """
        pid_file = Path(pid_file)

        if not pid_file.exists():
            return None

        try:
            content = pid_file.read_text().strip()
            if not content:
                return None
            return int(content)
        except (ValueError, OSError) as exc:
            logger.warning("Failed to read PID from %s: %s", pid_file, exc, exc_info=True)
            return None

    def remove_pid(self, pid_file: Path) -> bool:
        """Remove a PID file.

        Args:
            pid_file: Path to the PID file to remove.

        Returns:
            True if the file was removed, False if it did not exist.
        """
        pid_file = Path(pid_file)

        if not pid_file.exists():
            logger.debug("PID file does not exist: %s", pid_file)
            return False

        try:
            pid_file.unlink()
            logger.debug("Removed PID file: %s", pid_file)
            return True
        except OSError as exc:
            logger.warning("Failed to remove PID file %s: %s", pid_file, exc, exc_info=True)
            return False

    def write_pid_record(self, pid_file: Path, pid: int | None = None) -> PidRecord:
        """Write a PID + process-start-time record to *pid_file*.

        F2 (hardening roadmap #159): unlike :meth:`write_pid`, this
        records the process's start time so :meth:`is_running` can
        detect PID recycling. Use this method for new daemons; the
        legacy :meth:`write_pid` remains for backward compat.

        Args:
            pid_file: Path where the record will be written.
            pid: Process ID to write. Defaults to the current process.

        Returns:
            The :class:`PidRecord` that was written.

        Raises:
            OSError: If the file cannot be written.
            psutil.NoSuchProcess: If *pid* doesn't exist at write time.
        """
        pid = pid if pid is not None else os.getpid()
        create_time = psutil.Process(pid).create_time()
        pid_file = Path(pid_file)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        # F3 (atomic write): temp file + os.replace so a mid-write crash
        # can never leave a partial JSON file. A corrupt record would
        # otherwise defeat the F2 recycling check — ``read_pid_record``
        # falls through to legacy int parse, returns None, and a fresh
        # ``daemon start`` would spawn a second daemon alongside the
        # original.
        payload = json.dumps({"pid": pid, "create_time": create_time})
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=pid_file.parent,
                prefix=f".{pid_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, pid_file)
            tmp_path = None  # successfully moved — no cleanup needed
        finally:
            if tmp_path is not None and tmp_path.exists():
                # os.replace failed — best-effort cleanup of the stray
                # temp file so we don't accumulate .tmp debris.
                try:
                    tmp_path.unlink()
                except OSError:
                    logger.debug("Failed to clean up stray temp PID file %s", tmp_path)
        logger.debug(
            "Wrote PID record pid=%d create_time=%f to %s",
            pid,
            create_time,
            pid_file,
        )
        return PidRecord(pid=pid, create_time=create_time)

    def read_pid_record(self, pid_file: Path) -> PidRecord | None:
        """Read a PID record from *pid_file*.

        Handles both F2 JSON format and the legacy text-only integer
        format. Returns ``None`` for missing/empty/corrupt files.

        Args:
            pid_file: Path to the PID file.

        Returns:
            A :class:`PidRecord`, or ``None`` if the file is missing,
            empty, or unparsable as either JSON or integer.
        """
        pid_file = Path(pid_file)
        if not pid_file.exists():
            return None

        try:
            content = pid_file.read_text().strip()
        except OSError as exc:
            logger.warning("Failed to read PID file %s: %s", pid_file, exc, exc_info=True)
            return None

        if not content:
            return None

        # Try F2 JSON format first.
        try:
            data = json.loads(content)
            if isinstance(data, dict) and "pid" in data:
                pid = int(data["pid"])
                ct_raw = data.get("create_time")
                create_time = float(ct_raw) if ct_raw is not None else None
                return PidRecord(pid=pid, create_time=create_time)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            # Distinguish "valid JSON with malformed pid field" from
            # "invalid JSON, try legacy int" so an operator debugging a
            # corrupt PID file can tell the two failure modes apart.
            logger.debug(
                "read_pid_record: JSON parse failed for %s (%s); "
                "falling through to legacy int parse",
                pid_file,
                exc,
            )

        # Fall through to legacy integer format.
        try:
            return PidRecord(pid=int(content), create_time=None)
        except ValueError:
            logger.warning("PID file %s contains unparsable content", pid_file)
            return None

    def is_running(self, pid_file: Path) -> bool:
        """Check whether the process recorded in a PID file is alive.

        F2 (hardening roadmap #159): for records that include
        ``create_time`` (JSON format, from :meth:`write_pid_record`),
        the start-time of the live process is compared against the
        recorded value — a mismatch means the PID was recycled by the
        OS after the original daemon died, and we return False.

        For legacy text-only PID files (no ``create_time``), falls back
        to the pre-F2 behaviour: ``psutil.pid_exists(pid)``. This is a
        documented degradation — PID recycling is not detected for
        legacy files. Run ``write_pid_record`` on daemon startup to
        opt into recycling protection.

        ``psutil.pid_exists()`` uses OS-native handle checking
        (OpenProcess on Windows, /proc on Linux) and is safe on all
        platforms. ``os.kill(pid, 0)`` is not used because on Windows
        signal 0 maps to CTRL_C_EVENT.

        Args:
            pid_file: Path to the PID file.

        Returns:
            True if the PID file exists, the recorded process is
            alive, and (for F2 records) the recorded start-time
            matches the running process's start-time. False otherwise.
        """
        record = self.read_pid_record(pid_file)
        if record is None:
            return False

        if not psutil.pid_exists(record.pid):
            return False

        # Legacy format — no recycling detection.
        if record.create_time is None:
            return True

        # F2 recycling check: verify the PID points at the same process
        # that wrote the record.
        try:
            actual_create_time = psutil.Process(record.pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Process disappeared between pid_exists and create_time,
            # or we can't inspect it — treat as not running.
            return False

        if abs(actual_create_time - record.create_time) > _CREATE_TIME_TOLERANCE_S:
            logger.warning(
                "PID %d was recycled: recorded create_time=%f but running process "
                "create_time=%f; treating as not running (F2 protection).",
                record.pid,
                record.create_time,
                actual_create_time,
            )
            return False

        return True
