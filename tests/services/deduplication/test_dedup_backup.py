"""Tests for BackupManager class.

Tests backup creation, restoration, cleanup, manifest management,
verification, and statistics computation.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from services.deduplication.backup import BackupManager, _backup_safe_unlink

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backup_dir(tmp_path):
    """Provide a fresh temp directory for BackupManager."""
    return tmp_path


@pytest.fixture
def manager(backup_dir):
    """Create a BackupManager using a temporary directory."""
    return BackupManager(base_dir=backup_dir)


@pytest.fixture
def sample_file(tmp_path):
    """Create a sample file to backup."""
    p = tmp_path / "data" / "sample.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("sample content", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackupManagerInit:
    """Tests for BackupManager initialization."""

    def test_creates_backup_dir(self, backup_dir):
        mgr = BackupManager(base_dir=backup_dir)
        assert mgr.backup_dir.exists()
        assert mgr.backup_dir.is_dir()

    def test_creates_manifest(self, backup_dir):
        mgr = BackupManager(base_dir=backup_dir)
        assert mgr.manifest_path.exists()

    def test_manifest_is_valid_json(self, backup_dir):
        mgr = BackupManager(base_dir=backup_dir)
        data = json.loads(mgr.manifest_path.read_text())
        assert data == {}

    def test_default_base_dir(self, tmp_path):
        """When base_dir is None, uses cwd."""
        with patch("services.deduplication.backup.Path.cwd") as mock_cwd:
            mock_cwd.return_value = tmp_path
            mgr = BackupManager(base_dir=None)
            mock_cwd.assert_called_once()
            assert mgr.backup_dir == tmp_path / BackupManager.BACKUP_DIR_NAME

    def test_existing_manifest_preserved(self, backup_dir):
        """Existing manifest is not overwritten."""
        mgr1 = BackupManager(base_dir=backup_dir)
        # Create a fake backup entry
        manifest = {"test_key": {"original_path": "/test"}}
        mgr1._save_manifest(manifest)

        # New manager should load existing manifest
        mgr2 = BackupManager(base_dir=backup_dir)
        loaded = mgr2._load_manifest()
        assert "test_key" in loaded


# ---------------------------------------------------------------------------
# create_backup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateBackup:
    """Tests for create_backup."""

    def test_creates_backup_file(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        assert backup_path.exists()
        assert backup_path.read_text() == "sample content"

    def test_backup_in_backup_dir(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        assert str(manager.backup_dir) in str(backup_path)

    def test_manifest_updated(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        manifest = manager._load_manifest()
        assert str(backup_path) in manifest

    def test_manifest_entry_fields(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        manifest = manager._load_manifest()
        entry = manifest[str(backup_path)]
        assert "original_path" in entry
        assert "backup_path" in entry
        assert "backup_time" in entry
        assert "file_size" in entry
        assert "original_mtime" in entry

    def test_file_not_found(self, manager):
        with pytest.raises(FileNotFoundError, match="Source file not found"):
            manager.create_backup(Path("/nonexistent/file.txt"))

    def test_not_a_file(self, manager, tmp_path):
        with pytest.raises(ValueError, match="not a file"):
            manager.create_backup(tmp_path)

    def test_multiple_backups(self, manager, sample_file):
        b1 = manager.create_backup(sample_file)
        b2 = manager.create_backup(sample_file)
        assert b1 != b2
        assert b1.exists()
        assert b2.exists()


# ---------------------------------------------------------------------------
# restore_backup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRestoreBackup:
    """Tests for restore_backup."""

    def test_restore_to_original(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        # Delete original
        sample_file.unlink()
        assert not sample_file.exists()

        # Restore
        restored = manager.restore_backup(backup_path)
        assert restored.exists()
        assert restored.read_text() == "sample content"

    def test_restore_to_custom_path(self, manager, sample_file, tmp_path):
        backup_path = manager.create_backup(sample_file)
        target = tmp_path / "restored" / "custom.txt"

        restored = manager.restore_backup(backup_path, target_path=target)
        assert restored == target
        assert restored.exists()
        assert restored.read_text() == "sample content"

    def test_backup_not_found(self, manager):
        with pytest.raises(FileNotFoundError, match="Backup file not found"):
            manager.restore_backup(Path("/nonexistent/backup.txt"))

    def test_backup_not_in_manifest(self, manager, tmp_path):
        fake_backup = tmp_path / "fake_backup.txt"
        fake_backup.write_text("fake")
        with pytest.raises(ValueError, match="not found in manifest"):
            manager.restore_backup(fake_backup)


# ---------------------------------------------------------------------------
# cleanup_old_backups
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupOldBackups:
    """Tests for cleanup_old_backups."""

    def test_removes_old_backups(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)

        # Modify manifest to make backup old
        manifest = manager._load_manifest()
        old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
        key = str(backup_path)
        manifest[key]["backup_time"] = old_time
        manager._save_manifest(manifest)

        removed = manager.cleanup_old_backups(max_age_days=30)
        assert len(removed) == 1
        assert not backup_path.exists()

    def test_keeps_recent_backups(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        removed = manager.cleanup_old_backups(max_age_days=30)
        assert len(removed) == 0
        assert backup_path.exists()

    def test_negative_age_raises(self, manager):
        with pytest.raises(ValueError, match="non-negative"):
            manager.cleanup_old_backups(max_age_days=-1)

    def test_manifest_updated_after_cleanup(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)

        manifest = manager._load_manifest()
        old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
        manifest[str(backup_path)]["backup_time"] = old_time
        manager._save_manifest(manifest)

        manager.cleanup_old_backups(max_age_days=30)
        manifest = manager._load_manifest()
        assert str(backup_path) not in manifest


# ---------------------------------------------------------------------------
# get_backup_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBackupInfo:
    """Tests for get_backup_info."""

    def test_existing_backup(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        info = manager.get_backup_info(backup_path)
        assert info is not None
        assert "original_path" in info

    def test_nonexistent_backup(self, manager):
        info = manager.get_backup_info(Path("/nonexistent/backup.txt"))
        assert info is None


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListBackups:
    """Tests for list_backups."""

    def test_empty(self, manager):
        backups = manager.list_backups()
        assert backups == []

    def test_with_backups(self, manager, sample_file):
        manager.create_backup(sample_file)
        manager.create_backup(sample_file)
        backups = manager.list_backups()
        assert len(backups) == 2

    def test_sorted_by_time(self, manager, sample_file):
        manager.create_backup(sample_file)
        manager.create_backup(sample_file)
        backups = manager.list_backups()
        # Newest first
        assert backups[0]["backup_time"] >= backups[1]["backup_time"]

    def test_exists_field(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        backups = manager.list_backups()
        assert backups[0]["exists"] is True

        # Delete backup file
        backup_path.unlink()
        backups = manager.list_backups()
        assert backups[0]["exists"] is False


# ---------------------------------------------------------------------------
# get_statistics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStatistics:
    """Tests for get_statistics."""

    def test_empty_stats(self, manager):
        stats = manager.get_statistics()
        assert stats["total_backups"] == 0
        assert stats["existing_backups"] == 0
        assert stats["total_size_bytes"] == 0

    def test_with_backups(self, manager, sample_file):
        manager.create_backup(sample_file)
        stats = manager.get_statistics()
        assert stats["total_backups"] == 1
        assert stats["existing_backups"] == 1
        assert stats["total_size_bytes"] > 0
        assert stats["total_size_mb"] >= 0
        assert "backup_directory" in stats

    def test_missing_backup_counted(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        backup_path.unlink()

        stats = manager.get_statistics()
        assert stats["total_backups"] == 1
        assert stats["existing_backups"] == 0
        assert stats["missing_backups"] == 1


# ---------------------------------------------------------------------------
# verify_backups
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyBackups:
    """Tests for verify_backups."""

    def test_all_valid(self, manager, sample_file):
        manager.create_backup(sample_file)
        issues = manager.verify_backups()
        assert issues == []

    def test_missing_file(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        backup_path.unlink()

        issues = manager.verify_backups()
        assert len(issues) == 1
        assert "Missing" in issues[0]

    def test_size_mismatch(self, manager, sample_file):
        backup_path = manager.create_backup(sample_file)
        # Modify the backup file size
        backup_path.write_text("different content that changes size", encoding="utf-8")

        issues = manager.verify_backups()
        assert len(issues) == 1
        assert "Size mismatch" in issues[0]


# ---------------------------------------------------------------------------
# Manifest operations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestManifestOperations:
    """Tests for _load_manifest and _save_manifest."""

    def test_load_empty_manifest(self, manager):
        manager.manifest_path.unlink()
        manifest = manager._load_manifest()
        assert manifest == {}

    def test_load_corrupted_manifest(self, manager):
        manager.manifest_path.write_text("not json", encoding="utf-8")
        manifest = manager._load_manifest()
        assert manifest == {}

    def test_save_and_load_roundtrip(self, manager):
        data = {"key": {"value": "test"}}
        manager._save_manifest(data)
        loaded = manager._load_manifest()
        assert loaded == data

    def test_atomic_save(self, manager):
        """Save uses atomic write (temp file + rename)."""
        data = {"key": {"value": "test"}}
        manager._save_manifest(data)
        # Verify data is written correctly
        loaded = json.loads(manager.manifest_path.read_text(encoding="utf-8"))
        assert loaded == data


# ---------------------------------------------------------------------------
# Fix 2.4 — cleanup_old_backups survives ValueError from SafeDir
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupOldBackupsValueError:
    """cleanup_old_backups must not abort the whole pass on ValueError (fix 2.4).

    SafeDir raises ValueError for filenames with forbidden characters
    (e.g. backslash, which is legal on POSIX but rejected by SafeDir's
    name validator).  The cleanup loop must skip that entry, emit a
    warning, remove it from the manifest, and continue with the
    remaining entries.
    """

    def _add_old_manifest_entry(self, manager: BackupManager, key: str, backup_path: Path) -> None:
        """Insert a manifest entry with a 60-day-old timestamp."""
        manifest = manager._load_manifest()
        old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
        manifest[key] = {
            "original_path": str(backup_path),
            "backup_path": key,
            "backup_time": old_time,
            "file_size": 0,
            "original_mtime": old_time,
        }
        manager._save_manifest(manifest)

    @pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
    def test_valueerror_on_unlink_does_not_abort_cleanup(
        self, manager: BackupManager, sample_file: Path
    ) -> None:
        """A ValueError from _backup_safe_unlink is caught; cleanup continues."""
        # Create a legitimate backup that will be cleaned up
        backup_path = manager.create_backup(sample_file)
        manifest = manager._load_manifest()
        old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
        manifest[str(backup_path)]["backup_time"] = old_time
        manager._save_manifest(manifest)

        # Inject a manifest entry whose key has a backslash — SafeDir
        # raises ValueError for it, but cleanup must not propagate it.
        bad_key = str(manager.backup_dir / "bad\\name.bak")
        bad_path = Path(bad_key)
        self._add_old_manifest_entry(manager, bad_key, bad_path)

        removed = manager.cleanup_old_backups(max_age_days=30)

        # The legitimate backup was removed
        assert backup_path in removed
        assert not backup_path.exists()

        # The legitimate backup entry is gone (file was deleted).
        # The bad-name entry is RETAINED — _backup_safe_unlink returned False
        # for it (SafeDir ValueError), so the file was not removed and the
        # manifest entry must be kept for retry on the next pass (issue #350 C1/C2).
        final_manifest = manager._load_manifest()
        assert str(backup_path) not in final_manifest
        assert bad_key in final_manifest, (
            "manifest entry for a file that was NOT deleted must be preserved "
            "so the next cleanup pass can retry (issue #350 C1/C2)"
        )

    @pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
    def test_valueerror_from_backup_exists_logged_as_warning(
        self, manager: BackupManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A ValueError raised by backup_path.exists() is caught and logged as WARNING.

        _backup_safe_unlink already swallows ValueError from SafeDir internally
        (returning False with a debug log).  This test covers the outer
        except clause in cleanup_old_backups, which fires when backup_path.exists()
        itself raises (e.g. a filesystem or Path implementation error).
        """
        import logging

        bad_key = str(manager.backup_dir / "bad\\name.bak")
        bad_path = Path(bad_key)
        self._add_old_manifest_entry(manager, bad_key, bad_path)

        # Patch Path.exists to raise ValueError for the bad key, simulating an
        # OS or Path error that propagates before we even get to _backup_safe_unlink.
        original_exists = Path.exists

        def patched_exists(self: Path) -> bool:  # type: ignore[override]
            if str(self) == bad_key:
                raise ValueError("simulated path error")
            return original_exists(self)

        with patch.object(Path, "exists", patched_exists):
            with caplog.at_level(logging.WARNING, logger="services.deduplication.backup"):
                manager.cleanup_old_backups(max_age_days=30)

        assert any("skipping unlink" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Fix 2.5 — _backup_safe_unlink inode-swap TOCTOU protection
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestBackupSafeUnlinkInodeSwap:
    """_backup_safe_unlink must detect inode swaps between open and unlink (fix 2.5)."""

    def test_normal_file_is_removed(self, tmp_path: Path) -> None:
        """Happy path: a plain regular file is unlinked and True is returned."""
        target = tmp_path / "backup.dat"
        target.write_text("content", encoding="utf-8")

        import logging

        result = _backup_safe_unlink(target, logging.getLogger("test"))

        assert result is True
        assert not target.exists()

    def test_symlink_is_rejected(self, tmp_path: Path) -> None:
        """A symlink masquerading as a backup file is rejected (returns False)."""
        real = tmp_path / "real.dat"
        real.write_text("real", encoding="utf-8")
        link = tmp_path / "link.dat"
        link.symlink_to(real)

        import logging

        result = _backup_safe_unlink(link, logging.getLogger("test"))

        assert result is False
        assert link.exists()  # symlink itself still present
        assert real.exists()  # real file untouched

    def test_inode_swap_detected_and_rejected(self, tmp_path: Path) -> None:
        """When fstat and lstat disagree on inode, unlink is aborted."""
        import logging

        target = tmp_path / "backup.dat"
        target.write_text("original", encoding="utf-8")

        # Build a fake fstat result pointing at a different inode so that the
        # (dev, ino, size) triple will not match the real lstat.
        real_st = target.stat()
        swapped_st = os.stat_result(
            (
                real_st.st_mode,
                real_st.st_ino + 999,  # different inode
                real_st.st_dev,
                real_st.st_nlink,
                real_st.st_uid,
                real_st.st_gid,
                real_st.st_size,
                real_st.st_atime,
                real_st.st_mtime,
                real_st.st_ctime,
            )
        )

        log = logging.getLogger("test")
        with patch("os.fstat", return_value=swapped_st):
            result = _backup_safe_unlink(target, log)

        assert result is False
        # File should still exist because unlink was aborted
        assert target.exists()

    def test_inode_swap_logged_as_security_event(self, tmp_path: Path) -> None:
        """An inode mismatch triggers a security_event WARNING log entry."""
        import logging

        target = tmp_path / "backup.dat"
        target.write_text("original", encoding="utf-8")

        real_st = target.stat()
        swapped_st = os.stat_result(
            (
                real_st.st_mode,
                real_st.st_ino + 999,
                real_st.st_dev,
                real_st.st_nlink,
                real_st.st_uid,
                real_st.st_gid,
                real_st.st_size,
                real_st.st_atime,
                real_st.st_mtime,
                real_st.st_ctime,
            )
        )

        log = logging.getLogger("services.deduplication.backup")
        with patch("os.fstat", return_value=swapped_st):
            import io

            handler = logging.StreamHandler(io.StringIO())
            handler.setLevel(logging.WARNING)
            log.addHandler(handler)
            try:
                _backup_safe_unlink(target, log)
                output = handler.stream.getvalue()
            finally:
                log.removeHandler(handler)

        assert "security_event" in output

    def test_valueerror_returns_false(self, tmp_path: Path) -> None:
        """A ValueError from SafeDir name validation returns False (does not raise)."""
        import logging

        # SafeDir raises ValueError for names with backslash on POSIX.
        bad_parent = tmp_path
        bad_name = bad_parent / "bad\\name.dat"

        result = _backup_safe_unlink(bad_name, logging.getLogger("test"))

        assert result is False

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        """A non-existent file returns False without raising."""
        import logging

        target = tmp_path / "ghost.dat"  # does not exist

        result = _backup_safe_unlink(target, logging.getLogger("test"))

        assert result is False


# ---------------------------------------------------------------------------
# Issue #350 — R4, C3, T5, C1/C2 refinements to _backup_safe_unlink
# ---------------------------------------------------------------------------


@pytest.mark.ci
@pytest.mark.unit
@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestBackupSafeUnlinkIssue350:
    """Targeted tests for the issue #350 refinements."""

    def test_fifo_is_rejected_before_open(self, tmp_path: Path) -> None:
        """R4: a FIFO must be rejected by lstat type-check before open_child is called.

        open_child with O_RDONLY on a FIFO blocks until a writer connects; the
        pre-open lstat check must fire first so open_child is never reached.
        """
        import logging
        import stat
        from unittest.mock import patch

        from utils.safedir import SafeDir

        fifo = tmp_path / "queue.fifo"
        os.mkfifo(fifo)
        assert stat.S_ISFIFO(fifo.stat().st_mode)

        with patch.object(SafeDir, "open_child", autospec=True) as mock_open_child:
            result = _backup_safe_unlink(fifo, logging.getLogger("test"))

        assert result is False
        assert fifo.exists(), "FIFO must not be unlinked — type check should reject it"
        mock_open_child.assert_not_called()

    def test_size_change_on_same_inode_does_not_trigger_swap_detection(
        self, tmp_path: Path
    ) -> None:
        """C3: (st_dev, st_ino) comparison — st_size change on same inode must not abort.

        The old code compared (st_dev, st_ino, st_size).  A concurrent writer on the
        same inode can change st_size between fstat and lstat, causing a false positive
        that orphans a real backup.  The fix compares only (st_dev, st_ino).
        """
        import logging

        target = tmp_path / "backup.dat"
        target.write_bytes(b"x" * 100)
        real_st = target.stat()

        # Build an fstat result with same (dev, ino) but different size — simulates
        # a concurrent write between fstat and lstat.
        same_ino_different_size = os.stat_result(
            (
                real_st.st_mode,
                real_st.st_ino,  # same inode
                real_st.st_dev,
                real_st.st_nlink,
                real_st.st_uid,
                real_st.st_gid,
                real_st.st_size + 50,  # different size — concurrent writer
                real_st.st_atime,
                real_st.st_mtime,
                real_st.st_ctime,
            )
        )

        with patch("os.fstat", return_value=same_ino_different_size):
            result = _backup_safe_unlink(target, logging.getLogger("test"))

        # Must succeed — size difference on same inode is not a swap
        assert result is True
        assert not target.exists()

    def test_valueerror_logged_at_warning_level(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """C1/C2: ValueError from SafeDir name validation must be logged at WARNING.

        The old code logged at DEBUG, making it invisible in production.  A filename
        that SafeDir rejects (e.g. containing backslash) is a notable event that
        operators should see.
        """
        import logging

        bad_path = tmp_path / "bad\\name.bak"

        with caplog.at_level(logging.WARNING, logger="services.deduplication.backup"):
            result = _backup_safe_unlink(
                bad_path, logging.getLogger("services.deduplication.backup")
            )

        assert result is False
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("backup_name_rejected" in msg for msg in warning_messages), (
            f"expected backup_name_rejected WARNING, got: {warning_messages}"
        )

    def test_write_only_file_is_unlinked_without_fd_pin(self, tmp_path: Path) -> None:
        """T5: a write-only file raises PermissionError from open_child; unlink still succeeds.

        The fix catches PermissionError from open_child and proceeds with
        unlink-without-fd-pin — SafeDir's dir_fd still prevents directory swaps.

        Uses a deterministic mock rather than chmod(0o200) so the test passes
        even under privileged users (e.g. root on CI runners).
        """
        import logging
        from unittest.mock import patch

        from utils.safedir import SafeDir

        target = tmp_path / "locked.bak"
        target.write_bytes(b"data")

        with patch.object(
            SafeDir,
            "open_child",
            autospec=True,
            side_effect=PermissionError("simulated write-only file"),
        ):
            result = _backup_safe_unlink(target, logging.getLogger("test"))

        assert result is True
        assert not target.exists()


# ---------------------------------------------------------------------------
# _is_safedir_safe_name helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
class TestIsSafedirSafeName:
    """The helper mirrors SafeDir._validate_name's component check."""

    def test_reserved_names_are_unsafe(self) -> None:
        from services.deduplication.backup import _is_safedir_safe_name

        assert _is_safedir_safe_name("") is False
        assert _is_safedir_safe_name(".") is False
        assert _is_safedir_safe_name("..") is False

    def test_path_separator_chars_are_unsafe(self) -> None:
        from services.deduplication.backup import _is_safedir_safe_name

        assert _is_safedir_safe_name("a/b.bak") is False
        assert _is_safedir_safe_name("a\\b.bak") is False
        assert _is_safedir_safe_name("with\x00null") is False

    def test_plain_names_are_safe(self) -> None:
        from services.deduplication.backup import _is_safedir_safe_name

        assert _is_safedir_safe_name("backup.bak") is True
        assert _is_safedir_safe_name("file-with-dashes_and_underscores.txt") is True


# ---------------------------------------------------------------------------
# cleanup_old_backups: manifest entries pointing outside backup_dir
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
@pytest.mark.skipif(sys.platform == "win32", reason="path resolution is POSIX-specific")
class TestCleanupOldBackupsOutsideBackupDir:
    """A corrupted manifest pointing outside backup_dir must be skipped."""

    def test_entry_outside_backup_dir_is_skipped_and_preserved(
        self, manager: BackupManager, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Manifest entry whose resolved path falls outside backup_dir is skipped.

        The entry is preserved in the manifest so an operator can inspect
        the corruption — silent deletion would mask a tampering event.
        """
        import logging

        # Point the manifest at a path well outside backup_dir.
        outside_key = str(tmp_path / "elsewhere.bak")
        manifest = manager._load_manifest()
        old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
        manifest[outside_key] = {
            "original_path": str(tmp_path / "original"),
            "backup_path": outside_key,
            "backup_time": old_time,
            "file_size": 0,
            "original_mtime": old_time,
        }
        manager._save_manifest(manifest)

        with caplog.at_level(logging.WARNING, logger="services.deduplication.backup"):
            removed = manager.cleanup_old_backups(max_age_days=30)

        assert removed == []
        # The corrupt entry stays so it can be inspected.
        assert outside_key in manager._load_manifest()
        assert any("outside the backup directory" in r.message for r in caplog.records), (
            f"expected outside-dir WARNING, got: {[r.message for r in caplog.records]}"
        )
