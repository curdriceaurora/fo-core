"""PID file management for the daemon.

Provides the PidFileManager class for creating, reading, and removing
PID files, plus checking whether a recorded process is still alive.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)


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

    def is_running(self, pid_file: Path) -> bool:
        """Check whether the process recorded in a PID file is alive.

        Reads the PID from the file and uses psutil to test whether
        the process exists. psutil.pid_exists() uses OS-native handle
        checking (OpenProcess on Windows, /proc on Linux) and is safe
        on all platforms. os.kill(pid, 0) is not used because on Windows
        signal 0 maps to CTRL_C_EVENT and can send a real interrupt to
        the current console process group.

        Args:
            pid_file: Path to the PID file.

        Returns:
            True if the PID file exists and the recorded process is
            alive. False otherwise.
        """
        pid = self.read_pid(pid_file)

        if pid is None:
            return False

        return bool(psutil.pid_exists(pid))
