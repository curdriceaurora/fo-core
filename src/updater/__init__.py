"""Auto-update mechanism for File Organizer.

Checks GitHub Releases for new versions, downloads with SHA256
verification, and performs atomic binary replacement.

The :mod:`~updater.sidecar_updater` sub-module adds
coordinated update support so the launcher and the Python backend
are always kept in sync.
"""

from __future__ import annotations

from updater.checker import ReleaseInfo, UpdateChecker
from updater.installer import UpdateInstaller
from updater.manager import UpdateManager
from updater.sidecar_updater import (
    BackendUpdateStatus,
    CoordinatedUpdateResult,
    check_backend_update,
    coordinated_update,
    verify_sha256,
)

__all__ = [
    "CoordinatedUpdateResult",
    "ReleaseInfo",
    "BackendUpdateStatus",
    "UpdateChecker",
    "UpdateInstaller",
    "UpdateManager",
    "check_backend_update",
    "coordinated_update",
    "verify_sha256",
]
