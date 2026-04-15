"""Smoke canary for the [archive] optional extra (py7zr, rarfile).

Uses a 7z archive — NOT zip, which is core and would not exercise py7zr.
rarfile can read RAR files but cannot create them, so RAR validation is
import-only; the 7z path exercises the full read pipeline.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.smoke
def test_archive_reads_7z_file(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    from file_organizer.utils.readers.archives import read_7z_file

    # Create a minimal 7z archive containing one text file
    archive_path = tmp_path / "test.7z"
    content_file = tmp_path / "hello.txt"
    content_file.write_text("hello from 7z archive")

    with py7zr.SevenZipFile(archive_path, mode="w") as archive:
        archive.write(content_file, arcname="hello.txt")

    result = read_7z_file(archive_path)

    # read_7z_file returns archive metadata + file listing (not raw file content)
    assert result is not None
    assert isinstance(result, str)
    assert "hello.txt" in result  # archived filename appears in the listing


@pytest.mark.smoke
def test_rarfile_importable() -> None:
    """rarfile can only read RAR files, not create them.
    Validate it imports cleanly — creation requires external tooling."""
    pytest.importorskip("rarfile")
    import rarfile  # noqa: F401 — import validation only
