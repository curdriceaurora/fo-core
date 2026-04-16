"""Integration tests for utils/readers/_base.py.

Covers uncovered paths from the baseline (38%):
- _check_file_size: file within limit passes, file over limit raises FileTooLargeError,
  missing file returns silently (OSError branch), custom max_bytes parameter,
  exact boundary values, error message content
- FileReadError and FileTooLargeError: exception hierarchy and instantiation
- MAX_FILE_SIZE_BYTES: verify the constant value
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


class TestCheckFileSize:
    """Tests for _check_file_size helper."""

    def test_file_within_limit_passes(self, tmp_path: Path) -> None:
        from utils.readers._base import _check_file_size

        f = tmp_path / "small.txt"
        f.write_bytes(b"x" * 1024)  # 1 KB
        # Should not raise
        _check_file_size(f, max_bytes=2048)

    def test_file_over_limit_raises_file_too_large_error(self, tmp_path: Path) -> None:
        from utils.readers._base import FileTooLargeError, _check_file_size

        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 1024)  # 1 KB
        with pytest.raises(FileTooLargeError):
            _check_file_size(f, max_bytes=512)

    def test_error_message_mentions_file_too_large(self, tmp_path: Path) -> None:
        from utils.readers._base import FileTooLargeError, _check_file_size

        f = tmp_path / "toobig.txt"
        f.write_bytes(b"x" * 1024)
        with pytest.raises(FileTooLargeError, match="File too large to process"):
            _check_file_size(f, max_bytes=512)

    def test_error_message_contains_file_path(self, tmp_path: Path) -> None:
        from utils.readers._base import FileTooLargeError, _check_file_size

        f = tmp_path / "pathcheck.txt"
        f.write_bytes(b"x" * 1024)
        with pytest.raises(FileTooLargeError, match="pathcheck.txt"):
            _check_file_size(f, max_bytes=512)

    def test_missing_file_returns_silently(self, tmp_path: Path) -> None:
        from utils.readers._base import _check_file_size

        missing = tmp_path / "does_not_exist.txt"
        # OSError path: should return without raising
        _check_file_size(missing)

    def test_file_exactly_at_limit_passes(self, tmp_path: Path) -> None:
        from utils.readers._base import _check_file_size

        f = tmp_path / "exact.txt"
        f.write_bytes(b"x" * 512)
        # size == max_bytes: should NOT raise (condition is size > max_bytes)
        _check_file_size(f, max_bytes=512)

    def test_file_one_byte_over_limit_raises(self, tmp_path: Path) -> None:
        from utils.readers._base import FileTooLargeError, _check_file_size

        f = tmp_path / "oneover.txt"
        f.write_bytes(b"x" * 513)
        with pytest.raises(FileTooLargeError):
            _check_file_size(f, max_bytes=512)

    def test_default_max_bytes_is_large(self, tmp_path: Path) -> None:
        from utils.readers._base import MAX_FILE_SIZE_BYTES, _check_file_size

        f = tmp_path / "normal.txt"
        f.write_bytes(b"hello world")
        # Any real file should easily pass the 500 MB default
        _check_file_size(f)
        assert MAX_FILE_SIZE_BYTES == 500 * 1024 * 1024

    def test_empty_file_passes(self, tmp_path: Path) -> None:
        from utils.readers._base import _check_file_size

        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        _check_file_size(f, max_bytes=0)


class TestFileReadError:
    """Tests for FileReadError exception class."""

    def test_file_read_error_is_exception(self) -> None:
        from utils.readers._base import FileReadError

        assert issubclass(FileReadError, Exception)

    def test_file_read_error_can_be_raised_and_caught(self) -> None:
        from utils.readers._base import FileReadError

        with pytest.raises(FileReadError, match="test error"):
            raise FileReadError("test error")

    def test_file_read_error_stores_message(self) -> None:
        from utils.readers._base import FileReadError

        err = FileReadError("cannot read /path/to/file")
        assert "cannot read /path/to/file" in str(err)


class TestFileTooLargeError:
    """Tests for FileTooLargeError exception class."""

    def test_file_too_large_error_is_os_error(self) -> None:
        from utils.readers._base import FileTooLargeError

        assert issubclass(FileTooLargeError, OSError)

    def test_file_too_large_error_can_be_raised_and_caught(self) -> None:
        from utils.readers._base import FileTooLargeError

        with pytest.raises(FileTooLargeError, match="too large"):
            raise FileTooLargeError("file too large: 600 MB")

    def test_file_too_large_error_also_caught_as_os_error(self) -> None:
        from utils.readers._base import FileTooLargeError

        with pytest.raises(OSError):
            raise FileTooLargeError("file too large")


class TestMaxFileSizeConstant:
    """Tests for the MAX_FILE_SIZE_BYTES module constant."""

    def test_max_file_size_bytes_value(self) -> None:
        from utils.readers._base import MAX_FILE_SIZE_BYTES

        assert MAX_FILE_SIZE_BYTES == 500 * 1024 * 1024

    def test_max_file_size_bytes_is_int(self) -> None:
        from utils.readers._base import MAX_FILE_SIZE_BYTES

        assert isinstance(MAX_FILE_SIZE_BYTES, int)
        assert MAX_FILE_SIZE_BYTES == 524288000
