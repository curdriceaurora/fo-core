"""Tests for the SafeDir-aware branch of ``DocumentExtractor.extract_text``.

PR3e of #267 wires ``utils.safedir.SafeDir`` into the dedup ingestion
``extract_text`` entry point so a symlink swapped into the organize root
between the directory walk and the read is refused with
``SymlinkRejected`` rather than dereferenced.

Verifies:

- Plain ``.txt`` / ``.md`` extraction round-trips through SafeDir
- Symlinks under the SafeDir root are refused (extractor returns ``""``)
- DOCX / PDF / RTF / ODT exercise the ``fileobj=`` branch of the private
  ``_extract_X`` helpers (mocked underlying libs so the tests don't need
  the full optional-dep matrix)
- On Windows the ``NotImplementedError`` fallback uses the legacy
  path-based extraction
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.deduplication.extractor import DocumentExtractor
from utils.safedir import SafeDir

pytestmark = [
    pytest.mark.ci,
    pytest.mark.unit,
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only"),
]


@pytest.fixture
def extractor() -> DocumentExtractor:
    return DocumentExtractor()


class TestSafeDirBranchPlainText:
    """The simplest path: ``.txt`` / ``.md`` round-trip via SafeDir."""

    def test_extracts_txt_via_safedir(self, extractor: DocumentExtractor, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("first line\nsecond line\n")
        out = extractor.extract_text(target)
        assert "first line" in out
        assert "second line" in out

    def test_extracts_md_via_safedir(self, extractor: DocumentExtractor, tmp_path: Path) -> None:
        target = tmp_path / "doc.md"
        target.write_text("# Heading\n\nbody text\n")
        out = extractor.extract_text(target)
        assert "Heading" in out
        assert "body text" in out

    def test_refuses_symlinked_file_and_returns_empty(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """A symlink in the organize root is refused via SafeDir.open_for_reader;
        the extractor returns ``""`` instead of dereferencing to the real
        target. Matches the legacy contract of returning ``""`` for any
        unrecoverable extraction error.
        """
        real = tmp_path / "secret.txt"
        real.write_text("DO_NOT_EXFILTRATE_THIS_CONTENT")
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "decoy.txt").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        out = extractor.extract_text(organize / "decoy.txt")
        assert out == ""

    def test_file_not_found_still_raises(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """File-not-found is a caller-level error, not a SafeDir issue —
        the public contract still raises OSError. (Matches legacy behavior.)
        """
        with pytest.raises(OSError, match="File not found"):
            extractor.extract_text(tmp_path / "missing.txt")


class TestSafeDirBranchPdf:
    """PDF extraction via the ``fileobj=`` branch of ``_extract_pdf``."""

    def test_pdf_extraction_uses_pypdf_with_fileobj(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        p = tmp_path / "report.pdf"
        p.write_bytes(b"%PDF-fake content\n")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "page text"
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("pypdf.PdfReader", return_value=mock_reader) as mock_ctor:
            out = extractor.extract_text(p)

        assert out == "page text"
        # The SafeDir branch passes a fileobj, not a path.
        call_args, _ = mock_ctor.call_args
        assert hasattr(call_args[0], "read"), "PdfReader should receive a file-like"


class TestSafeDirBranchDocx:
    """DOCX extraction via the ``fileobj=`` branch of ``_extract_docx``."""

    def test_docx_extraction_uses_docx_with_fileobj(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        p = tmp_path / "report.docx"
        p.write_bytes(b"fake docx bytes")

        mock_para = MagicMock()
        mock_para.text = "Hello docx"
        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]
        mock_doc.tables = []

        with patch("docx.Document", return_value=mock_doc) as mock_ctor:
            out = extractor.extract_text(p)

        assert out == "Hello docx"
        # The SafeDir branch passes a fileobj, not a path string.
        call_args, _ = mock_ctor.call_args
        assert hasattr(call_args[0], "read")


class TestSafeDirBranchRtf:
    """RTF extraction via the ``fileobj=`` branch of ``_extract_rtf``."""

    def test_rtf_extraction_uses_striprtf_on_fileobj_bytes(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        p = tmp_path / "letter.rtf"
        p.write_bytes(b"{\\rtf1\\ansi this is rtf}")

        with patch("striprtf.striprtf.rtf_to_text", return_value="this is rtf") as mock_strip:
            out = extractor.extract_text(p)

        assert "this is rtf" in out
        mock_strip.assert_called_once()


class TestSafeDirBranchOdt:
    """ODT extraction via the ``fileobj=`` branch of ``_extract_odt``."""

    def test_odt_extraction_reads_zip_from_fileobj(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        p = tmp_path / "doc.odt"
        # Build a minimal ODT (zip with content.xml)
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr(
                "content.xml",
                (
                    '<?xml version="1.0"?>'
                    '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                    "<office:body><office:text>"
                    "<text:p>ODT paragraph one</text:p>"
                    "<text:p>ODT paragraph two</text:p>"
                    "</office:text></office:body></office:document-content>"
                ),
            )

        out = extractor.extract_text(p)
        assert "ODT paragraph one" in out
        assert "ODT paragraph two" in out


class TestSafeDirFallbackOnNotImplemented:
    """When SafeDir raises ``NotImplementedError`` (Windows-style port not
    available), the extractor falls back to the legacy path-based read so
    the public contract still produces a result.
    """

    def test_falls_back_to_path_extraction(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        target = tmp_path / "via_path.txt"
        target.write_text("path-branch content")

        with patch(
            "services.deduplication.extractor.SafeDir.open_root",
            side_effect=NotImplementedError("simulated platform without SafeDir"),
        ):
            out = extractor.extract_text(target)
        assert "path-branch content" in out

    def test_real_oserror_does_not_silently_fall_back(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """A real OS error from SafeDir (e.g. permission denied) is logged
        and ``""`` is returned — not silently routed to the path branch
        that would defeat the SafeDir hardening.
        """
        target = tmp_path / "x.txt"
        target.write_text("data")

        with patch(
            "services.deduplication.extractor.SafeDir.open_root",
            side_effect=OSError("simulated permission denied"),
        ):
            out = extractor.extract_text(target)
        assert out == ""


class TestExtractorFileobjUnitMethods:
    """Direct unit tests on the new ``fileobj=`` branches of each helper."""

    def test_extract_pdf_fileobj(self, extractor: DocumentExtractor) -> None:
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "p1 text"
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        with patch("pypdf.PdfReader", return_value=mock_reader):
            out = extractor._extract_pdf(fileobj=io.BytesIO(b"%PDF-fake"), label="t.pdf")
        assert out == "p1 text"

    def test_extract_docx_fileobj(self, extractor: DocumentExtractor) -> None:
        mock_para = MagicMock()
        mock_para.text = "hello"
        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]
        mock_doc.tables = []
        with patch("docx.Document", return_value=mock_doc):
            out = extractor._extract_docx(fileobj=io.BytesIO(b"data"), label="t.docx")
        assert out == "hello"

    def test_extract_text_fileobj_utf8(self, extractor: DocumentExtractor) -> None:
        out = extractor._extract_text(fileobj=io.BytesIO(b"hello world"), label="t.txt")
        assert out == "hello world"

    def test_extract_text_fileobj_falls_back_to_latin1(self, extractor: DocumentExtractor) -> None:
        # Byte 0xff is invalid UTF-8 but valid latin-1
        out = extractor._extract_text(fileobj=io.BytesIO(b"hello \xff world"), label="t.txt")
        # Either latin-1 (preferred fallback) or utf-8 with errors=ignore
        assert "hello" in out
        assert "world" in out

    def test_extract_rtf_fileobj(self, extractor: DocumentExtractor) -> None:
        with patch("striprtf.striprtf.rtf_to_text", return_value="plain text"):
            out = extractor._extract_rtf(fileobj=io.BytesIO(b"{\\rtf1}"), label="t.rtf")
        assert "plain text" in out

    def test_extract_odt_fileobj(self, extractor: DocumentExtractor, tmp_path: Path) -> None:
        # Build the ODT bytes in memory.
        zip_bytes = io.BytesIO()
        with zipfile.ZipFile(zip_bytes, "w") as zf:
            zf.writestr(
                "content.xml",
                '<?xml version="1.0"?><office:document-content '
                'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                "<office:body><office:text><text:p>odt content</text:p>"
                "</office:text></office:body></office:document-content>",
            )
        zip_bytes.seek(0)
        out = extractor._extract_odt(fileobj=zip_bytes, label="t.odt")
        assert "odt content" in out


class TestSafeDirRouteSelected:
    """Locks in the routing: the SafeDir branch is the default on POSIX."""

    def test_safedir_open_root_is_called(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """The non-Windows code path always tries SafeDir.open_root before
        any fallback. Regression-locks the wire-up.
        """
        target = tmp_path / "x.txt"
        target.write_text("data")
        with patch(
            "services.deduplication.extractor.SafeDir.open_root",
            wraps=SafeDir.open_root,
        ) as mock_open_root:
            extractor.extract_text(target)
        mock_open_root.assert_called_once_with(tmp_path)
