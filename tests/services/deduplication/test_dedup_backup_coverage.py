"""Coverage tests for BackupManager — targets uncovered branches."""

from __future__ import annotations

import io
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from services.deduplication.backup import BackupManager, _backup_safe_unlink

pytestmark = pytest.mark.unit


@pytest.fixture()
def bm(tmp_path):
    return BackupManager(base_dir=tmp_path)


@pytest.fixture()
def sample_file(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("sample content")
    return f


# ---------------------------------------------------------------------------
# create_backup
# ---------------------------------------------------------------------------


class TestCreateBackup:
    def test_backup_creates_file(self, bm, sample_file):
        backup_path = bm.create_backup(sample_file)
        assert backup_path.exists()
        assert backup_path.read_text() == "sample content"

    def test_backup_nonexistent_raises(self, bm):
        with pytest.raises(FileNotFoundError):
            bm.create_backup(Path("no/such/file.txt"))

    def test_backup_directory_raises(self, bm, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        with pytest.raises(ValueError, match="not a file"):
            bm.create_backup(d)

    def test_backup_updates_manifest(self, bm, sample_file):
        backup_path = bm.create_backup(sample_file)
        info = bm.get_backup_info(backup_path)
        assert info is not None
        assert info["original_path"] == str(sample_file.resolve())


# ---------------------------------------------------------------------------
# restore_backup
# ---------------------------------------------------------------------------


class TestRestoreBackup:
    def test_restore_to_original(self, bm, sample_file, tmp_path):
        backup_path = bm.create_backup(sample_file)
        sample_file.unlink()
        restored = bm.restore_backup(backup_path)
        assert restored.exists()
        assert restored.read_text() == "sample content"

    def test_restore_to_custom_target(self, bm, sample_file, tmp_path):
        backup_path = bm.create_backup(sample_file)
        target = tmp_path / "restored" / "custom.txt"
        restored = bm.restore_backup(backup_path, target_path=target)
        assert restored == target.resolve()
        assert restored.read_text() == "sample content"

    def test_restore_nonexistent_backup(self, bm):
        with pytest.raises(FileNotFoundError):
            bm.restore_backup(Path("no/such/backup.txt"))

    def test_restore_not_in_manifest(self, bm, tmp_path):
        f = tmp_path / "rogue.txt"
        f.write_text("rogue")
        with pytest.raises(ValueError, match="not found in manifest"):
            bm.restore_backup(f)


# ---------------------------------------------------------------------------
# cleanup_old_backups
# ---------------------------------------------------------------------------


class TestCleanupOldBackups:
    def test_negative_age_raises(self, bm):
        with pytest.raises(ValueError, match="non-negative"):
            bm.cleanup_old_backups(max_age_days=-1)

    def test_cleanup_removes_old(self, bm, sample_file):
        backup_path = bm.create_backup(sample_file)
        # Manually age the backup in the manifest
        manifest = bm._load_manifest()
        key = str(backup_path.resolve())
        old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
        manifest[key]["backup_time"] = old_time
        bm._save_manifest(manifest)

        removed = bm.cleanup_old_backups(max_age_days=30)
        assert len(removed) == 1
        assert not backup_path.exists()

    def test_cleanup_keeps_recent(self, bm, sample_file):
        bm.create_backup(sample_file)
        removed = bm.cleanup_old_backups(max_age_days=30)
        assert len(removed) == 0

    def test_cleanup_missing_file(self, bm, sample_file):
        backup_path = bm.create_backup(sample_file)
        # Age and remove the file manually
        manifest = bm._load_manifest()
        key = str(backup_path.resolve())
        old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
        manifest[key]["backup_time"] = old_time
        bm._save_manifest(manifest)
        backup_path.unlink()  # Remove file before cleanup

        removed = bm.cleanup_old_backups(max_age_days=30)
        assert len(removed) == 0  # File was already gone


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------


class TestListBackups:
    def test_list_empty(self, bm):
        backups = bm.list_backups()
        assert backups == []

    def test_list_with_backups(self, bm, sample_file):
        bm.create_backup(sample_file)
        backups = bm.list_backups()
        assert len(backups) == 1
        assert backups[0]["exists"] is True

    def test_list_with_missing_backup(self, bm, sample_file):
        backup_path = bm.create_backup(sample_file)
        backup_path.unlink()
        backups = bm.list_backups()
        assert len(backups) == 1
        assert backups[0]["exists"] is False


# ---------------------------------------------------------------------------
# get_statistics
# ---------------------------------------------------------------------------


class TestGetStatistics:
    def test_stats_empty(self, bm):
        stats = bm.get_statistics()
        assert stats["total_backups"] == 0
        assert stats["total_size_bytes"] == 0

    def test_stats_with_data(self, bm, sample_file):
        bm.create_backup(sample_file)
        stats = bm.get_statistics()
        assert stats["total_backups"] == 1
        assert stats["existing_backups"] == 1
        assert stats["total_size_bytes"] > 0

    def test_stats_with_missing(self, bm, sample_file):
        backup_path = bm.create_backup(sample_file)
        backup_path.unlink()
        stats = bm.get_statistics()
        assert stats["missing_backups"] == 1


# ---------------------------------------------------------------------------
# verify_backups
# ---------------------------------------------------------------------------


class TestVerifyBackups:
    def test_verify_all_ok(self, bm, sample_file):
        bm.create_backup(sample_file)
        issues = bm.verify_backups()
        assert issues == []

    def test_verify_missing_file(self, bm, sample_file):
        backup_path = bm.create_backup(sample_file)
        backup_path.unlink()
        issues = bm.verify_backups()
        assert len(issues) == 1
        assert "Missing" in issues[0]

    def test_verify_size_mismatch(self, bm, sample_file):
        backup_path = bm.create_backup(sample_file)
        # Modify backup to change size
        backup_path.write_text("different content that is longer")
        issues = bm.verify_backups()
        assert len(issues) == 1
        assert "Size mismatch" in issues[0]


# ---------------------------------------------------------------------------
# _load_manifest / _save_manifest edge cases
# ---------------------------------------------------------------------------


class TestManifest:
    def test_load_corrupted_manifest(self, bm):
        bm.manifest_path.write_text("{bad json", encoding="utf-8")
        result = bm._load_manifest()
        assert result == {}

    def test_load_missing_manifest(self, bm):
        bm.manifest_path.unlink(missing_ok=True)
        result = bm._load_manifest()
        assert result == {}


# ---------------------------------------------------------------------------
# _backup_safe_unlink — new diff lines (PR #335)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestBackupSafeUnlinkNewPaths:
    """Cover the new code paths introduced in PR #335 TOCTOU fix."""

    def test_inode_swap_returns_false(self, tmp_path: Path) -> None:
        """Lines 85-96: inode mismatch between fstat and lstat returns False."""

        target = tmp_path / "swap_target.dat"
        target.write_text("content", encoding="utf-8")

        real_st = target.stat()
        # Build a fake fstat with a different inode so (dev, ino, size) != lstat
        swapped_st = os.stat_result(
            (
                real_st.st_mode,
                real_st.st_ino + 9999,  # different inode
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

        with patch("os.fstat", return_value=swapped_st):
            result = _backup_safe_unlink(target, logging.getLogger("test"))

        assert result is False
        assert target.exists()  # file was NOT removed

    def test_inode_swap_logs_security_event(self, tmp_path: Path) -> None:
        """Lines 85-96: inode mismatch emits a security_event WARNING log."""
        target = tmp_path / "swap_log_target.dat"
        target.write_text("content", encoding="utf-8")

        real_st = target.stat()
        swapped_st = os.stat_result(
            (
                real_st.st_mode,
                real_st.st_ino + 9999,
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

        log = logging.getLogger("test_swap_log")
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.WARNING)
        log.addHandler(handler)
        log.setLevel(logging.WARNING)
        try:
            with patch("os.fstat", return_value=swapped_st):
                _backup_safe_unlink(target, log)
            output = buf.getvalue()
        finally:
            log.removeHandler(handler)

        assert "security_event" in output

    def test_oserror_from_safe_dir_returns_false(self, tmp_path: Path) -> None:
        """Line 100: OSError from SafeDir is caught; returns False without raising."""

        target = tmp_path / "oserr_target.dat"
        target.write_text("content", encoding="utf-8")

        with patch(
            "services.deduplication.backup.SafeDir.open_root",
            side_effect=OSError("simulated open failure"),
        ):
            result = _backup_safe_unlink(target, logging.getLogger("test"))

        assert result is False


# ---------------------------------------------------------------------------
# cleanup_old_backups — exception handler lines 264-265 (PR #335)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupOldBackupsExceptionHandler:
    """Lines 264-265: cleanup_old_backups catches OSError/ValueError per-file."""

    def _age_backup(self, bm: BackupManager, backup_path: Path) -> None:
        """Helper: stamp a manifest entry as 60 days old."""
        manifest = bm._load_manifest()
        key = str(backup_path.resolve())
        old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
        manifest[key]["backup_time"] = old_time
        bm._save_manifest(manifest)

    def test_oserror_in_unlink_is_skipped(self, bm: BackupManager, sample_file: Path) -> None:
        """Lines 264-265: OSError from _backup_safe_unlink is caught per-file.

        cleanup_old_backups must continue processing remaining entries
        instead of aborting the whole pass.
        """
        backup_path = bm.create_backup(sample_file)
        self._age_backup(bm, backup_path)

        with patch(
            "services.deduplication.backup._backup_safe_unlink",
            side_effect=OSError("disk full"),
        ):
            removed = bm.cleanup_old_backups(max_age_days=30)

        assert removed == []
        # manifest entry should still be purged even though unlink failed
        assert bm._load_manifest() == {}

    def test_valueerror_in_unlink_is_skipped(self, bm: BackupManager, sample_file: Path) -> None:
        """Lines 264-265: ValueError from _backup_safe_unlink is caught per-file.

        A SafeDir ValueError (e.g. backslash in filename) must not abort
        the entire cleanup pass.
        """
        backup_path = bm.create_backup(sample_file)
        self._age_backup(bm, backup_path)

        with patch(
            "services.deduplication.backup._backup_safe_unlink",
            side_effect=ValueError("illegal filename character"),
        ):
            removed = bm.cleanup_old_backups(max_age_days=30)

        assert removed == []
        assert bm._load_manifest() == {}
