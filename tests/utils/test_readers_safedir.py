"""Tests for the SafeDir-aware reader API.

Covers the additions made in PR3a (#267):

- ``fileobj=`` kwarg on the public reader functions in
  ``utils.readers.documents`` (text, docx, pdf, rtf, spreadsheet,
  presentation). When given, the reader uses the library's file-like
  API instead of opening the path. Path-based callers are unchanged
  (covered by the existing ``tests/utils/test_file_readers.py``).
- ``utils.readers.read_file_via_safedir(safe_dir, name)`` — the
  SafeDir-friendly dispatcher: opens via ``SafeDir.open_for_reader``
  (which refuses symlinks with ``O_NOFOLLOW``) and routes to the
  matching reader via ``fileobj=``.
- ``utils.readers._base._check_fd_size`` — fd-based size check used by
  the fileobj branch.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.readers import (
    MAX_FILE_SIZE_BYTES,
    FileTooLargeError,
    read_docx_file,
    read_file_via_safedir,
    read_pdf_file,
    read_presentation_file,
    read_rtf_file,
    read_spreadsheet_file,
    read_text_file,
)
from utils.readers._base import _check_fd_size
from utils.safedir import SafeDir, SymlinkRejected

pytestmark = [
    pytest.mark.ci,
    pytest.mark.unit,
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only"),
]


# ---------------------------------------------------------------------------
# fileobj= on individual readers
# ---------------------------------------------------------------------------


class TestReadTextFileFileobj:
    def test_reads_from_fileobj(self) -> None:
        data = b"hello\nworld\n"
        assert read_text_file(fileobj=io.BytesIO(data)) == "hello\nworld\n"

    def test_respects_max_chars(self) -> None:
        data = b"x" * 50_000
        out = read_text_file(fileobj=io.BytesIO(data), max_chars=100)
        assert out == "x" * 100

    def test_ignores_invalid_utf8(self) -> None:
        # An invalid UTF-8 byte gets dropped; the surrounding ASCII remains.
        data = b"valid \xfe more"
        out = read_text_file(fileobj=io.BytesIO(data))
        assert "valid" in out
        assert "more" in out

    def test_label_falls_back_when_no_file_path(self) -> None:
        # Doesn't raise; label is just used in logs.
        assert read_text_file(fileobj=io.BytesIO(b"x")) == "x"


class TestReadDocxFileFileobj:
    @patch("utils.readers.documents.docx")
    def test_reads_from_fileobj(self, mock_docx: MagicMock, tmp_path: Path) -> None:
        para = MagicMock()
        para.text = "Hello docx"
        mock_doc = MagicMock()
        mock_doc.paragraphs = [para]
        mock_docx.Document.return_value = mock_doc

        out = read_docx_file(fileobj=io.BytesIO(b"fake docx bytes"))
        assert out == "Hello docx"
        mock_docx.Document.assert_called_once()


class TestReadPdfFileFileobj:
    @patch("utils.readers.documents.fitz")
    def test_reads_from_fileobj_via_stream(self, mock_fitz: MagicMock) -> None:
        mock_page = MagicMock()
        mock_page.get_text.return_value = "page text"
        mock_doc = MagicMock()
        mock_doc.__enter__.return_value = mock_doc
        mock_doc.__exit__.return_value = None
        mock_doc.__len__.return_value = 1
        mock_doc.load_page.return_value = mock_page
        mock_fitz.open.return_value = mock_doc

        out = read_pdf_file(fileobj=io.BytesIO(b"%PDF-fake"))
        assert out == "page text"
        # The fileobj path must use ``stream=`` so fitz never receives a path.
        kwargs = mock_fitz.open.call_args.kwargs
        assert "stream" in kwargs
        assert kwargs.get("filetype") == "pdf"


class TestReadRtfFileFileobj:
    @patch("utils.readers.documents._rtf_to_text", return_value="rtf content")
    def test_reads_from_fileobj(self, _mock_rtf: MagicMock) -> None:
        out = read_rtf_file(fileobj=io.BytesIO(b"{\\rtf1...}"))
        assert "rtf content" in out


class TestReadSpreadsheetFileFileobj:
    def test_csv_from_fileobj(self, tmp_path: Path) -> None:
        data = b"a,b,c\n1,2,3\n4,5,6\n"
        out = read_spreadsheet_file(
            file_path=tmp_path / "data.csv",
            fileobj=io.BytesIO(data),
        )
        assert out == "a,b,c\n1,2,3\n4,5,6"

    @patch("utils.readers.documents.openpyxl")
    def test_xlsx_from_fileobj(self, mock_openpyxl: MagicMock, tmp_path: Path) -> None:
        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = iter([("h1", "h2"), (1, 2)])
        mock_wb = MagicMock()
        mock_wb.active = mock_ws
        mock_openpyxl.load_workbook.return_value = mock_wb

        out = read_spreadsheet_file(
            file_path=tmp_path / "data.xlsx",
            fileobj=io.BytesIO(b"fake xlsx"),
        )
        assert "h1,h2" in out
        assert "1,2" in out

    def test_requires_file_path_for_extension_detection(self) -> None:
        with pytest.raises(ValueError, match="file_path"):
            read_spreadsheet_file(fileobj=io.BytesIO(b"data"))


class TestReadPresentationFileFileobj:
    @patch("utils.readers.documents.Presentation")
    def test_reads_from_fileobj(self, mock_prs_cls: MagicMock) -> None:
        shape = MagicMock()
        shape.text = "Slide text"
        slide = MagicMock()
        slide.shapes = [shape]
        mock_prs = MagicMock()
        mock_prs.slides = [slide]
        mock_prs_cls.return_value = mock_prs

        out = read_presentation_file(fileobj=io.BytesIO(b"fake pptx"))
        assert "Slide text" in out


# ---------------------------------------------------------------------------
# Either-or argument validation
# ---------------------------------------------------------------------------


class TestFileTooLargeErrorPropagation:
    """`FileTooLargeError` from ``_check_fd_size`` must propagate to callers
    of the ``fileobj=`` branch. The dispatcher docstring promises this type;
    if the broad ``except Exception`` parser-error handler wrapped it as
    ``FileReadError``, callers could no longer distinguish oversized files
    from genuine reader failures.

    Patches ``_check_fd_size`` to raise directly — the goal here is to
    verify the reader's exception handling preserves the type, not to
    re-test ``_check_fd_size`` itself (covered above).
    """

    @staticmethod
    def _raise_too_large(*_args: object, **_kwargs: object) -> None:
        raise FileTooLargeError("test: file too large")

    def test_text_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.documents._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_text_file(fileobj=io.BytesIO(b"any"))

    def test_docx_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.documents._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_docx_file(fileobj=io.BytesIO(b"any"))

    def test_pdf_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.documents._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_pdf_file(fileobj=io.BytesIO(b"any"))

    def test_rtf_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.documents._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_rtf_file(fileobj=io.BytesIO(b"any"))

    def test_spreadsheet_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.documents._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_spreadsheet_file(file_path=tmp_path / "x.csv", fileobj=io.BytesIO(b"any"))

    def test_presentation_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.documents._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_presentation_file(fileobj=io.BytesIO(b"any"))


class TestRequiresFilePathOrFileobj:
    """Every reader must reject calls with neither arg supplied."""

    @pytest.mark.parametrize(
        "reader",
        [read_text_file, read_docx_file, read_pdf_file, read_rtf_file, read_presentation_file],
    )
    def test_rejects_both_args_missing(self, reader) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError):
            reader()


# ---------------------------------------------------------------------------
# read_file_via_safedir: dispatcher + symlink rejection
# ---------------------------------------------------------------------------


class TestReadFileViaSafedir:
    def test_reads_real_text_file(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("integration test content")
        with SafeDir.open_root(tmp_path) as sd:
            out = read_file_via_safedir(sd, "notes.txt")
        assert out == "integration test content"

    def test_reads_md_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("# heading\n\nbody")
        with SafeDir.open_root(tmp_path) as sd:
            out = read_file_via_safedir(sd, "doc.md")
        assert "heading" in out

    def test_refuses_symlink(self, tmp_path: Path) -> None:
        """The dispatcher uses ``open_for_reader`` which refuses symlinks."""
        honey = tmp_path / "honey.txt"
        honey.write_text("do_not_exfiltrate")
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "link.txt").symlink_to(honey)
        except OSError:
            pytest.skip("symlink creation not supported")

        with SafeDir.open_root(organize) as sd:
            with pytest.raises(SymlinkRejected):
                read_file_via_safedir(sd, "link.txt")

    def test_unsupported_extension_returns_none(self, tmp_path: Path) -> None:
        """Extensions not yet in ``_SAFEDIR_READERS`` (archives, ebooks, etc.)
        return None — caller can fall back to legacy path-based ``read_file``.
        """
        (tmp_path / "archive.zip").write_bytes(b"PK\x03\x04")
        with SafeDir.open_root(tmp_path) as sd:
            assert read_file_via_safedir(sd, "archive.zip") is None

    def test_rejects_bad_name(self, tmp_path: Path) -> None:
        """Component-name validation rides on SafeDir.open_for_reader."""
        with SafeDir.open_root(tmp_path) as sd:
            with pytest.raises(ValueError):
                read_file_via_safedir(sd, "../escape.txt")


# ---------------------------------------------------------------------------
# _check_fd_size
# ---------------------------------------------------------------------------


class TestCheckFdSize:
    def test_raises_for_large_fd(self, tmp_path: Path) -> None:
        big = tmp_path / "big.bin"
        big.write_bytes(b"\x00" * 1024)
        with big.open("rb") as f:
            with pytest.raises(FileTooLargeError):
                _check_fd_size(f, max_bytes=512)

    def test_allows_small_fd(self, tmp_path: Path) -> None:
        small = tmp_path / "small.bin"
        small.write_bytes(b"\x00" * 100)
        with small.open("rb") as f:
            _check_fd_size(f, max_bytes=MAX_FILE_SIZE_BYTES)  # no raise

    def test_silently_skips_in_memory_buffers(self) -> None:
        """``BytesIO`` raises ``io.UnsupportedOperation`` on ``fileno()``;
        the check should fall through silently rather than erroring out.
        """
        _check_fd_size(io.BytesIO(b"x" * 100), max_bytes=10)  # no raise

    def test_silently_skips_when_fstat_fails(self, tmp_path: Path) -> None:
        """If ``os.fstat`` itself errors (e.g. closed fd), the check
        falls through. The reader's subsequent read will raise its own
        error; the size check shouldn't mask the real problem.
        """
        import os as _os

        target = tmp_path / "x.bin"
        target.write_bytes(b"data")
        f = target.open("rb")
        _os.close(f.fileno())
        # f.fileno() still returns the int, but fstat now raises EBADF
        _check_fd_size(f, max_bytes=10)  # no raise


class TestReadFileViaSafedirCompoundExtension:
    """Cover the ``.tar.gz`` / ``.tar.bz2`` / ``.tar.xz`` branch and the
    dispatcher's exception-rewrap path."""

    def test_tar_gz_returns_none(self, tmp_path: Path) -> None:
        """Archives aren't in ``_SAFEDIR_READERS`` yet (migrated in PR3b),
        but the compound-extension parsing path still executes — covers
        the ``.tar.gz`` branch in ``read_file_via_safedir``.
        """
        (tmp_path / "data.tar.gz").write_bytes(b"PK\x03\x04")
        with SafeDir.open_root(tmp_path) as sd:
            assert read_file_via_safedir(sd, "data.tar.gz") is None

    def test_dispatcher_reraises_unexpected_reader_exception(self, tmp_path: Path) -> None:
        """``read_file_via_safedir`` wraps the reader call in a try/except
        that logs then re-raises any exception. Covers the
        ``except Exception as exc: logger.error(...); raise`` block.

        Patching ``utils.readers.documents.fitz.open`` (the underlying
        library) reliably triggers the path because ``read_pdf_file``
        is reached via the dispatcher's ``_SAFEDIR_READERS`` dict —
        patching the module-level binding wouldn't intercept that.
        """
        (tmp_path / "report.pdf").write_bytes(b"%PDF-fake")
        with SafeDir.open_root(tmp_path) as sd:
            with patch(
                "utils.readers.documents.fitz.open",
                side_effect=RuntimeError("synthetic fitz failure"),
            ):
                # The reader wraps the fitz error as FileReadError;
                # the dispatcher logs and re-raises that.
                from utils.readers import FileReadError as _FRE

                with pytest.raises(_FRE):
                    read_file_via_safedir(sd, "report.pdf")
