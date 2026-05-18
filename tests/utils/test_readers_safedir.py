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
    read_7z_file,
    read_docx_file,
    read_file_via_safedir,
    read_pdf_file,
    read_presentation_file,
    read_rar_file,
    read_rtf_file,
    read_spreadsheet_file,
    read_tar_file,
    read_text_file,
    read_zip_file,
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
# Archive readers — fileobj= branch (PR3b)
# ---------------------------------------------------------------------------


def _make_zip(path: Path) -> None:
    import zipfile as _zf

    with _zf.ZipFile(path, "w", _zf.ZIP_DEFLATED) as zf:
        zf.writestr("hello.txt", "hello\n")
        zf.writestr("data.csv", "a,b,c\n1,2,3\n")


def _make_tar(path: Path, *, mode: str = "w:gz") -> None:
    import tarfile as _tf

    payload = path.parent / f".__tar_payload_{path.name}.txt"
    payload.write_text("tar payload")
    with _tf.open(path, mode) as tf:
        tf.add(payload, arcname="readme.txt")
    payload.unlink()


class TestReadZipFileFileobj:
    def test_reads_from_fileobj(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "archive.zip"
        _make_zip(zip_path)

        with zip_path.open("rb") as f:
            out = read_zip_file(file_path=zip_path, fileobj=f)
        assert "ZIP Archive: archive.zip" in out
        assert "Total files: 2" in out
        assert "hello.txt" in out
        assert "data.csv" in out

    def test_label_falls_back_when_no_file_path(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "archive.zip"
        _make_zip(zip_path)
        with zip_path.open("rb") as f:
            out = read_zip_file(fileobj=f)
        assert "ZIP Archive: <fileobj>" in out

    def test_fileobj_error_wraps_as_file_read_error(self, tmp_path: Path) -> None:
        """A zipfile parse error from the fileobj branch wraps as FileReadError."""
        from utils.readers import FileReadError as _FRE

        with patch(
            "utils.readers.archives.zipfile.ZipFile",
            side_effect=RuntimeError("synthetic zip failure"),
        ):
            with pytest.raises(_FRE, match="ZIP file"):
                read_zip_file(fileobj=io.BytesIO(b"not a zip"))

    def test_requires_arg(self) -> None:
        with pytest.raises(ValueError, match="file_path or fileobj"):
            read_zip_file()


class TestReadTarFileFileobj:
    def test_reads_tar_gz_from_fileobj(self, tmp_path: Path) -> None:
        tar_path = tmp_path / "archive.tar.gz"
        _make_tar(tar_path, mode="w:gz")
        with tar_path.open("rb") as f:
            out = read_tar_file(file_path=tar_path, fileobj=f)
        assert "TAR Archive: archive.tar.gz" in out
        assert "Compression: GZ" in out
        assert "Total files: 1" in out
        assert "readme.txt" in out

    def test_reads_plain_tar_from_fileobj(self, tmp_path: Path) -> None:
        tar_path = tmp_path / "archive.tar"
        _make_tar(tar_path, mode="w")
        with tar_path.open("rb") as f:
            out = read_tar_file(file_path=tar_path, fileobj=f)
        assert "Compression: None" in out

    def test_compression_unknown_when_no_file_path(self, tmp_path: Path) -> None:
        """Without a filename hint we can't display the compression type;
        tarfile still auto-detects from the magic bytes so the read succeeds.
        """
        tar_path = tmp_path / "archive.tar.gz"
        _make_tar(tar_path, mode="w:gz")
        with tar_path.open("rb") as f:
            out = read_tar_file(fileobj=f)
        assert "Compression: Unknown" in out
        assert "readme.txt" in out

    def test_requires_arg(self) -> None:
        with pytest.raises(ValueError, match="file_path or fileobj"):
            read_tar_file()


class TestRead7zFileFileobj:
    @patch("utils.readers.archives.py7zr")
    def test_reads_from_fileobj(self, mock_py7zr: MagicMock, tmp_path: Path) -> None:
        mock_file_info = MagicMock()
        mock_file_info.filename = "hello.txt"
        mock_file_info.uncompressed = 12
        mock_file_info.compressed = 6
        mock_archive = MagicMock()
        mock_archive.__enter__.return_value = mock_archive
        mock_archive.__exit__.return_value = None
        mock_archive.list.return_value = [mock_file_info]
        mock_archive.password_protected = False
        mock_py7zr.SevenZipFile.return_value = mock_archive

        out = read_7z_file(file_path=tmp_path / "archive.7z", fileobj=io.BytesIO(b"7z fake"))
        assert "7Z Archive: archive.7z" in out
        assert "Total files: 1" in out
        assert "hello.txt" in out

    @patch("utils.readers.archives.py7zr")
    def test_fileobj_error_wraps_as_file_read_error(
        self, mock_py7zr: MagicMock, tmp_path: Path
    ) -> None:
        from utils.readers import FileReadError as _FRE

        mock_py7zr.SevenZipFile.side_effect = RuntimeError("synthetic 7z failure")
        with pytest.raises(_FRE, match="7Z file"):
            read_7z_file(file_path=tmp_path / "x.7z", fileobj=io.BytesIO(b"not 7z"))

    def test_requires_arg(self) -> None:
        with pytest.raises(ValueError, match="file_path or fileobj"):
            read_7z_file()


class TestReadRarFileFileobj:
    @patch("utils.readers.archives.rarfile")
    def test_reads_from_fileobj(self, mock_rarfile_mod: MagicMock, tmp_path: Path) -> None:
        mock_info = MagicMock()
        mock_info.filename = "hello.txt"
        mock_info.file_size = 100
        mock_info.compress_size = 50
        mock_rf = MagicMock()
        mock_rf.__enter__.return_value = mock_rf
        mock_rf.__exit__.return_value = None
        mock_rf.infolist.return_value = [mock_info]
        mock_rf.needs_password.return_value = False
        # The reader references the module's RarCannotExec class for the
        # narrower except branch — patch.dict-style replacement on the
        # module-level binding loses that attribute, so re-attach a real
        # class object that's never raised by the mock.
        mock_rarfile_mod.RarFile.return_value = mock_rf
        mock_rarfile_mod.RarCannotExec = type("RarCannotExec", (Exception,), {})

        out = read_rar_file(file_path=tmp_path / "archive.rar", fileobj=io.BytesIO(b"rar fake"))
        assert "RAR Archive: archive.rar" in out
        assert "Total files: 1" in out
        assert "hello.txt" in out

    @patch("utils.readers.archives.rarfile")
    def test_fileobj_error_wraps_as_file_read_error(
        self, mock_rarfile_mod: MagicMock, tmp_path: Path
    ) -> None:
        from utils.readers import FileReadError as _FRE

        mock_rarfile_mod.RarFile.side_effect = RuntimeError("synthetic rar failure")
        mock_rarfile_mod.RarCannotExec = type("RarCannotExec", (Exception,), {})
        with pytest.raises(_FRE, match="RAR file"):
            read_rar_file(file_path=tmp_path / "x.rar", fileobj=io.BytesIO(b"not rar"))

    @patch("utils.readers.archives.rarfile")
    def test_fileobj_rar_cannot_exec_wraps_with_unrar_hint(
        self, mock_rarfile_mod: MagicMock, tmp_path: Path
    ) -> None:
        """Missing ``unrar`` tool surfaces as ``FileReadError`` with the
        install-hint message — the narrower ``RarCannotExec`` branch.
        """
        from utils.readers import FileReadError as _FRE

        rar_cannot_exec_cls = type("RarCannotExec", (Exception,), {})
        mock_rarfile_mod.RarCannotExec = rar_cannot_exec_cls
        mock_rarfile_mod.RarFile.side_effect = rar_cannot_exec_cls("unrar missing")
        with pytest.raises(_FRE, match="unrar tool not found"):
            read_rar_file(file_path=tmp_path / "x.rar", fileobj=io.BytesIO(b"not rar"))

    def test_requires_arg(self) -> None:
        with pytest.raises(ValueError, match="file_path or fileobj"):
            read_rar_file()


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

    def test_zip_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.archives._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_zip_file(fileobj=io.BytesIO(b"any"))

    def test_7z_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.archives._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_7z_file(fileobj=io.BytesIO(b"any"))

    def test_tar_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.archives._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_tar_file(fileobj=io.BytesIO(b"any"))

    def test_rar_reader_propagates(self, tmp_path: Path) -> None:
        with patch("utils.readers.archives._check_fd_size", side_effect=self._raise_too_large):
            with pytest.raises(FileTooLargeError):
                read_rar_file(fileobj=io.BytesIO(b"any"))


class TestRequiresFilePathOrFileobj:
    """Every reader must reject calls with neither arg supplied."""

    @pytest.mark.parametrize(
        "reader",
        [
            read_text_file,
            read_docx_file,
            read_pdf_file,
            read_rtf_file,
            read_presentation_file,
            read_zip_file,
            read_7z_file,
            read_tar_file,
            read_rar_file,
        ],
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
        """Extensions not yet in ``_SAFEDIR_READERS`` (ebooks, scientific,
        CAD) return None — caller can fall back to legacy path-based
        ``read_file``.
        """
        (tmp_path / "book.epub").write_bytes(b"PK\x03\x04")
        with SafeDir.open_root(tmp_path) as sd:
            assert read_file_via_safedir(sd, "book.epub") is None

    def test_rejects_bad_name(self, tmp_path: Path) -> None:
        """Component-name validation rides on SafeDir.open_for_reader."""
        with SafeDir.open_root(tmp_path) as sd:
            with pytest.raises(ValueError):
                read_file_via_safedir(sd, "../escape.txt")

    def test_reads_real_zip_archive(self, tmp_path: Path) -> None:
        """End-to-end: dispatcher resolves ``.zip`` via the new SafeDir
        archive entry and returns the metadata-list output of
        ``read_zip_file``.
        """
        _make_zip(tmp_path / "real.zip")
        with SafeDir.open_root(tmp_path) as sd:
            out = read_file_via_safedir(sd, "real.zip")
        assert out is not None
        assert "ZIP Archive: real.zip" in out
        assert "hello.txt" in out

    @pytest.mark.parametrize(
        ("name", "tar_mode", "expected_compression"),
        [
            ("real.tar", "w", "None"),
            ("real.tar.gz", "w:gz", "GZ"),
            ("real.tgz", "w:gz", "GZ"),
            ("real.tar.bz2", "w:bz2", "BZ2"),
            ("real.tbz2", "w:bz2", "BZ2"),
            ("real.tar.xz", "w:xz", "XZ"),
        ],
    )
    def test_reads_real_tar_archive_for_each_extension(
        self, tmp_path: Path, name: str, tar_mode: str, expected_compression: str
    ) -> None:
        """End-to-end via the dispatcher for every TAR extension in
        ``_SAFEDIR_READERS``. A dropped mapping (incl. the compound-extension
        branch in ``read_file_via_safedir``) would silently return ``None`` —
        we assert real content makes it through for each alias so the
        mapping has positive coverage.
        """
        _make_tar(tmp_path / name, mode=tar_mode)
        with SafeDir.open_root(tmp_path) as sd:
            out = read_file_via_safedir(sd, name)
        assert out is not None, f"dispatcher returned None for {name!r}"
        assert f"TAR Archive: {name}" in out
        assert f"Compression: {expected_compression}" in out
        assert "readme.txt" in out

    def test_dispatches_7z_extension(self, tmp_path: Path) -> None:
        """``.7z`` routes to ``read_7z_file`` via the SafeDir dispatcher.

        ``_SAFEDIR_READERS`` holds direct function references — patching
        ``read_7z_file`` at the module level won't intercept. Instead we
        patch the underlying py7zr binding and assert the reader reached
        it with the SafeDir-opened fileobj (same approach as
        ``test_dispatcher_reraises_unexpected_reader_exception``).
        """
        (tmp_path / "data.7z").write_bytes(b"7z placeholder")
        mock_file_info = MagicMock()
        mock_file_info.filename = "inside.txt"
        mock_file_info.uncompressed = 5
        mock_file_info.compressed = 5
        mock_archive = MagicMock()
        mock_archive.__enter__.return_value = mock_archive
        mock_archive.__exit__.return_value = None
        mock_archive.list.return_value = [mock_file_info]
        mock_archive.password_protected = False

        with SafeDir.open_root(tmp_path) as sd:
            with patch("utils.readers.archives.py7zr") as mock_py7zr:
                mock_py7zr.SevenZipFile.return_value = mock_archive
                out = read_file_via_safedir(sd, "data.7z")
        assert out is not None
        assert "7Z Archive: data.7z" in out
        # SafeDir-opened fileobj reached the underlying library.
        mock_py7zr.SevenZipFile.assert_called_once()
        call_args, _ = mock_py7zr.SevenZipFile.call_args
        assert hasattr(call_args[0], "read")

    def test_dispatches_rar_extension(self, tmp_path: Path) -> None:
        """``.rar`` routes to ``read_rar_file`` via the SafeDir dispatcher."""
        (tmp_path / "data.rar").write_bytes(b"rar placeholder")
        mock_info = MagicMock()
        mock_info.filename = "inside.txt"
        mock_info.file_size = 10
        mock_info.compress_size = 8
        mock_rf = MagicMock()
        mock_rf.__enter__.return_value = mock_rf
        mock_rf.__exit__.return_value = None
        mock_rf.infolist.return_value = [mock_info]
        mock_rf.needs_password.return_value = False

        with SafeDir.open_root(tmp_path) as sd:
            with patch("utils.readers.archives.rarfile") as mock_rarfile_mod:
                mock_rarfile_mod.RarFile.return_value = mock_rf
                mock_rarfile_mod.RarCannotExec = type("RarCannotExec", (Exception,), {})
                out = read_file_via_safedir(sd, "data.rar")
        assert out is not None
        assert "RAR Archive: data.rar" in out
        mock_rarfile_mod.RarFile.assert_called_once()
        call_args, _ = mock_rarfile_mod.RarFile.call_args
        assert hasattr(call_args[0], "read")

    def test_refuses_symlinked_zip(self, tmp_path: Path) -> None:
        """A symlinked archive in the organize root must be refused, not
        followed and indexed.
        """
        real = tmp_path / "real.zip"
        _make_zip(real)
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "decoy.zip").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported")

        with SafeDir.open_root(organize) as sd:
            with pytest.raises(SymlinkRejected):
                read_file_via_safedir(sd, "decoy.zip")


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

    def test_tar_gz_dispatches_to_tar_reader(self, tmp_path: Path) -> None:
        """Once archives migrated in PR3b, ``.tar.gz`` resolves via the
        compound-extension branch and reaches ``read_tar_file`` with the
        SafeDir-opened fd. We assert the route taken via patching, since
        crafting a real tar.gz here would duplicate the round-trip test
        below.
        """
        (tmp_path / "data.tar.gz").write_bytes(b"\x1f\x8b\x08\x00")  # gz header
        with SafeDir.open_root(tmp_path) as sd:
            with patch(
                "utils.readers.archives.tarfile.open",
                side_effect=RuntimeError("synthetic tarfile failure"),
            ):
                from utils.readers import FileReadError as _FRE

                with pytest.raises(_FRE):
                    read_file_via_safedir(sd, "data.tar.gz")

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
