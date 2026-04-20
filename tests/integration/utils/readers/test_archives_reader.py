"""Integration tests for utils/readers/archives.py.

Covers uncovered paths from the baseline (59%):
- read_7z_file: real 7z archive read (py7zr happy path), max_files truncation,
  compression stats, error on corrupt file, ImportError when py7zr unavailable
- read_rar_file: ImportError when rarfile unavailable (real rar requires unrar tool,
  so we test what we can without it), plus the mock path
- read_tar_file: xz compression type detection
- read_zip_file: max_files=0 edge case and compression ratio with empty archive
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import py7zr
import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip(tmp_path: Path, name: str, files: dict[str, bytes]) -> Path:
    """Create a ZIP archive at tmp_path/name."""
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files.items():
            zf.writestr(filename, content)
    return zip_path


def _make_tar(tmp_path: Path, name: str, files: dict[str, bytes], mode: str = "w") -> Path:
    """Create a TAR archive at tmp_path/name."""
    tar_path = tmp_path / name
    with tarfile.open(tar_path, mode) as tf:
        for filename, content in files.items():
            info = tarfile.TarInfo(name=filename)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return tar_path


def _make_7z(tmp_path: Path, name: str, files: dict[str, bytes]) -> Path:
    """Create a 7Z archive at tmp_path/name using py7zr.

    Writes real files via archive.write() so that py7zr records compressed
    size for every entry (writestr() produces solid archives where only the
    first entry carries a non-None compressed size).
    """
    archive_path = tmp_path / name
    # Stage files under a sub-directory so names don't collide with archive_path
    staging = tmp_path / ("_stage_" + name)
    staging.mkdir(exist_ok=True)
    staged: list[tuple[Path, str]] = []
    for filename, content in files.items():
        dest = staging / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        staged.append((dest, filename))
    with py7zr.SevenZipFile(archive_path, "w") as archive:
        for dest, arcname in staged:
            archive.write(dest, arcname)
    return archive_path


# ---------------------------------------------------------------------------
# read_7z_file — happy path (requires py7zr)
# ---------------------------------------------------------------------------


class TestRead7zFileHappyPath:
    def test_7z_returns_string(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(tmp_path, "test.7z", {"doc.txt": b"hello 7z"})
        result = read_7z_file(ap)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_7z_contains_archive_name_in_header(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(tmp_path, "myarchive.7z", {"note.txt": b"content"})
        result = read_7z_file(ap)
        assert "myarchive.7z" in result

    def test_7z_header_line(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(tmp_path, "archive.7z", {"f.txt": b"x"})
        result = read_7z_file(ap)
        assert "7Z Archive:" in result

    def test_7z_total_files_count(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(
            tmp_path,
            "multi.7z",
            {"a.txt": b"aaa", "b.txt": b"bbb", "c.txt": b"ccc"},
        )
        result = read_7z_file(ap)
        assert "Total files: 3" in result

    def test_7z_lists_file_names(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(tmp_path, "list.7z", {"readme.md": b"# README"})
        result = read_7z_file(ap)
        assert "readme.md" in result

    def test_7z_max_files_truncates(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        files = {f"{i}.txt": f"file {i}".encode() for i in range(10)}
        ap = _make_7z(tmp_path, "many.7z", files)
        result = read_7z_file(ap, max_files=3)
        assert "and 7 more files" in result

    def test_7z_no_truncation_when_max_files_large(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(tmp_path, "few.7z", {"x.txt": b"x", "y.txt": b"y"})
        result = read_7z_file(ap, max_files=100)
        assert "more files" not in result

    def test_7z_compression_ratio_present(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(tmp_path, "comp.7z", {"big.txt": b"A" * 10000})
        result = read_7z_file(ap)
        assert "Compression ratio:" in result

    def test_7z_encrypted_field_present(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(tmp_path, "enc.7z", {"data.bin": b"data"})
        result = read_7z_file(ap)
        assert "Encrypted:" in result

    def test_7z_accepts_string_path(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(tmp_path, "str.7z", {"f.txt": b"hello"})
        result = read_7z_file(str(ap))
        assert "7Z Archive:" in result
        assert "f.txt" in result

    def test_7z_max_files_zero(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_7z_file

        ap = _make_7z(tmp_path, "zero.7z", {"a.txt": b"a"})
        result = read_7z_file(ap, max_files=0)
        assert isinstance(result, str)
        # With max_files=0 the truncation message appears
        assert "and 1 more files" in result

    def test_7z_invalid_file_raises_file_read_error(self, tmp_path: Path) -> None:
        from utils.readers._base import FileReadError
        from utils.readers.archives import read_7z_file

        broken = tmp_path / "broken.7z"
        broken.write_bytes(b"this is not a 7z file")
        with pytest.raises(FileReadError):
            read_7z_file(broken)

    def test_7z_missing_file_raises_file_read_error(self, tmp_path: Path) -> None:
        from utils.readers._base import FileReadError
        from utils.readers.archives import read_7z_file

        with pytest.raises(FileReadError):
            read_7z_file(tmp_path / "nonexistent.7z")


class TestRead7zImportError:
    """Test the ImportError guard when py7zr is not available."""

    def test_raises_import_error_when_py7zr_unavailable(self, tmp_path: Path) -> None:
        from utils.readers import archives
        from utils.readers.archives import read_7z_file

        with patch.object(archives, "PY7ZR_AVAILABLE", False):
            with pytest.raises(ImportError, match="py7zr"):
                read_7z_file(tmp_path / "dummy.7z")


class TestRead7zMockedPaths:
    def test_mocked_success_without_py7zr_dependency(self, tmp_path: Path) -> None:
        from utils.readers import archives
        from utils.readers.archives import read_7z_file

        path = tmp_path / "mocked.7z"
        path.write_bytes(b"7z placeholder")

        class FakeEntry:
            def __init__(
                self, filename: str, compressed: int | None, uncompressed: int | None
            ) -> None:
                self.filename = filename
                self.compressed = compressed
                self.uncompressed = uncompressed

        class FakeArchive:
            password_protected = True

            def __enter__(self) -> FakeArchive:
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def list(self) -> list[FakeEntry]:
                return [
                    FakeEntry("a.txt", 100, 200),
                    FakeEntry("b.txt", None, 300),
                    FakeEntry("c.txt", 50, None),
                ]

        sentinel = object()
        original_py7zr = getattr(archives, "py7zr", sentinel)
        original_available = archives.PY7ZR_AVAILABLE
        seven_zip_ctor = MagicMock(return_value=FakeArchive())
        try:
            archives.PY7ZR_AVAILABLE = True
            archives.py7zr = SimpleNamespace(SevenZipFile=seven_zip_ctor)
            result = read_7z_file(path, max_files=2)
        finally:
            archives.PY7ZR_AVAILABLE = original_available
            if original_py7zr is sentinel:
                del archives.py7zr
            else:
                archives.py7zr = original_py7zr

        seven_zip_ctor.assert_called_once_with(path, "r")
        assert "7Z Archive: mocked.7z" in result
        assert "Encrypted: Yes" in result
        assert "a.txt" in result
        assert "b.txt" in result
        assert "and 1 more files" in result

    def test_mocked_error_without_py7zr_dependency_raises_file_read_error(
        self, tmp_path: Path
    ) -> None:
        from utils.readers import archives
        from utils.readers._base import FileReadError
        from utils.readers.archives import read_7z_file

        path = tmp_path / "broken.7z"
        path.write_bytes(b"7z placeholder")

        sentinel = object()
        original_py7zr = getattr(archives, "py7zr", sentinel)
        original_available = archives.PY7ZR_AVAILABLE
        seven_zip_ctor = MagicMock(side_effect=OSError("bad 7z"))
        try:
            archives.PY7ZR_AVAILABLE = True
            archives.py7zr = SimpleNamespace(SevenZipFile=seven_zip_ctor)
            with pytest.raises(FileReadError):
                read_7z_file(path)
        finally:
            archives.PY7ZR_AVAILABLE = original_available
            if original_py7zr is sentinel:
                del archives.py7zr
            else:
                archives.py7zr = original_py7zr

        seven_zip_ctor.assert_called_once_with(path, "r")


# ---------------------------------------------------------------------------
# read_rar_file — ImportError path
# ---------------------------------------------------------------------------


class TestReadRarFileImportError:
    """Test the ImportError guard when rarfile is not available."""

    def test_raises_import_error_when_rarfile_unavailable(self, tmp_path: Path) -> None:
        from utils.readers import archives
        from utils.readers.archives import read_rar_file

        with patch.object(archives, "RARFILE_AVAILABLE", False):
            with pytest.raises(ImportError, match="rarfile"):
                read_rar_file(tmp_path / "dummy.rar")

    def test_import_error_message_mentions_unrar(self, tmp_path: Path) -> None:
        from utils.readers import archives
        from utils.readers.archives import read_rar_file

        with patch.object(archives, "RARFILE_AVAILABLE", False):
            with pytest.raises(ImportError, match="unrar"):
                read_rar_file(tmp_path / "dummy.rar")


# ---------------------------------------------------------------------------
# read_tar_file — xz compression detection (previously uncovered)
# ---------------------------------------------------------------------------


class TestReadTarFileXzCompression:
    def test_tar_xz_extension_detected(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_tar_file

        tp = _make_tar(tmp_path, "archive.tar.xz", {"f.txt": b"hello xz"}, mode="w:xz")
        result = read_tar_file(tp)
        assert "XZ" in result

    def test_txz_extension_detected(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_tar_file

        # .xz suffix without .tar prefix is also matched by the source code
        # (it checks _name.endswith(".tar.xz") or _name.endswith(".xz"))
        tp = _make_tar(tmp_path, "archive.xz", {"f.txt": b"hello txz"}, mode="w:xz")
        result = read_tar_file(tp)
        assert "XZ" in result

    def test_tbz2_extension_detected(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_tar_file

        # .tbz2 is also handled by the source (.tar.bz2 branch checks tbz2 too)
        tp = _make_tar(tmp_path, "archive.tbz2", {"f.txt": b"hello tbz2"}, mode="w:bz2")
        result = read_tar_file(tp)
        assert "BZ2" in result

    def test_tar_gz_extension_detected(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_tar_file

        tp = _make_tar(tmp_path, "archive.tar.gz", {"f.txt": b"hello gz"}, mode="w:gz")
        result = read_tar_file(tp)
        assert "GZ" in result

    def test_tgz_extension_detected(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_tar_file

        tp = _make_tar(tmp_path, "archive.tgz", {"f.txt": b"hello tgz"}, mode="w:gz")
        result = read_tar_file(tp)
        assert "GZ" in result

    def test_tar_max_files_truncation(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_tar_file

        files = {f"{i}.txt": f"file {i}".encode() for i in range(10)}
        tp = _make_tar(tmp_path, "many.tar", files)
        result = read_tar_file(tp, max_files=3)
        assert "and 7 more files" in result

    def test_tar_invalid_file_raises_file_read_error(self, tmp_path: Path) -> None:
        from utils.readers._base import FileReadError
        from utils.readers.archives import read_tar_file

        broken = tmp_path / "broken.tar"
        broken.write_bytes(b"this is not a tar file")
        with pytest.raises(FileReadError):
            read_tar_file(broken)


# ---------------------------------------------------------------------------
# read_zip_file — comprehensive coverage
# ---------------------------------------------------------------------------


class TestReadZipFile:
    def test_zip_returns_string(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        zp = _make_zip(tmp_path, "test.zip", {"doc.txt": b"hello zip"})
        result = read_zip_file(zp)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_zip_header_line(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        zp = _make_zip(tmp_path, "myarchive.zip", {"note.txt": b"content"})
        result = read_zip_file(zp)
        assert "ZIP Archive:" in result
        assert "myarchive.zip" in result

    def test_zip_total_files_count(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        zp = _make_zip(
            tmp_path,
            "multi.zip",
            {"a.txt": b"aaa", "b.txt": b"bbb", "c.txt": b"ccc"},
        )
        result = read_zip_file(zp)
        assert "Total files: 3" in result

    def test_zip_lists_file_names(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        zp = _make_zip(tmp_path, "list.zip", {"readme.md": b"# README"})
        result = read_zip_file(zp)
        assert "readme.md" in result

    def test_zip_max_files_truncation(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        files = {f"{i}.txt": f"file {i}".encode() for i in range(10)}
        zp = _make_zip(tmp_path, "many.zip", files)
        result = read_zip_file(zp, max_files=3)
        assert "and 7 more files" in result

    def test_zip_no_truncation_when_max_files_large(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        zp = _make_zip(tmp_path, "few.zip", {"x.txt": b"x", "y.txt": b"y"})
        result = read_zip_file(zp, max_files=100)
        assert "more files" not in result

    def test_zip_compression_ratio_present(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        zp = _make_zip(tmp_path, "comp.zip", {"big.txt": b"A" * 10000})
        result = read_zip_file(zp)
        assert "Compression ratio:" in result

    def test_zip_encrypted_field_present(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        zp = _make_zip(tmp_path, "enc.zip", {"data.bin": b"data"})
        result = read_zip_file(zp)
        assert "Encrypted:" in result

    def test_zip_accepts_string_path(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        zp = _make_zip(tmp_path, "str.zip", {"f.txt": b"hello"})
        result = read_zip_file(str(zp))
        assert "ZIP Archive:" in result
        assert "f.txt" in result

    def test_zip_max_files_zero(self, tmp_path: Path) -> None:
        from utils.readers.archives import read_zip_file

        zp = _make_zip(tmp_path, "zero.zip", {"a.txt": b"a"})
        result = read_zip_file(zp, max_files=0)
        assert isinstance(result, str)
        assert "and 1 more files" in result

    def test_zip_invalid_file_raises_file_read_error(self, tmp_path: Path) -> None:
        from utils.readers._base import FileReadError
        from utils.readers.archives import read_zip_file

        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"this is not a zip file")
        with pytest.raises(FileReadError):
            read_zip_file(broken)

    def test_zip_missing_file_raises_file_read_error(self, tmp_path: Path) -> None:
        from utils.readers._base import FileReadError
        from utils.readers.archives import read_zip_file

        with pytest.raises(FileReadError):
            read_zip_file(tmp_path / "nonexistent.zip")


# ---------------------------------------------------------------------------
# read_rar_file — mock-based happy path
# ---------------------------------------------------------------------------


class TestReadRarFileMocked:
    """Test read_rar_file happy-path via mocking when rarfile is available."""

    def test_rar_returns_string_via_mock(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from utils.readers import archives
        from utils.readers.archives import read_rar_file

        mock_info = MagicMock()
        mock_info.filename = "doc.txt"
        mock_info.file_size = 1024
        mock_info.compress_size = 512

        mock_rf = MagicMock()
        mock_rf.__enter__ = MagicMock(return_value=mock_rf)
        mock_rf.__exit__ = MagicMock(return_value=False)
        mock_rf.infolist.return_value = [mock_info]
        mock_rf.needs_password.return_value = False

        dummy = tmp_path / "test.rar"
        dummy.write_bytes(b"dummy")

        with (
            patch.object(archives, "RARFILE_AVAILABLE", True),
            patch.object(archives, "rarfile", create=True) as mock_rarfile_mod,
        ):
            mock_rarfile_mod.RarFile.return_value = mock_rf
            result = read_rar_file(dummy)

        assert isinstance(result, str)
        assert "RAR Archive:" in result
        assert "doc.txt" in result

    def test_rar_truncation(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from utils.readers import archives
        from utils.readers.archives import read_rar_file

        entries = [
            MagicMock(filename=f"{i}.txt", file_size=100, compress_size=50) for i in range(10)
        ]

        mock_rf = MagicMock()
        mock_rf.__enter__ = MagicMock(return_value=mock_rf)
        mock_rf.__exit__ = MagicMock(return_value=False)
        mock_rf.infolist.return_value = entries
        mock_rf.needs_password.return_value = False

        dummy = tmp_path / "many.rar"
        dummy.write_bytes(b"dummy")

        with (
            patch.object(archives, "RARFILE_AVAILABLE", True),
            patch.object(archives, "rarfile", create=True) as mock_rarfile_mod,
        ):
            mock_rarfile_mod.RarFile.return_value = mock_rf
            result = read_rar_file(dummy, max_files=3)

        assert "and 7 more files" in result

    def test_rar_invalid_file_raises_file_read_error(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from utils.readers import archives
        from utils.readers._base import FileReadError
        from utils.readers.archives import read_rar_file

        mock_rf = MagicMock()
        mock_rf.__enter__ = MagicMock(side_effect=Exception("bad rar"))
        mock_rf.__exit__ = MagicMock(return_value=False)

        dummy = tmp_path / "broken.rar"
        dummy.write_bytes(b"not a rar")

        with (
            patch.object(archives, "RARFILE_AVAILABLE", True),
            patch.object(archives, "rarfile", create=True) as mock_rarfile_mod,
        ):
            mock_rarfile_mod.RarFile.return_value = mock_rf
            with pytest.raises(FileReadError):
                read_rar_file(dummy)
