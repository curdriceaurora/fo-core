"""Generic path migration helpers.

The application no longer auto-detects or falls back to legacy app identity
paths.  The copy/backup helper remains available for explicit, caller-owned
data moves, but runtime path resolution hard-cuts to the new ``fo`` app dirs.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def detect_legacy_paths(home: Path, config_home: Path, data_home: Path) -> list[Path]:
    """Return no legacy app paths for the hard-cut ``fo`` identity."""
    _ = (home, config_home, data_home)
    return []


class PathMigrator:
    """Handles migration from legacy paths to canonical XDG structure."""

    def __init__(self, legacy_path: Path, canonical_path: Path):
        """Initialize migrator.

        Args:
            legacy_path: Source legacy path to migrate from
            canonical_path: Target canonical path to migrate to
        """
        self.legacy_path = legacy_path
        self.canonical_path = canonical_path
        self.backup_path: Path | None = None
        self.migration_log: dict[str, Any] = {}

    def backup_legacy_path(self) -> Path:
        """Create backup of legacy path before migration.

        Returns:
            Path to backup directory
        """
        # Use microseconds to ensure uniqueness for rapid successive migrations
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        backup = self.legacy_path.parent / f"{self.legacy_path.name}.backup.{timestamp}"
        shutil.copytree(self.legacy_path, backup)
        self.backup_path = backup
        return backup

    def migrate(self) -> None:
        """Migrate files from legacy to canonical location."""
        if not self.legacy_path.exists():
            return

        # Create backup first
        self.backup_legacy_path()

        # Ensure canonical path exists
        self.canonical_path.mkdir(parents=True, exist_ok=True)

        # Copy all files from legacy to canonical
        for item in self.legacy_path.rglob("*"):
            if item.is_file():
                relative = item.relative_to(self.legacy_path)
                target = self.canonical_path / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)

    def create_migration_log(self) -> dict[str, Any]:
        """Create migration log entry for audit trail.

        Returns:
            Dictionary with migration details
        """
        return {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "from": str(self.legacy_path),
            "to": str(self.canonical_path),
            "status": "pending",
            "backup": str(self.backup_path) if self.backup_path else None,
        }

    def finalize_migration(self) -> None:
        """Finalize migration after verification, persisting audit trail."""
        log = self.create_migration_log()
        log["status"] = "completed"
        self.migration_log = log

        # Persist log to audit trail for data integrity and compliance
        self.canonical_path.mkdir(parents=True, exist_ok=True)
        audit_file = self.canonical_path / ".migration-audit.json"
        audit_file.write_text(json.dumps(log, indent=2, default=str))

        # Could optionally remove legacy path here
        # For now, leave it with backup created for safety


def resolve_legacy_path(new_dir: Path, legacy_dir: Path) -> Path:
    """Return *new_dir* without falling back to legacy app locations.

    Args:
        new_dir: The new canonical directory (from ``get_data_dir()`` /
            ``get_config_dir()``).
        legacy_dir: Ignored legacy directory retained for API compatibility
            with explicit migration helpers.

    Returns:
        *new_dir*.
    """
    _ = legacy_dir
    return new_dir
