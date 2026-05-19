"""Regression tests for the SafeDir-routed read in
``services.search.hybrid_retriever.read_text_safe`` (PR3h).

The corpus text extractor reads up to ``CORPUS_TEXT_LIMIT`` bytes via
``SafeDir.open_root`` + ``open_for_reader`` so a symlink swapped between
corpus enumeration and the content read is refused rather than
dereferenced (closes the LLM-exfiltration vector documented in #264).

This file exercises every branch the SafeDir migration introduced:
- happy path on a real on-disk file (no mocks)
- ``SymlinkRejected`` returning ``""`` and logging a warning
- ``NotImplementedError`` falling back to the legacy path-based open
- ``os.fdopen`` raising ``OSError`` after the bare fd has been
  obtained (cleanup must not leak the fd)
- legacy path-based ``open`` raising ``OSError`` (returns ``""``)
- defensive limit clamp: ``limit <= 0`` returns ``""`` without reading
- defensive limit clamp: ``limit > CORPUS_TEXT_LIMIT`` is capped
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ``hybrid_retriever`` transitively imports BM25 / sklearn / numpy via
# its ``VectorIndex`` and ``BM25Index`` siblings. The ``read_text_safe``
# helper itself doesn't depend on them, but module import still pulls
# them in, so we skip the file cleanly when the optional ``search`` /
# ``dedup-text`` extras aren't installed (e.g. CI's lint env).
pytest.importorskip("rank_bm25")
pytest.importorskip("sklearn")

from services.search.hybrid_retriever import (
    CORPUS_TEXT_LIMIT,
    read_text_safe,
)
from utils.safedir import SymlinkRejected

pytestmark = [
    pytest.mark.ci,
    pytest.mark.unit,
    pytest.mark.integration,
]


class TestReadTextSafeHappyPath:
    """Sanity checks: real on-disk files must still work end-to-end."""

    def test_reads_text_file(self, tmp_path: Path) -> None:
        target = tmp_path / "hello.txt"
        target.write_text("hello world")
        assert read_text_safe(target) == "hello world"

    def test_returns_empty_for_binary_file(self, tmp_path: Path) -> None:
        target = tmp_path / "bin"
        target.write_bytes(b"\x00\x01\x02\x03prefix")
        assert read_text_safe(target) == ""

    def test_caps_at_limit(self, tmp_path: Path) -> None:
        target = tmp_path / "big.txt"
        target.write_text("a" * (CORPUS_TEXT_LIMIT + 1000))
        assert len(read_text_safe(target, limit=128)) == 128


class TestReadTextSafeLimitClamp:
    """Defensive limit handling: non-positive or oversized values must
    not let the underlying ``read(n)`` call read the entire file (S3
    corpus-cap rule)."""

    def test_zero_limit_returns_empty(self, tmp_path: Path) -> None:
        target = tmp_path / "x.txt"
        target.write_text("content")
        assert read_text_safe(target, limit=0) == ""

    def test_negative_limit_returns_empty(self, tmp_path: Path) -> None:
        target = tmp_path / "x.txt"
        target.write_text("content")
        assert read_text_safe(target, limit=-1) == ""

    def test_oversized_limit_clamped_to_corpus_text_limit(self, tmp_path: Path) -> None:
        target = tmp_path / "big.txt"
        target.write_text("a" * (CORPUS_TEXT_LIMIT * 2))
        # An oversized limit must NOT let us read more than CORPUS_TEXT_LIMIT.
        out = read_text_safe(target, limit=CORPUS_TEXT_LIMIT * 2)
        assert len(out) <= CORPUS_TEXT_LIMIT


@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestReadTextSafeSafedirBranches:
    """Each SafeDir error branch is mocked so we can prove the function
    handles it the way the security contract demands (return ``""``,
    log, no fd leak)."""

    def test_symlink_rejected_returns_empty_string(self, tmp_path: Path) -> None:
        target = tmp_path / "real.txt"
        target.write_text("secret")
        with patch(
            "services.search.hybrid_retriever.SafeDir.open_root",
            side_effect=SymlinkRejected("name"),
        ):
            assert read_text_safe(target) == ""

    def test_not_implemented_falls_back_to_legacy_open(self, tmp_path: Path) -> None:
        """When SafeDir is unavailable (e.g. macOS without dir_fd), the
        function must read the file via the legacy ``path.open(...)``
        fallback rather than returning empty.
        """
        target = tmp_path / "fallback.txt"
        target.write_text("fallback content")
        with patch(
            "services.search.hybrid_retriever.SafeDir.open_root",
            side_effect=NotImplementedError("no dir_fd"),
        ):
            assert read_text_safe(target) == "fallback content"

    def test_fdopen_failure_closes_bare_fd(self, tmp_path: Path) -> None:
        """If ``os.fdopen`` raises after ``open_for_reader`` already
        returned a raw fd, the function must close that fd explicitly
        (otherwise we leak file descriptors). We verify the cleanup by
        spying on ``os.close``.
        """
        target = tmp_path / "real.txt"
        target.write_text("never read")
        sentinel_fd = 4242

        # Build a fake SafeDir context whose open_for_reader returns
        # our sentinel fd. We don't actually have an OS fd at that
        # number, so we patch os.close to swallow it.
        fake_safe_dir = MagicMock()
        fake_safe_dir.open_for_reader.return_value = sentinel_fd
        fake_cm = MagicMock()
        fake_cm.__enter__.return_value = fake_safe_dir
        fake_cm.__exit__.return_value = False

        with (
            patch(
                "services.search.hybrid_retriever.SafeDir.open_root",
                return_value=fake_cm,
            ),
            patch(
                "services.search.hybrid_retriever.os.fdopen",
                side_effect=OSError("fdopen blew up"),
            ),
            patch("services.search.hybrid_retriever.os.close") as mock_close,
        ):
            assert read_text_safe(target) == ""
            mock_close.assert_called_once_with(sentinel_fd)

    def test_safedir_oserror_returns_empty(self, tmp_path: Path) -> None:
        """Any non-symlink OSError from the SafeDir branch (e.g. file
        not found at open_for_reader) returns ``""`` — same shape as
        the legacy path's ``except OSError: return ""``."""
        target = tmp_path / "x.txt"
        target.write_text("data")
        with patch(
            "services.search.hybrid_retriever.SafeDir.open_root",
            side_effect=OSError("no such file"),
        ):
            assert read_text_safe(target) == ""

    def test_safedir_valueerror_returns_empty(self, tmp_path: Path) -> None:
        """SafeDir's name validation raises ``ValueError`` for
        filenames containing backslash / NUL / path separators. On
        POSIX such filenames are legal in the filesystem, so a corpus
        enumerator can yield them. The helper must return ``""``
        rather than letting the ``ValueError`` abort the caller."""
        target = tmp_path / "ok.txt"
        target.write_text("data")
        with patch(
            "services.search.hybrid_retriever.SafeDir.open_root",
            side_effect=ValueError("name 'a\\b' contains path separator"),
        ):
            assert read_text_safe(target) == ""


@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestReadTextSafeLegacyFallback:
    """When the function falls through to the legacy ``path.open(...)``
    branch (e.g. via ``NotImplementedError``), it must still return
    ``""`` on OSError, matching the original PR1 behavior."""

    def test_legacy_open_oserror_returns_empty(self, tmp_path: Path) -> None:
        target = tmp_path / "x.txt"
        # File never created — Path.open will OSError. We also disable
        # the SafeDir path so we reach the legacy fallback.
        with patch(
            "services.search.hybrid_retriever.SafeDir.open_root",
            side_effect=NotImplementedError(),
        ):
            assert read_text_safe(target) == ""
