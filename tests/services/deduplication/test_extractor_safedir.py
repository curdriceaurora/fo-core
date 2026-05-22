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


class TestAnchoredTraversalExtractor:
    """Regression tests for issue #325: component-wise SafeDir traversal
    via ``scan_root``.

    A symlink swapped into an intermediate directory between the scan root
    and the leaf file must be refused (``extract_text`` returns ``""``)
    rather than followed — closes the nested-ancestor TOCTOU window
    documented in #286/#325.
    """

    def test_reads_file_via_scan_root(self, extractor: DocumentExtractor, tmp_path: Path) -> None:
        """Happy path: a real file nested under scan_root is read correctly."""
        scan_root = tmp_path / "root"
        subdir = scan_root / "subdir"
        subdir.mkdir(parents=True)
        target = subdir / "doc.txt"
        target.write_text("anchored content")

        result = extractor.extract_text(target, scan_root=scan_root)
        assert "anchored content" in result

    def test_symlinked_intermediate_dir_is_refused(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """A symlink swapped into an intermediate directory between scan_root
        and the leaf is refused when scan_root is provided — regression for
        the nested-ancestor TOCTOU window documented in #286/#325.

        Layout::

            tmp_path/real_outside/secret.txt   <- sensitive file OUTSIDE scan_root
            tmp_path/scan_root/               <- trusted scan root
            tmp_path/scan_root/evil_link -> tmp_path/real_outside
            apparent path: scan_root/evil_link/secret.txt

        Without anchored traversal, ``open_root(file_path.parent)`` would open
        ``evil_link`` as a plain directory and read secret.txt through it.
        With anchored traversal, ``open_subdir("evil_link")`` detects the
        symlink and raises ``SymlinkRejected`` → ``extract_text`` returns ``""``.
        """
        outside = tmp_path / "real_outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("TOP_SECRET_CONTENT")

        scan_root = tmp_path / "scan_root"
        scan_root.mkdir()

        try:
            (scan_root / "evil_link").symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        apparent_path = scan_root / "evil_link" / "secret.txt"
        result = extractor.extract_text(apparent_path, scan_root=scan_root)
        assert result == ""
        assert "TOP_SECRET_CONTENT" not in result

    def test_scan_root_none_still_uses_parent_rooted_safedir(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """When scan_root=None the existing parent-rooted SafeDir path is used
        (no regression on the default call-site behaviour).
        """
        target = tmp_path / "plain.txt"
        target.write_text("parent rooted content")

        with patch(
            "services.deduplication.extractor.SafeDir.open_root",
            wraps=SafeDir.open_root,
        ) as mock_open_root:
            result = extractor.extract_text(target, scan_root=None)

        assert "parent rooted content" in result
        mock_open_root.assert_called_once_with(tmp_path)

    def test_extract_batch_threads_scan_root(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """extract_batch passes scan_root through to each extract_text call."""
        scan_root = tmp_path / "root"
        scan_root.mkdir()
        f1 = scan_root / "a.txt"
        f2 = scan_root / "b.txt"
        f1.write_text("alpha")
        f2.write_text("beta")

        with patch.object(extractor, "extract_text", wraps=extractor.extract_text) as mock_extract:
            extractor.extract_batch([f1, f2], scan_root=scan_root)

        calls = mock_extract.call_args_list
        assert len(calls) == 2
        for call in calls:
            assert call.kwargs.get("scan_root") == scan_root


# ---------------------------------------------------------------------------
# Issue #349 — S2, C4, C5 fixes to the anchored scan_root branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestAnchoredTraversalIssue349:
    """Targeted tests for the issue #349 refinements to the scan_root branch."""

    def test_anchored_open_used_for_all_extensions_when_scan_root_set(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """S2: open_anchored_reader must be used for ALL extensions, not just those
        registered in the utils.readers SafeDir registry.

        The old code called read_file_via_safedir_anchored which returned None for
        unregistered extensions (e.g. ODT) and fell through to an unanchored
        SafeDir.open_root(file_path.parent), bypassing intermediate-ancestor
        protection.  The fix uses open_anchored_reader directly for all extensions.
        """
        scan_root = tmp_path / "root"
        subdir = scan_root / "sub"
        subdir.mkdir(parents=True)
        target = subdir / "doc.txt"
        target.write_text("full anchored content")

        # Patch open_anchored_reader to verify it's called (not open_for_reader
        # on a parent-rooted SafeDir, which would mean S2 is not fixed).
        original_open_anchored = SafeDir.open_anchored_reader
        anchored_calls: list[str] = []

        def spy_anchored(self: SafeDir, relative_path: object) -> int:
            anchored_calls.append(str(relative_path))
            return original_open_anchored(self, relative_path)  # type: ignore[arg-type]

        with patch.object(SafeDir, "open_anchored_reader", spy_anchored):
            result = extractor.extract_text(target, scan_root=scan_root)

        assert result == "full anchored content"
        assert len(anchored_calls) == 1, (
            "open_anchored_reader must be called exactly once for anchored traversal"
        )

    def test_anchored_scan_root_uses_full_file_extractor_not_readers_defaults(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """C4: _extract_from_fileobj must be called, not read_file_via_safedir_anchored.

        read_file_via_safedir_anchored used utils.readers defaults (max_pages=5,
        max_chars=5000), silently truncating documents.  The fix passes the fd from
        open_anchored_reader directly to _extract_from_fileobj which uses the full-
        file extractors.
        """
        scan_root = tmp_path / "root"
        scan_root.mkdir()
        target = scan_root / "doc.txt"
        target.write_text("full document content for dedup")

        with patch.object(
            extractor, "_extract_from_fileobj", wraps=extractor._extract_from_fileobj
        ) as mock_extract_fileobj:
            result = extractor.extract_text(target, scan_root=scan_root)

        assert result == "full document content for dedup"
        mock_extract_fileobj.assert_called_once()

    def test_fileread_error_from_extractor_returns_empty_string(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """C5: FileReadError raised by a format extractor must be caught and return "".

        The old except clause only caught (OSError, ValueError, ImportError).
        FileReadError (a distinct Exception subclass) escaped, breaking the
        documented 'return "" on any extraction failure' contract.
        """
        from utils.readers import FileReadError

        scan_root = tmp_path / "root"
        scan_root.mkdir()
        target = scan_root / "corrupt.txt"
        target.write_bytes(b"\x00" * 100)

        with patch.object(
            extractor,
            "_extract_from_fileobj",
            side_effect=FileReadError("simulated corrupt file"),
        ):
            result = extractor.extract_text(target, scan_root=scan_root)

        assert result == "", (
            "FileReadError from a format extractor must be caught and return empty string"
        )

    def test_not_implemented_error_falls_through_to_parent_rooted_safedir(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """NotImplementedError from SafeDir.open_root(scan_root) falls through to
        the parent-rooted SafeDir branch, still returning the file content.

        This exercises the ``except NotImplementedError`` clause that handles
        platforms where SafeDir primitives are unavailable.  When scan_root is
        supplied but SafeDir raises NotImplementedError, the extractor must
        silently fall back instead of propagating the error.
        """
        scan_root = tmp_path / "root"
        scan_root.mkdir()
        target = scan_root / "fallback.txt"
        target.write_text("fallback content via parent-rooted path")

        call_args: list[object] = []
        original_open_root = SafeDir.open_root

        def patched_open_root(path: object) -> object:
            call_args.append(path)
            if len(call_args) == 1:
                raise NotImplementedError("simulated SafeDir unavailable on this platform")
            return original_open_root(path)  # type: ignore[arg-type]

        with patch("services.deduplication.extractor.SafeDir.open_root", patched_open_root):
            result = extractor.extract_text(target, scan_root=scan_root)

        assert "fallback content via parent-rooted path" in result, (
            "NotImplementedError from scan_root SafeDir.open_root must fall through to "
            "parent-rooted SafeDir branch and still return extracted content"
        )

    def test_osfdopen_failure_in_scan_root_closes_fd_and_returns_empty(
        self, extractor: DocumentExtractor, tmp_path: Path
    ) -> None:
        """OSError from os.fdopen in the scan_root branch closes the raw fd
        and returns ``""`` via the outer ``(OSError, …)`` handler.

        This exercises lines 135-137 of extractor.py — the cleanup path
        where os.fdopen raises after open_anchored_reader returned a valid fd:

        .. code-block:: python

            try:
                fileobj = os.fdopen(fd, "rb", closefd=True)  # raises
            except OSError:        # line 135  ← covered here
                os.close(fd)       # line 136  ← covered here
                raise              # line 137  ← covered here
        """
        import os as _os

        scan_root = tmp_path / "root"
        scan_root.mkdir()
        target = scan_root / "doc.txt"
        target.write_bytes(b"content")

        call_count = 0
        original_fdopen = _os.fdopen

        def patched_fdopen(fd: int, *args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("simulated os.fdopen failure in scan_root branch")
            return original_fdopen(fd, *args, **kwargs)  # type: ignore[arg-type]

        with patch("services.deduplication.extractor.os.fdopen", patched_fdopen):
            result = extractor.extract_text(target, scan_root=scan_root)

        assert result == "", (
            "OSError from os.fdopen in the scan_root branch must close the fd "
            "and return empty string via the outer (OSError, ...) handler"
        )
