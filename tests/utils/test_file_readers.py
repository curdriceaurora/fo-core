"""Tests for file_readers.py."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    import ebooklib
except ImportError:
    ebooklib = None  # type: ignore[assignment]

from utils.file_readers import (
    FileReadError,
    FileTooLargeError,
    read_cad_file,
    read_docx_file,
    read_ebook_file,
    read_file,
    read_iges_file,
    read_pdf_file,
    read_presentation_file,
    read_spreadsheet_file,
    read_step_file,
    read_tar_file,
    read_text_file,
    read_zip_file,
)
from utils.readers._base import _check_file_size
from utils.readers.ebook import EBOOKLIB_AVAILABLE

pytestmark = [pytest.mark.unit, pytest.mark.integration]


@pytest.mark.unit
class TestFileReaders:
    """Tests for individual file readers in utils/file_readers.py."""

    def test_check_file_size(self, tmp_path: Path) -> None:
        """Test file size limit checking."""
        test_file = tmp_path / "large_file.txt"
        test_file.write_bytes(b"x" * 1024)  # 1 KB

        # Should pass
        _check_file_size(test_file, max_bytes=2048)

        # Should fail
        with pytest.raises(FileTooLargeError, match="File too large to process"):
            _check_file_size(test_file, max_bytes=512)

    def test_check_file_size_missing_file(self, tmp_path: Path) -> None:
        """Test file size check for missing file."""
        missing = tmp_path / "missing.txt"
        # Should return silently and let the caller handle the missing file
        _check_file_size(missing)

    def test_read_text_file(self, tmp_path: Path) -> None:
        """Test reading a plain text file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello World\nLine 2")

        content = read_text_file(test_file)
        assert "Hello World" in content
        assert "Line 2" in content

    def test_read_text_file_max_chars(self, tmp_path: Path) -> None:
        """Test max_chars limit in text reader."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("1234567890")

        content = read_text_file(test_file, max_chars=5)
        assert content == "12345"

    def test_read_text_file_error(self, tmp_path: Path) -> None:
        """Test error handling in text reader."""
        missing = tmp_path / "missing.txt"
        with pytest.raises(FileReadError):
            read_text_file(missing)

    @patch("utils.readers.documents.DOCX_AVAILABLE", True)
    @patch("utils.readers.documents.docx", create=True)
    def test_read_docx_file_success(self, mock_docx: MagicMock, tmp_path: Path) -> None:
        """Test reading DOCX file."""
        # Setup mock doc
        mock_doc = MagicMock()
        mock_para1 = MagicMock()
        mock_para1.text = "Paragraph 1"
        mock_para2 = MagicMock()
        mock_para2.text = "Paragraph 2"
        mock_doc.paragraphs = [mock_para1, mock_para2]
        mock_docx.Document.return_value = mock_doc

        test_file = tmp_path / "test.docx"
        test_file.touch()

        content = read_docx_file(test_file)
        assert "Paragraph 1\nParagraph 2" in content

    @patch("utils.readers.documents.DOCX_AVAILABLE", False)
    def test_read_docx_not_installed(self) -> None:
        """Test DOCX reading when library is missing."""
        with pytest.raises(ImportError, match="python-docx is not installed"):
            read_docx_file("test.docx")

    @patch("utils.readers.documents.DOCX_AVAILABLE", True)
    @patch("utils.readers.documents.docx", create=True)
    def test_read_docx_error(self, mock_docx: MagicMock, tmp_path: Path) -> None:
        """Test reading DOCX file with error."""
        mock_docx.Document.side_effect = RuntimeError("Doc error")
        test_file = tmp_path / "test.docx"
        test_file.touch()

        with pytest.raises(FileReadError, match="Failed to read DOCX"):
            read_docx_file(test_file)

    @patch("utils.readers.documents.PYMUPDF_AVAILABLE", True)
    @patch("utils.readers.documents.fitz", create=True)
    def test_read_pdf_file_success(self, mock_fitz: MagicMock, tmp_path: Path) -> None:
        """Test reading PDF file."""
        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 2
        mock_page1 = MagicMock()
        mock_page1.get_text.return_value = "Page 1 content"
        mock_page2 = MagicMock()
        mock_page2.get_text.return_value = "Page 2 content"
        mock_doc.load_page.side_effect = [mock_page1, mock_page2]
        # fitz.open() is used as a context manager; __enter__ must return mock_doc
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        test_file = tmp_path / "test.pdf"
        test_file.touch()

        content = read_pdf_file(test_file)
        assert "Page 1 content" in content
        assert "Page 2 content" in content

    @patch("utils.readers.documents.PYMUPDF_AVAILABLE", False)
    def test_read_pdf_not_installed(self) -> None:
        """Test PDF reading when missing library."""
        with pytest.raises(ImportError, match="PyMuPDF is not installed"):
            read_pdf_file("test.pdf")

    @patch("utils.readers.documents.PYMUPDF_AVAILABLE", True)
    @patch("utils.readers.documents.fitz", create=True)
    def test_read_pdf_error(self, mock_fitz: MagicMock, tmp_path: Path) -> None:
        """Test PDF reading error."""
        mock_fitz.open.side_effect = RuntimeError("PDF render error")
        test_file = tmp_path / "test.pdf"
        test_file.touch()

        with pytest.raises(FileReadError):
            read_pdf_file(test_file)

    def test_read_spreadsheet_csv(self, tmp_path: Path) -> None:
        """Test reading CSV spreadsheet."""
        test_file = tmp_path / "test.csv"
        test_file.write_text("Col1,Col2\nA,B")

        content = read_spreadsheet_file(test_file)
        assert "Col1,Col2" in content
        assert "A,B" in content

    @patch("utils.readers.documents.OPENPYXL_AVAILABLE", True)
    def test_read_spreadsheet_xlsx(self, tmp_path: Path) -> None:
        """Test reading XLSX spreadsheet."""
        openpyxl = pytest.importorskip("openpyxl")

        test_file = tmp_path / "test.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "Col1"
        ws["B1"] = "Col2"
        ws["A2"] = "A"
        ws["B2"] = "B"
        wb.save(test_file)

        content = read_spreadsheet_file(test_file)
        assert "Col1,Col2" in content
        assert "A,B" in content

    @patch("utils.readers.documents.OPENPYXL_AVAILABLE", False)
    def test_read_spreadsheet_not_installed(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.xlsx"
        test_file.touch()
        with pytest.raises(ImportError, match="openpyxl is not installed"):
            read_spreadsheet_file(test_file)

    def test_read_spreadsheet_bad_format(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.unknown"
        test_file.touch()
        with pytest.raises(FileReadError, match="Unsupported spreadsheet"):
            read_spreadsheet_file(test_file)

    @patch("utils.readers.documents.PPTX_AVAILABLE", True)
    @patch("utils.readers.documents.Presentation", create=True)
    def test_read_presentation_file(self, mock_prs_cls: MagicMock, tmp_path: Path) -> None:
        """Test reading PPTX."""
        mock_prs = MagicMock()
        mock_slide = MagicMock()
        mock_shape = MagicMock()
        mock_shape.text = "Presentation text"
        mock_slide.shapes = [mock_shape]
        mock_prs.slides = [mock_slide]
        mock_prs_cls.return_value = mock_prs

        test_file = tmp_path / "test.pptx"
        test_file.touch()

        content = read_presentation_file(test_file)
        assert "Slide 1" in content
        assert "Presentation text" in content

    @patch("utils.readers.documents.PPTX_AVAILABLE", False)
    def test_read_presentation_not_installed(self) -> None:
        with pytest.raises(ImportError, match="python-pptx is not installed"):
            read_presentation_file("test.pptx")

    @pytest.mark.skipif(not EBOOKLIB_AVAILABLE, reason="ebooklib not installed")
    @patch("utils.readers.ebook.EBOOKLIB_AVAILABLE", True)
    @patch("utils.readers.ebook.epub", create=True)
    def test_read_ebook_file(self, mock_epub: MagicMock, tmp_path: Path) -> None:
        """Test reading EPUB."""
        mock_book = MagicMock()
        mock_item = MagicMock()
        mock_item.get_type.return_value = ebooklib.ITEM_DOCUMENT if ebooklib is not None else 9
        mock_item.get_content.return_value = b"<html><body>Ebook Content</body></html>"
        mock_book.get_items.return_value = [mock_item]
        mock_epub.read_epub.return_value = mock_book

        test_file = tmp_path / "test.epub"
        test_file.touch()

        content = read_ebook_file(test_file)
        assert "Ebook Content" in content

    @patch("utils.readers.ebook.EBOOKLIB_AVAILABLE", False)
    def test_read_ebook_not_installed(self) -> None:
        with pytest.raises(ImportError, match="ebooklib is not installed"):
            read_ebook_file("test.epub")

    @patch("utils.readers.ebook.EBOOKLIB_AVAILABLE", True)
    def test_read_ebook_unsupported_format(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.mobi"
        test_file.touch()
        with pytest.raises(FileReadError, match="Unsupported ebook format"):
            read_ebook_file(test_file)


@pytest.mark.unit
class TestReadFileGeneric:
    """Test the read_file routing function."""

    @patch("utils.readers.read_text_file")
    def test_read_file_text(self, mock_read_text: MagicMock, tmp_path: Path) -> None:
        test_file = tmp_path / "doc.txt"
        test_file.touch()
        mock_read_text.return_value = "text"

        read_file(test_file)
        mock_read_text.assert_called_once_with(test_file)

    @patch("utils.readers.read_docx_file")
    def test_read_file_docx(self, mock_read_docx: MagicMock, tmp_path: Path) -> None:
        test_file = tmp_path / "doc.docx"
        test_file.touch()
        read_file(test_file)
        mock_read_docx.assert_called_once()

    @patch("utils.readers.read_pdf_file")
    def test_read_file_pdf(self, mock_read_pdf: MagicMock, tmp_path: Path) -> None:
        test_file = tmp_path / "doc.pdf"
        test_file.touch()
        read_file(test_file)
        mock_read_pdf.assert_called_once()

    @patch("utils.readers.read_spreadsheet_file")
    def test_read_file_spreadsheet(self, mock_read_csv: MagicMock, tmp_path: Path) -> None:
        test_file = tmp_path / "data.csv"
        test_file.touch()
        read_file(test_file)
        mock_read_csv.assert_called_once()

    def test_read_file_unsupported(self, tmp_path: Path) -> None:
        test_file = tmp_path / "unknown.xyz123"
        test_file.touch()
        assert read_file(test_file) is None


# ────────────────────────────────────────────────────────────────────────────
# New tests below: archive, scientific (optional-dep), CAD, and read_file dispatch
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestArchiveReaders:
    """Tests for ZIP and TAR archive readers."""

    def test_read_zip_file(self, tmp_path):
        """Create a real zip with temp files and verify metadata extraction."""
        # Create files to zip
        (tmp_path / "hello.txt").write_text("Hello, world!")
        (tmp_path / "data.csv").write_text("a,b,c\n1,2,3")

        zip_path = tmp_path / "archive.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_path / "hello.txt", "hello.txt")
            zf.write(tmp_path / "data.csv", "data.csv")

        content = read_zip_file(zip_path)
        assert "ZIP Archive: archive.zip" in content
        assert "Total files: 2" in content
        assert "hello.txt" in content
        assert "data.csv" in content
        assert "Encrypted: No" in content

    def test_read_zip_empty_archive(self, tmp_path):
        """Test reading an empty zip archive."""
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w"):
            pass  # empty archive

        content = read_zip_file(zip_path)
        assert "Total files: 0" in content
        assert "ZIP Archive: empty.zip" in content

    def test_read_tar_file(self, tmp_path):
        """Create a real tar.gz with temp files and verify metadata."""
        (tmp_path / "readme.md").write_text("# README")
        (tmp_path / "notes.txt").write_text("some notes")

        tar_path = tmp_path / "archive.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(tmp_path / "readme.md", arcname="readme.md")
            tf.add(tmp_path / "notes.txt", arcname="notes.txt")

        content = read_tar_file(tar_path)
        assert "TAR Archive: archive.tar.gz" in content
        assert "Compression: GZ" in content
        assert "Total files: 2" in content
        assert "readme.md" in content
        assert "notes.txt" in content

    def test_read_tar_bz2(self, tmp_path):
        """Test reading a tar.bz2 archive."""
        (tmp_path / "file.txt").write_text("bz2 content")

        tar_path = tmp_path / "archive.tar.bz2"
        with tarfile.open(tar_path, "w:bz2") as tf:
            tf.add(tmp_path / "file.txt", arcname="file.txt")

        content = read_tar_file(tar_path)
        assert "TAR Archive: archive.tar.bz2" in content
        assert "Total files: 1" in content
        assert "file.txt" in content

    def test_read_tar_plain(self, tmp_path):
        """Test reading a plain .tar archive (no compression)."""
        (tmp_path / "plain.txt").write_text("plain tar content")

        tar_path = tmp_path / "archive.tar"
        with tarfile.open(tar_path, "w") as tf:
            tf.add(tmp_path / "plain.txt", arcname="plain.txt")

        content = read_tar_file(tar_path)
        assert "TAR Archive: archive.tar" in content
        assert "Compression: None" in content
        assert "Total files: 1" in content
        assert "plain.txt" in content

    def test_read_zip_error(self, tmp_path):
        """Corrupt zip raises FileReadError."""
        corrupt_zip = tmp_path / "corrupt.zip"
        corrupt_zip.write_bytes(b"this is not a zip file at all")

        with pytest.raises(FileReadError, match="Failed to read ZIP file"):
            read_zip_file(corrupt_zip)

    def test_read_tar_error(self, tmp_path):
        """Corrupt tar raises FileReadError."""
        corrupt_tar = tmp_path / "corrupt.tar.gz"
        corrupt_tar.write_bytes(b"this is not a tar file at all")

        with pytest.raises(FileReadError, match="Failed to read TAR file"):
            read_tar_file(corrupt_tar)


@pytest.mark.unit
class TestScientificReaders:
    """Tests for optional-dependency scientific format readers (unavailable paths)."""

    @patch("utils.readers.archives.PY7ZR_AVAILABLE", False)
    def test_read_7z_not_installed(self):
        """py7zr not installed raises ImportError."""
        from utils.file_readers import read_7z_file

        with pytest.raises(ImportError, match="py7zr is not installed"):
            read_7z_file("test.7z")

    @patch("utils.readers.archives.RARFILE_AVAILABLE", False)
    def test_read_rar_not_installed(self):
        """rarfile not installed raises ImportError."""
        from utils.file_readers import read_rar_file

        with pytest.raises(ImportError, match="rarfile is not installed"):
            read_rar_file("test.rar")

    @patch("utils.readers.scientific.H5PY_AVAILABLE", False)
    def test_read_hdf5_not_installed(self):
        """h5py not installed raises ImportError."""
        from utils.file_readers import read_hdf5_file

        with pytest.raises(ImportError, match="h5py is not installed"):
            read_hdf5_file("test.hdf5")

    @patch("utils.readers.scientific.NETCDF4_AVAILABLE", False)
    def test_read_netcdf_not_installed(self):
        """netCDF4 not installed raises ImportError."""
        from utils.file_readers import read_netcdf_file

        with pytest.raises(ImportError, match="netCDF4 is not installed"):
            read_netcdf_file("test.nc")

    @patch("utils.readers.scientific.SCIPY_AVAILABLE", False)
    def test_read_mat_not_installed(self):
        """scipy not installed raises ImportError."""
        from utils.file_readers import read_mat_file

        with pytest.raises(ImportError, match="scipy is not installed"):
            read_mat_file("test.mat")

    @patch("utils.readers.cad.EZDXF_AVAILABLE", False)
    def test_read_dxf_not_installed(self):
        """ezdxf not installed raises ImportError for DXF."""
        from utils.file_readers import read_dxf_file

        with pytest.raises(ImportError, match="ezdxf is not installed"):
            read_dxf_file("test.dxf")

    @patch("utils.readers.cad.EZDXF_AVAILABLE", False)
    def test_read_dwg_not_installed(self):
        """ezdxf not installed raises ImportError for DWG."""
        from utils.file_readers import read_dwg_file

        with pytest.raises(ImportError, match="ezdxf is not installed"):
            read_dwg_file("test.dwg")


@pytest.mark.unit
class TestCADReaders:
    """Tests for text-based CAD readers (STEP, IGES) and CAD dispatch."""

    def test_read_step_file(self, tmp_path):
        """Create a minimal STEP file and verify header extraction."""
        step_content = (
            "ISO-10303-21;\n"
            "HEADER;\n"
            "FILE_DESCRIPTION(('Test'),'2;1');\n"
            "FILE_NAME('test.step','2024-01-01',('Author'),('Org'),'','','');\n"
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
            "ENDSEC;\n"
            "DATA;\n"
            "#1=SHAPE_REPRESENTATION('test',(#2),#3);\n"
            "ENDSEC;\n"
            "END-ISO-10303-21;\n"
        )
        step_path = tmp_path / "model.step"
        step_path.write_text(step_content)

        content = read_step_file(step_path)
        assert "STEP File Information" in content
        assert "model.step" in content
        assert "FILE_DESCRIPTION" in content
        assert "FILE_NAME" in content
        assert "FILE_SCHEMA" in content
        assert "AUTOMOTIVE_DESIGN" in content

    def test_read_step_file_no_header(self, tmp_path):
        """STEP file without proper header returns basic info only."""
        step_path = tmp_path / "noheader.step"
        step_path.write_text("ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")

        content = read_step_file(step_path)
        assert "STEP File Information" in content
        assert "noheader.step" in content
        # Should not crash, but no header fields extracted
        assert "FILE_DESCRIPTION" not in content

    def test_read_iges_file(self, tmp_path):
        """Create a minimal IGES file with proper column structure."""
        # IGES format: 80-char lines with section type at column 73
        start_line = "{:<72}S{:>7d}\n".format("Test IGES file", 1)
        global_line = "{:<72}G{:>7d}\n".format("1H,,1H;,4Htest,8Htest.igs,8Htest.igs", 1)
        term_line = "{:<72}T{:>7d}\n".format("S      1G      1D      0P      0", 1)

        iges_path = tmp_path / "model.igs"
        iges_path.write_text(start_line + global_line + term_line)

        content = read_iges_file(iges_path)
        assert "IGES File Information" in content
        assert "model.igs" in content
        assert "Start Section" in content
        assert "Test IGES file" in content

    def test_read_iges_file_empty(self, tmp_path):
        """IGES file with no valid sections returns basic info."""
        iges_path = tmp_path / "empty.igs"
        iges_path.write_text("Short line\nAnother short line\n")

        content = read_iges_file(iges_path)
        assert "IGES File Information" in content
        assert "empty.igs" in content
        # No sections parsed
        assert "Start Section" not in content

    def test_read_cad_file_unsupported(self, tmp_path):
        """Unsupported CAD extension raises FileReadError."""
        cad_path = tmp_path / "model.obj"
        cad_path.touch()

        with pytest.raises(FileReadError, match="Unsupported CAD file format"):
            read_cad_file(cad_path)


@pytest.mark.unit
class TestReadFileExpanded:
    """Expanded tests for the read_file dispatch function."""

    def test_read_file_zip(self, tmp_path):
        """Verify read_file routes .zip to read_zip_file."""
        (tmp_path / "f.txt").write_text("data")
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(tmp_path / "f.txt", "f.txt")

        content = read_file(zip_path)
        assert content is not None
        assert "ZIP Archive" in content
        assert "f.txt" in content

    def test_read_file_tar_gz(self, tmp_path):
        """Verify compound extension .tar.gz routes correctly."""
        (tmp_path / "data.txt").write_text("tar content")
        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(tmp_path / "data.txt", arcname="data.txt")

        content = read_file(tar_path)
        assert content is not None
        assert "TAR Archive" in content
        assert "data.txt" in content

    def test_read_file_step(self, tmp_path):
        """Verify .step routes to read_cad_file -> read_step_file."""
        step_content = (
            "ISO-10303-21;\n"
            "HEADER;\n"
            "FILE_DESCRIPTION(('Test'),'2;1');\n"
            "ENDSEC;\n"
            "DATA;\n"
            "ENDSEC;\n"
            "END-ISO-10303-21;\n"
        )
        step_path = tmp_path / "model.step"
        step_path.write_text(step_content)

        content = read_file(step_path)
        assert content is not None
        assert "STEP File Information" in content

    @patch("utils.readers.cad.EZDXF_AVAILABLE", False)
    def test_read_file_dxf(self, tmp_path):
        """Verify .dxf routes to read_cad_file -> read_dxf_file (raises when unavailable)."""
        dxf_path = tmp_path / "drawing.dxf"
        dxf_path.touch()

        with pytest.raises(ImportError, match="ezdxf is not installed"):
            read_file(dxf_path)


def test_read_rtf_file_returns_text(tmp_path: Path) -> None:
    rtf_file = tmp_path / "sample.rtf"
    rtf_file.write_bytes(rb"{\rtf1\ansi{\fonttbl\f0\fswiss Helvetica;}\f0\pard Hello RTF\par}")
    from utils.readers import read_file

    result = read_file(rtf_file)
    assert result is not None
    assert "Hello RTF" in result


# ────────────────────────────────────────────────────────────────────────────
# Coverage-hardening tests: fileobj paths, ValueError branches, ImportError
# branches for optional libraries, and unsupported-suffix paths.
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDocumentsMissingLibraries:
    """Tests for ImportError branches when optional libraries are absent."""

    @patch("utils.readers.documents.DOCX_AVAILABLE", False)
    def test_read_docx_raises_import_error_when_unavailable(self) -> None:
        """Patch DOCX_AVAILABLE to False → read_docx_file raises ImportError."""
        from utils.readers.documents import read_docx_file as _read_docx

        with pytest.raises(ImportError, match="python-docx"):
            _read_docx(Path("dummy.docx"))

    @patch("utils.readers.documents.PYMUPDF_AVAILABLE", False)
    def test_read_pdf_raises_import_error_when_unavailable(self) -> None:
        """Patch PYMUPDF_AVAILABLE to False → read_pdf_file raises ImportError."""
        from utils.readers.documents import read_pdf_file as _read_pdf

        with pytest.raises(ImportError, match="PyMuPDF"):
            _read_pdf(Path("dummy.pdf"))

    @patch("utils.readers.documents.STRIPRTF_AVAILABLE", False)
    def test_read_rtf_raises_import_error_when_unavailable(self) -> None:
        """Patch STRIPRTF_AVAILABLE to False → read_rtf_file raises ImportError."""
        from utils.readers.documents import read_rtf_file as _read_rtf

        with pytest.raises(ImportError, match="striprtf"):
            _read_rtf(Path("dummy.rtf"))

    @patch("utils.readers.documents.OPENPYXL_AVAILABLE", False)
    def test_read_spreadsheet_xlsx_raises_import_error_when_unavailable(self) -> None:
        """Patch OPENPYXL_AVAILABLE to False → spreadsheet reader raises ImportError for xlsx."""
        import io

        from utils.readers.documents import read_spreadsheet_file as _read_ss

        # Use fileobj so the extension is taken from file_path without needing the file to exist
        fileobj = io.BytesIO(b"fake xlsx bytes")
        with pytest.raises(ImportError, match="openpyxl"):
            _read_ss(file_path="test.xlsx", fileobj=fileobj)

    @patch("utils.readers.documents.PPTX_AVAILABLE", False)
    def test_read_presentation_raises_import_error_when_unavailable(self) -> None:
        """Patch PPTX_AVAILABLE to False → read_presentation_file raises ImportError."""
        from utils.readers.documents import read_presentation_file as _read_pptx

        with pytest.raises(ImportError, match="python-pptx"):
            _read_pptx(Path("dummy.pptx"))


@pytest.mark.unit
class TestDocumentsValueErrorPaths:
    """Tests for ValueError when neither file_path nor fileobj is provided."""

    def test_read_text_file_no_args_raises_value_error(self) -> None:
        from utils.readers.documents import read_text_file as _read_text

        with pytest.raises(ValueError, match="read_text_file requires"):
            _read_text()

    @patch("utils.readers.documents.DOCX_AVAILABLE", True)
    def test_read_docx_file_no_args_raises_value_error(self) -> None:
        from utils.readers.documents import read_docx_file as _read_docx

        with pytest.raises(ValueError, match="read_docx_file requires"):
            _read_docx()

    @patch("utils.readers.documents.PYMUPDF_AVAILABLE", True)
    def test_read_pdf_file_no_args_raises_value_error(self) -> None:
        from utils.readers.documents import read_pdf_file as _read_pdf

        with pytest.raises(ValueError, match="read_pdf_file requires"):
            _read_pdf()

    @patch("utils.readers.documents.STRIPRTF_AVAILABLE", True)
    def test_read_rtf_file_no_args_raises_value_error(self) -> None:
        from utils.readers.documents import read_rtf_file as _read_rtf

        with pytest.raises(ValueError, match="read_rtf_file requires"):
            _read_rtf()

    def test_read_spreadsheet_file_no_args_raises_value_error(self) -> None:
        from utils.readers.documents import read_spreadsheet_file as _read_ss

        with pytest.raises(ValueError, match="read_spreadsheet_file requires"):
            _read_ss()

    @patch("utils.readers.documents.PPTX_AVAILABLE", True)
    def test_read_presentation_file_no_args_raises_value_error(self) -> None:
        from utils.readers.documents import read_presentation_file as _read_pptx

        with pytest.raises(ValueError, match="read_presentation_file requires"):
            _read_pptx()


@pytest.mark.unit
class TestDocumentsFileobjPaths:
    """Tests for fileobj= entry points (SafeDir-friendly paths)."""

    def test_read_text_file_via_fileobj(self, tmp_path: Path) -> None:
        """read_text_file accepts a binary fileobj and returns decoded text."""
        import io

        from utils.readers.documents import read_text_file as _read_text

        content = b"Hello from fileobj\nLine 2"
        fileobj = io.BytesIO(content)
        result = _read_text(fileobj=fileobj)
        assert "Hello from fileobj" in result
        assert "Line 2" in result

    def test_read_text_file_via_fileobj_with_label(self, tmp_path: Path) -> None:
        """read_text_file uses file_path for label only when fileobj provided."""
        import io

        from utils.readers.documents import read_text_file as _read_text

        content = b"Labelled content"
        fileobj = io.BytesIO(content)
        result = _read_text(file_path="myfile.txt", fileobj=fileobj)
        assert "Labelled content" in result

    @patch("utils.readers.documents.DOCX_AVAILABLE", True)
    @patch("utils.readers.documents.docx", create=True)
    def test_read_docx_file_via_fileobj(self, mock_docx: MagicMock) -> None:
        """read_docx_file accepts a binary fileobj."""
        import io

        from utils.readers.documents import read_docx_file as _read_docx

        mock_doc = MagicMock()
        mock_para = MagicMock()
        mock_para.text = "Fileobj paragraph"
        mock_doc.paragraphs = [mock_para]
        mock_docx.Document.return_value = mock_doc

        fileobj = io.BytesIO(b"fake docx bytes")
        result = _read_docx(fileobj=fileobj)
        assert "Fileobj paragraph" in result

    @patch("utils.readers.documents.DOCX_AVAILABLE", True)
    @patch("utils.readers.documents.docx", create=True)
    def test_read_docx_file_via_fileobj_raises_file_read_error(self, mock_docx: MagicMock) -> None:
        """Exceptions from python-docx when using fileobj are wrapped as FileReadError."""
        import io

        from utils.readers.documents import read_docx_file as _read_docx

        mock_docx.Document.side_effect = RuntimeError("corrupted docx")
        fileobj = io.BytesIO(b"bad bytes")
        with pytest.raises(FileReadError, match="Failed to read DOCX"):
            _read_docx(fileobj=fileobj)

    @patch("utils.readers.documents.PYMUPDF_AVAILABLE", True)
    @patch("utils.readers.documents.fitz", create=True)
    def test_read_pdf_file_via_fileobj_falls_back_to_in_memory(self, mock_fitz: MagicMock) -> None:
        """read_pdf_file with a BytesIO fileobj falls back to in-memory open (no real fd)."""
        import io

        from utils.readers.documents import read_pdf_file as _read_pdf

        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 1
        mock_page = MagicMock()
        mock_page.get_text.return_value = "PDF page text"
        mock_doc.load_page.return_value = mock_page
        # fitz.open() is a context manager
        mock_fitz.open.return_value.__enter__.return_value = mock_doc
        mock_fitz.open.return_value.__exit__.return_value = False

        # BytesIO has no real fd, so fileno() raises io.UnsupportedOperation (OSError subclass)
        fileobj = io.BytesIO(b"%PDF-1.4 fake pdf bytes")
        result = _read_pdf(fileobj=fileobj)
        assert "PDF page text" in result

    @patch("utils.readers.documents.PYMUPDF_AVAILABLE", True)
    @patch("utils.readers.documents.fitz", create=True)
    def test_read_pdf_file_via_fileobj_raises_file_read_error(self, mock_fitz: MagicMock) -> None:
        """Exceptions from PyMuPDF when using fileobj are wrapped as FileReadError."""
        import io

        from utils.readers.documents import read_pdf_file as _read_pdf

        mock_fitz.open.side_effect = RuntimeError("corrupted pdf")
        fileobj = io.BytesIO(b"bad bytes")
        with pytest.raises(FileReadError, match="Failed to read PDF"):
            _read_pdf(fileobj=fileobj)

    @patch("utils.readers.documents.STRIPRTF_AVAILABLE", True)
    @patch("utils.readers.documents._rtf_to_text", create=True)
    def test_read_rtf_file_via_fileobj(self, mock_rtf_to_text: MagicMock) -> None:
        """read_rtf_file accepts a binary fileobj."""
        import io

        from utils.readers.documents import read_rtf_file as _read_rtf

        mock_rtf_to_text.return_value = "Plain text from RTF"
        fileobj = io.BytesIO(b"{\\rtf1 Hello}")
        result = _read_rtf(fileobj=fileobj)
        assert "Plain text from RTF" in result

    @patch("utils.readers.documents.STRIPRTF_AVAILABLE", True)
    @patch("utils.readers.documents._rtf_to_text", create=True)
    def test_read_rtf_file_via_fileobj_raises_file_read_error(
        self, mock_rtf_to_text: MagicMock
    ) -> None:
        """Exceptions from striprtf when using fileobj are wrapped as FileReadError."""
        import io

        from utils.readers.documents import read_rtf_file as _read_rtf

        mock_rtf_to_text.side_effect = RuntimeError("rtf parse failure")
        fileobj = io.BytesIO(b"bad rtf")
        with pytest.raises(FileReadError, match="Failed to read RTF"):
            _read_rtf(fileobj=fileobj)

    def test_read_spreadsheet_csv_via_fileobj(self, tmp_path: Path) -> None:
        """read_spreadsheet_file accepts a binary fileobj for CSV."""
        import io

        from utils.readers.documents import read_spreadsheet_file as _read_ss

        csv_bytes = b"Name,Score\nAlice,100\nBob,90"
        fileobj = io.BytesIO(csv_bytes)
        result = _read_ss(file_path="data.csv", fileobj=fileobj)
        assert "Name,Score" in result
        assert "Alice,100" in result

    @patch("utils.readers.documents.OPENPYXL_AVAILABLE", True)
    @patch("utils.readers.documents.openpyxl", create=True)
    def test_read_spreadsheet_xlsx_via_fileobj(self, mock_openpyxl: MagicMock) -> None:
        """read_spreadsheet_file accepts a binary fileobj for XLSX."""
        import io

        from utils.readers.documents import read_spreadsheet_file as _read_ss

        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = [(("col1", "col2"),), (("a", "b"),)]
        mock_wb = MagicMock()
        mock_wb.active = mock_ws
        mock_openpyxl.load_workbook.return_value = mock_wb

        fileobj = io.BytesIO(b"fake xlsx")
        result = _read_ss(file_path="data.xlsx", fileobj=fileobj)
        assert isinstance(result, str)

    def test_read_spreadsheet_unsupported_suffix_raises_file_read_error(self) -> None:
        """read_spreadsheet_file with an unsupported extension raises FileReadError."""
        import io

        from utils.readers.documents import read_spreadsheet_file as _read_ss

        # Use fileobj so the extension is taken from file_path without needing the file to exist;
        # the unsupported-suffix guard fires inside _dispatch_spreadsheet before any I/O.
        fileobj = io.BytesIO(b"irrelevant")
        with pytest.raises(FileReadError, match="Unsupported"):
            _read_ss(file_path="test.txt", fileobj=fileobj)

    @patch("utils.readers.documents.OPENPYXL_AVAILABLE", True)
    @patch("utils.readers.documents.openpyxl", create=True)
    def test_read_spreadsheet_fileobj_raises_file_read_error_on_exception(
        self, mock_openpyxl: MagicMock
    ) -> None:
        """Generic exceptions from openpyxl via fileobj are wrapped as FileReadError."""
        import io

        from utils.readers.documents import read_spreadsheet_file as _read_ss

        mock_openpyxl.load_workbook.side_effect = RuntimeError("xlsx parse failed")
        fileobj = io.BytesIO(b"bad xlsx")
        with pytest.raises(FileReadError, match="Failed to read spreadsheet"):
            _read_ss(file_path="data.xlsx", fileobj=fileobj)

    @patch("utils.readers.documents.PPTX_AVAILABLE", True)
    @patch("utils.readers.documents.Presentation", create=True)
    def test_read_presentation_via_fileobj(self, mock_prs_cls: MagicMock) -> None:
        """read_presentation_file accepts a binary fileobj."""
        import io

        from utils.readers.documents import read_presentation_file as _read_pptx

        mock_prs = MagicMock()
        mock_slide = MagicMock()
        mock_shape = MagicMock()
        mock_shape.text = "Slide text from fileobj"
        mock_slide.shapes = [mock_shape]
        mock_prs.slides = [mock_slide]
        mock_prs_cls.return_value = mock_prs

        fileobj = io.BytesIO(b"fake pptx")
        result = _read_pptx(fileobj=fileobj)
        assert "Slide 1" in result
        assert "Slide text from fileobj" in result

    @patch("utils.readers.documents.PPTX_AVAILABLE", True)
    @patch("utils.readers.documents.Presentation", create=True)
    def test_read_presentation_via_fileobj_raises_file_read_error(
        self, mock_prs_cls: MagicMock
    ) -> None:
        """Exceptions from python-pptx via fileobj are wrapped as FileReadError."""
        import io

        from utils.readers.documents import read_presentation_file as _read_pptx

        mock_prs_cls.side_effect = RuntimeError("corrupted pptx")
        fileobj = io.BytesIO(b"bad bytes")
        with pytest.raises(FileReadError, match="Failed to read presentation"):
            _read_pptx(fileobj=fileobj)


@pytest.mark.unit
class TestDocumentsRtfPathBranch:
    """Tests for read_rtf_file path-based branch error handling."""

    @patch("utils.readers.documents.STRIPRTF_AVAILABLE", True)
    @patch("utils.readers.documents._rtf_to_text", create=True)
    def test_read_rtf_file_path_raises_file_read_error(
        self, mock_rtf_to_text: MagicMock, tmp_path: Path
    ) -> None:
        """Exceptions from striprtf via path branch are wrapped as FileReadError."""
        from utils.readers.documents import read_rtf_file as _read_rtf

        mock_rtf_to_text.side_effect = RuntimeError("parse failure")
        rtf_file = tmp_path / "sample.rtf"
        rtf_file.write_bytes(b"{\\rtf1 bad}")
        with pytest.raises(FileReadError, match="Failed to read RTF"):
            _read_rtf(rtf_file)


@pytest.mark.unit
class TestDocumentsPdfLazyParsing:
    """Tests for _parse_pdf_stream fallback when fileno() raises OSError."""

    @patch("utils.readers.documents.PYMUPDF_AVAILABLE", True)
    @patch("utils.readers.documents.fitz", create=True)
    def test_pdf_fileobj_with_real_fd_uses_dev_fd_path(
        self, mock_fitz: MagicMock, tmp_path: Path
    ) -> None:
        """When fileobj has a real fd, _parse_pdf_stream uses /dev/fd/{fd}."""
        from utils.readers.documents import read_pdf_file as _read_pdf

        mock_doc = MagicMock()
        mock_doc.__len__.return_value = 1
        mock_page = MagicMock()
        mock_page.get_text.return_value = "PDF from fd"
        mock_doc.load_page.return_value = mock_page
        mock_fitz.open.return_value.__enter__.return_value = mock_doc
        mock_fitz.open.return_value.__exit__.return_value = False

        pdf_file = tmp_path / "real.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 minimal")
        with pdf_file.open("rb") as f:
            result = _read_pdf(file_path="real.pdf", fileobj=f)
        assert "PDF from fd" in result


@pytest.mark.unit
class TestDocumentsRowLimitBranches:
    """Tests for max_rows break branches in _parse_csv and _parse_xlsx."""

    def test_parse_csv_respects_max_rows(self, tmp_path: Path) -> None:
        """_parse_csv stops at max_rows — covers the break on line 288."""
        import io

        from utils.readers.documents import read_spreadsheet_file as _read_ss

        # 5 data rows; limit to 2
        csv_bytes = b"a\nb\nc\nd\ne\n"
        fileobj = io.BytesIO(csv_bytes)
        result = _read_ss(file_path="data.csv", max_rows=2, fileobj=fileobj)
        lines = [ln for ln in result.splitlines() if ln]
        assert len(lines) == 2
        assert lines[0] == "a"
        assert lines[1] == "b"

    @patch("utils.readers.documents.OPENPYXL_AVAILABLE", True)
    @patch("utils.readers.documents.openpyxl", create=True)
    def test_parse_xlsx_respects_max_rows(self, mock_openpyxl: MagicMock) -> None:
        """_parse_xlsx stops at max_rows — covers the break on line 303."""
        import io

        from utils.readers.documents import read_spreadsheet_file as _read_ss

        # Simulate 5 rows returned by iter_rows; limit to 3
        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = [
            ("row1col1",),
            ("row2col1",),
            ("row3col1",),
            ("row4col1",),
            ("row5col1",),
        ]
        mock_wb = MagicMock()
        mock_wb.active = mock_ws
        mock_openpyxl.load_workbook.return_value = mock_wb

        fileobj = io.BytesIO(b"fake xlsx")
        result = _read_ss(file_path="data.xlsx", max_rows=3, fileobj=fileobj)
        lines = [ln for ln in result.splitlines() if ln]
        assert len(lines) == 3

    @patch("utils.readers.documents.OPENPYXL_AVAILABLE", True)
    @patch("utils.readers.documents.openpyxl", create=True)
    def test_parse_xlsx_skips_empty_rows(self, mock_openpyxl: MagicMock) -> None:
        """_parse_xlsx skips rows where all cells are None — covers branch 305->301."""
        import io

        from utils.readers.documents import read_spreadsheet_file as _read_ss

        # First row has data; second row is all None (empty)
        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = [
            ("data_cell",),
            (None,),  # all-None row → row_str.strip(",") is empty → skipped
        ]
        mock_wb = MagicMock()
        mock_wb.active = mock_ws
        mock_openpyxl.load_workbook.return_value = mock_wb

        fileobj = io.BytesIO(b"fake xlsx")
        result = _read_ss(file_path="data.xlsx", fileobj=fileobj)
        lines = [ln for ln in result.splitlines() if ln]
        assert len(lines) == 1
        assert "data_cell" in lines[0]


@pytest.mark.unit
class TestDocumentsPathBranchErrors:
    """Tests for except-Exception branches in path-based readers."""

    def test_read_text_file_via_fileobj_oserror_wrapped(self) -> None:
        """OSError from _parse_text via fileobj is wrapped as FileReadError (lines 98-99)."""
        import io

        from utils.readers.documents import read_text_file as _read_text

        # Create a fileobj whose .read() raises OSError
        bad_fileobj = MagicMock(spec=io.RawIOBase)
        bad_fileobj.read.side_effect = OSError("I/O error")
        bad_fileobj.fileno.side_effect = OSError("no fd")

        with pytest.raises(FileReadError, match="Failed to read text file"):
            _read_text(fileobj=bad_fileobj)

    @patch("utils.readers.documents.PPTX_AVAILABLE", True)
    @patch("utils.readers.documents.Presentation", create=True)
    def test_read_presentation_path_raises_file_read_error(
        self, mock_prs_cls: MagicMock, tmp_path: Path
    ) -> None:
        """Exceptions from python-pptx via path branch are wrapped as FileReadError (428-429)."""
        from utils.readers.documents import read_presentation_file as _read_pptx

        mock_prs_cls.side_effect = RuntimeError("corrupted pptx on disk")
        pptx_file = tmp_path / "slides.pptx"
        pptx_file.write_bytes(b"fake pptx bytes")
        with pytest.raises(FileReadError, match="Failed to read presentation"):
            _read_pptx(pptx_file)

    @patch("utils.readers.documents.OPENPYXL_AVAILABLE", True)
    @patch("utils.readers.documents.openpyxl", create=True)
    def test_read_spreadsheet_xlsx_path_raises_file_read_error(
        self, mock_openpyxl: MagicMock, tmp_path: Path
    ) -> None:
        """Generic exceptions from openpyxl via path branch are wrapped as FileReadError (371-372)."""
        from utils.readers.documents import read_spreadsheet_file as _read_ss

        mock_openpyxl.load_workbook.side_effect = RuntimeError("xlsx on disk failed")
        xlsx_file = tmp_path / "data.xlsx"
        xlsx_file.write_bytes(b"fake xlsx bytes")
        with pytest.raises(FileReadError, match="Failed to read spreadsheet"):
            _read_ss(xlsx_file)
