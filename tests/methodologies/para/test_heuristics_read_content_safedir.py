"""Regression tests for ``AIHeuristic._read_content_bytes`` (PR3h).

The PARA content extractor routes file reads through ``SafeDir`` so a
symlink swapped between detection and the content sample is refused.

This file exercises every branch the SafeDir migration introduced:

- happy path on a real on-disk file (no mocks)
- ``SymlinkRejected`` returns ``None``
- ``NotImplementedError`` falls back to legacy ``path.open(...)``
- ``os.fdopen`` failure closes the bare fd
- generic ``OSError`` from SafeDir returns ``None``
- legacy fallback ``OSError`` returns ``None``
- defensive limit clamp: non-positive ``limit`` returns ``None``
  without reading
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from methodologies.para.detection.heuristics import AIHeuristic
from utils.safedir import SymlinkRejected

pytestmark = [
    pytest.mark.ci,
    pytest.mark.unit,
    pytest.mark.integration,
]


class TestReadContentBytesHappyPath:
    def test_reads_up_to_limit(self, tmp_path: Path) -> None:
        target = tmp_path / "x.txt"
        target.write_bytes(b"abcdefghij")
        assert AIHeuristic._read_content_bytes(target, limit=5) == b"abcde"

    def test_reads_whole_file_when_smaller_than_limit(self, tmp_path: Path) -> None:
        target = tmp_path / "x.txt"
        target.write_bytes(b"abc")
        assert AIHeuristic._read_content_bytes(target, limit=100) == b"abc"


class TestReadContentBytesLimitGuard:
    """Non-positive limits (from a misconfigured ``max_content_chars``)
    must not let ``read(limit)`` read the whole file."""

    def test_zero_limit_returns_none(self, tmp_path: Path) -> None:
        target = tmp_path / "x.txt"
        target.write_bytes(b"content")
        assert AIHeuristic._read_content_bytes(target, limit=0) is None

    def test_negative_limit_returns_none(self, tmp_path: Path) -> None:
        target = tmp_path / "x.txt"
        target.write_bytes(b"content")
        assert AIHeuristic._read_content_bytes(target, limit=-1) is None


@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestReadContentBytesSafedirBranches:
    def test_symlink_rejected_returns_none(self, tmp_path: Path) -> None:
        target = tmp_path / "real.txt"
        target.write_bytes(b"secret")
        with patch(
            "methodologies.para.detection.heuristics.SafeDir.open_root",
            side_effect=SymlinkRejected("real.txt"),
        ):
            assert AIHeuristic._read_content_bytes(target, limit=64) is None

    def test_not_implemented_falls_back_to_legacy_open(self, tmp_path: Path) -> None:
        target = tmp_path / "fb.txt"
        payload = b"fallback content"
        target.write_bytes(payload)
        with patch(
            "methodologies.para.detection.heuristics.SafeDir.open_root",
            side_effect=NotImplementedError(),
        ):
            assert AIHeuristic._read_content_bytes(target, limit=100) == payload

    def test_fdopen_failure_closes_bare_fd(self, tmp_path: Path) -> None:
        target = tmp_path / "real.txt"
        target.write_bytes(b"never read")
        sentinel_fd = 4242

        fake_safe_dir = MagicMock()
        fake_safe_dir.open_for_reader.return_value = sentinel_fd
        fake_cm = MagicMock()
        fake_cm.__enter__.return_value = fake_safe_dir
        fake_cm.__exit__.return_value = False

        with (
            patch(
                "methodologies.para.detection.heuristics.SafeDir.open_root",
                return_value=fake_cm,
            ),
            patch(
                "methodologies.para.detection.heuristics.os.fdopen",
                side_effect=OSError("fdopen failed"),
            ),
            patch("methodologies.para.detection.heuristics.os.close") as mock_close,
        ):
            assert AIHeuristic._read_content_bytes(target, limit=64) is None
            mock_close.assert_called_once_with(sentinel_fd)

    def test_safedir_oserror_returns_none(self, tmp_path: Path) -> None:
        target = tmp_path / "x.txt"
        with patch(
            "methodologies.para.detection.heuristics.SafeDir.open_root",
            side_effect=OSError("no such file"),
        ):
            assert AIHeuristic._read_content_bytes(target, limit=64) is None


@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestReadContentBytesLegacyFallback:
    def test_legacy_open_oserror_returns_none(self, tmp_path: Path) -> None:
        target = tmp_path / "ghost.txt"
        with patch(
            "methodologies.para.detection.heuristics.SafeDir.open_root",
            side_effect=NotImplementedError(),
        ):
            assert AIHeuristic._read_content_bytes(target, limit=64) is None
