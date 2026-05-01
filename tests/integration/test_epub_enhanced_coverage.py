"""Integration tests for utils/epub_enhanced.py covering uncovered branches.

Targets the import-guard ``except ImportError`` branches (lines 26-27,
33-34, 40-41, 45-46), the unavailable-library error path in
``EnhancedEPUBReader.__init__`` (line 170), the Pillow-unavailable
path in ``_extract_cover`` (lines 573-576), and the full read_epub
code path using a real EPUB book created with ebooklib.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Import-guard branches (lines 26-27, 33-34, 40-41, 45-46)
# ---------------------------------------------------------------------------


class TestEpubEnhancedImportGuards:
    """Cover the optional-dependency ``except ImportError`` branches in the module.

    Pattern: save the original module object, evict it from sys.modules, then
    import a *fresh* copy with the dependency blocked — asserting on the fresh
    copy's flag values.  The original module object is restored to sys.modules
    in a finally block so that other tests which hold pre-bound references to
    classes (e.g. ``EPUBMetadata``) see the same class objects they imported at
    collection time.  Using reload() would mutate the shared module object and
    create new class objects, breaking ``isinstance`` checks in unit tests that
    imported the class before the reload (the root cause of the
    ``test_get_epub_metadata`` xdist failure).
    """

    @staticmethod
    def _fresh_import(blocked: dict) -> object:
        """Import utils.epub_enhanced fresh with the given sys.modules overrides.

        Saves and evicts the current module, imports a new copy with the blocked
        entries active, records the fresh module, evicts *that* copy, then
        restores the original — all inside a finally block so the original is
        always restored even on assertion failure.

        Returns the freshly-imported module for assertion.
        """
        original = sys.modules.pop("utils.epub_enhanced", None)
        try:
            with patch.dict(sys.modules, blocked):
                import utils.epub_enhanced as fresh

                return fresh
        finally:
            # Evict whatever copy is in sys.modules now (the fresh test-only copy)
            sys.modules.pop("utils.epub_enhanced", None)
            # Restore the original module (class objects preserved for unit tests)
            if original is not None:
                sys.modules["utils.epub_enhanced"] = original

    @pytest.mark.ci
    def test_ebooklib_unavailable_sets_flag_false(self) -> None:
        """When ebooklib is absent, EBOOKLIB_AVAILABLE is False (lines 26-27)."""
        fresh = self._fresh_import({"ebooklib": None, "ebooklib.epub": None})
        assert fresh.EBOOKLIB_AVAILABLE is False

    @pytest.mark.ci
    def test_pillow_unavailable_sets_flag_false(self) -> None:
        """When PIL is absent, PILLOW_AVAILABLE is False (lines 33-34)."""
        fresh = self._fresh_import({"PIL": None, "PIL.Image": None})
        assert fresh.PILLOW_AVAILABLE is False

    @pytest.mark.ci
    def test_bs4_unavailable_sets_flag_false(self) -> None:
        """When bs4 is absent, BS4_AVAILABLE is False (lines 40-41)."""
        fresh = self._fresh_import({"bs4": None})
        assert fresh.BS4_AVAILABLE is False

    @pytest.mark.ci
    def test_xml_parsed_as_html_warning_unavailable_sets_none(self) -> None:
        """When XMLParsedAsHTMLWarning is absent from bs4, it is set to None (lines 45-46).

        A fake bs4 module (MagicMock) won't have XMLParsedAsHTMLWarning as a
        real attribute; accessing it via ``from bs4 import XMLParsedAsHTMLWarning``
        raises ImportError, which the guard converts to ``None``.
        """
        fake_bs4 = MagicMock(spec=[])  # spec=[] → AttributeError on any attr access
        fresh = self._fresh_import({"bs4": fake_bs4})
        assert fresh.XMLParsedAsHTMLWarning is None


# ---------------------------------------------------------------------------
# EnhancedEPUBReader.__init__ — unavailable library paths (line 170)
# ---------------------------------------------------------------------------


class TestEnhancedEPUBReaderInit:
    """Cover the ImportError paths in the reader constructor."""

    @pytest.mark.ci
    def test_init_raises_when_ebooklib_unavailable(self) -> None:
        """Reader.__init__ raises ImportError when ebooklib is absent (line 170)."""
        from utils.epub_enhanced import EnhancedEPUBReader

        with patch("utils.epub_enhanced.EBOOKLIB_AVAILABLE", False):
            with pytest.raises(ImportError, match="ebooklib"):
                EnhancedEPUBReader()

    @pytest.mark.ci
    def test_init_raises_when_bs4_unavailable(self) -> None:
        """Reader.__init__ raises ImportError when bs4 is absent (line 173)."""
        from utils.epub_enhanced import EnhancedEPUBReader

        with (
            patch("utils.epub_enhanced.EBOOKLIB_AVAILABLE", True),
            patch("utils.epub_enhanced.BS4_AVAILABLE", False),
        ):
            with pytest.raises(ImportError, match="beautifulsoup4"):
                EnhancedEPUBReader()


# ---------------------------------------------------------------------------
# _extract_cover — Pillow unavailable path (lines 573-576)
# ---------------------------------------------------------------------------


class TestExtractCoverPillowUnavailable:
    """Cover the early-return path in _extract_cover when Pillow is absent."""

    @pytest.mark.ci
    def test_extract_cover_returns_none_without_pillow(self, tmp_path: Path) -> None:
        """_extract_cover returns None immediately when PILLOW_AVAILABLE is False."""
        from utils.epub_enhanced import EnhancedEPUBReader

        reader = EnhancedEPUBReader()
        mock_book = MagicMock()
        fake_epub_path = tmp_path / "book.epub"

        with patch("utils.epub_enhanced.PILLOW_AVAILABLE", False):
            result = reader._extract_cover(mock_book, fake_epub_path, None)

        assert result is None
        # Book should not have been queried — we returned before touching it
        mock_book.get_metadata.assert_not_called()


# ---------------------------------------------------------------------------
# Full read_epub pipeline using a real EPUB (lines 212-213, 343, 349-373)
# ---------------------------------------------------------------------------


class TestReadEpubWithRealBook:
    """Create a minimal real EPUB via ebooklib and exercise read_epub branches."""

    @pytest.fixture
    def _require_ebooklib(self) -> None:
        pytest.importorskip("ebooklib")
        pytest.importorskip("bs4")

    @pytest.mark.ci
    def test_read_epub_with_cover_extraction(self, _require_ebooklib: None, tmp_path: Path) -> None:
        """read_epub with extract_cover=True exercises lines 212-213."""
        from ebooklib import epub as _epub

        from utils.epub_enhanced import EnhancedEPUBReader

        # Build a minimal valid EPUB
        book = _epub.EpubBook()
        book.set_identifier("test-id-001")
        book.set_title("Test Book")
        book.set_language("en")
        book.add_author("Test Author")

        # Add a chapter with enough content to pass the 50-char filter
        chapter = _epub.EpubHtml(title="Chapter 1", file_name="chapter1.xhtml", lang="en")
        chapter.set_content(
            b"<html><body><h1>Chapter One</h1>"
            b"<p>This is the first chapter of the test book with enough content to process.</p>"
            b"</body></html>"
        )
        book.add_item(chapter)
        book.toc = (_epub.Link("chapter1.xhtml", "Chapter 1", "chapter1"),)
        book.add_item(_epub.EpubNcx())
        book.add_item(_epub.EpubNav())
        book.spine = ["nav", chapter]

        epub_path = tmp_path / "test.epub"
        _epub.write_epub(str(epub_path), book)

        reader = EnhancedEPUBReader()
        # extract_cover=True exercises the cover-extraction branch (lines 212-213)
        result = reader.read_epub(epub_path, extract_cover=True)

        assert result is not None
        # read_epub returns EPUBContent; metadata is in result.metadata
        assert result.metadata.title == "Test Book"
        assert "Test Author" in result.metadata.authors

    @pytest.mark.ci
    def test_read_epub_max_chapters_limit(self, _require_ebooklib: None, tmp_path: Path) -> None:
        """max_chapters limit exercises the chapter-count guard (lines 343, 349-351)."""
        from ebooklib import epub as _epub

        from utils.epub_enhanced import EnhancedEPUBReader

        book = _epub.EpubBook()
        book.set_identifier("test-id-002")
        book.set_title("Multi-Chapter Book")
        book.set_language("en")

        chapters = []
        for i in range(5):
            ch = _epub.EpubHtml(
                title=f"Chapter {i + 1}",
                file_name=f"chapter{i + 1}.xhtml",
                lang="en",
            )
            ch.set_content(
                f"<html><body><h1>Chapter {i + 1}</h1>"
                f"<p>{'Content for chapter ' * 10} {i + 1}.</p></body></html>".encode()
            )
            book.add_item(ch)
            chapters.append(ch)

        book.toc = tuple(
            _epub.Link(f"chapter{i + 1}.xhtml", f"Chapter {i + 1}", f"ch{i + 1}") for i in range(5)
        )
        book.add_item(_epub.EpubNcx())
        book.add_item(_epub.EpubNav())
        book.spine = ["nav"] + chapters

        epub_path = tmp_path / "multi.epub"
        _epub.write_epub(str(epub_path), book)

        reader = EnhancedEPUBReader()
        result = reader.read_epub(epub_path, max_chapters=2)

        assert result is not None
        # EPUBContent.total_chapters is the count field
        assert result.total_chapters <= 2

    @pytest.mark.ci
    def test_read_epub_chapter_parse_exception_is_skipped(
        self, _require_ebooklib: None, tmp_path: Path
    ) -> None:
        """Chapter parsing exception handler (lines 371-373) skips bad chapters."""
        from ebooklib import epub as _epub

        from utils.epub_enhanced import EnhancedEPUBReader

        book = _epub.EpubBook()
        book.set_identifier("test-id-003")
        book.set_title("Exception Book")
        book.set_language("en")

        ch = _epub.EpubHtml(title="Chapter 1", file_name="chapter1.xhtml", lang="en")
        ch.set_content(
            b"<html><body><p>Good chapter with enough content to be included.</p></body></html>"
        )
        book.add_item(ch)
        book.toc = (_epub.Link("chapter1.xhtml", "Chapter 1", "ch1"),)
        book.add_item(_epub.EpubNcx())
        book.add_item(_epub.EpubNav())
        book.spine = ["nav", ch]

        epub_path = tmp_path / "exception.epub"
        _epub.write_epub(str(epub_path), book)

        reader = EnhancedEPUBReader()

        # Patch BeautifulSoup to raise on first call, succeed on subsequent
        call_count = [0]
        real_bs4 = None
        try:
            from bs4 import BeautifulSoup as _BS4

            real_bs4 = _BS4
        except ImportError:
            pytest.skip("bs4 not installed")

        def _failing_bs4(content: bytes, parser: str) -> object:
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("Simulated BS4 parse failure")
            return real_bs4(content, parser)

        with patch("utils.epub_enhanced.BeautifulSoup", _failing_bs4):
            result = reader.read_epub(epub_path)

        # Should succeed overall even though the first chapter threw
        assert result is not None
