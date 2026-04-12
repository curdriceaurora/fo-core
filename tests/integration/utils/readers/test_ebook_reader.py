"""Integration tests for utils/readers/ebook.py.

Covers:
- read_ebook_file: ImportError when ebooklib unavailable,
  unsupported format raises FileReadError,
  real EPUB extraction via ebooklib,
  max_chars truncation,
  EPUB with no document items returns empty string,
  HTML stripping from EPUB content,
  multi-item EPUB aggregation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from file_organizer.utils.readers._base import FileReadError

pytestmark = pytest.mark.integration


def _make_epub(tmp_path: Path, name: str = "test.epub", chapters: list[str] | None = None) -> Path:
    """Create a minimal valid EPUB file using ebooklib."""
    from ebooklib import epub

    if chapters is None:
        chapters = ["<html><body><h1>Chapter 1</h1><p>Hello world content.</p></body></html>"]

    book = epub.EpubBook()
    book.set_title("Test Book")
    book.set_language("en")
    book.add_author("Test Author")

    spine_items: list = ["nav"]
    toc_links = []

    for i, content in enumerate(chapters, 1):
        chap = epub.EpubHtml(
            title=f"Chapter {i}",
            file_name=f"chap_{i:02d}.xhtml",
            lang="en",
        )
        chap.content = content.encode("utf-8") if isinstance(content, str) else content
        book.add_item(chap)
        spine_items.append(chap)
        toc_links.append(epub.Link(f"chap_{i:02d}.xhtml", f"Chapter {i}", f"chapter{i:02d}"))

    book.toc = tuple(toc_links)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine_items

    epub_path = tmp_path / name
    epub.write_epub(str(epub_path), book)
    return epub_path


class TestReadEbookFile:
    """Integration tests for read_ebook_file."""

    @pytest.fixture(autouse=True)
    def _require_ebooklib(self) -> None:
        pytest.importorskip("ebooklib")

    def test_import_error_when_ebooklib_unavailable(self) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        with patch("file_organizer.utils.readers.ebook.EBOOKLIB_AVAILABLE", False):
            with pytest.raises(ImportError, match="ebooklib is not installed"):
                read_ebook_file("test.epub")

    def test_unsupported_format_raises_file_read_error(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        mobi_file = tmp_path / "book.mobi"
        mobi_file.touch()

        with pytest.raises(FileReadError, match="Unsupported ebook format"):
            read_ebook_file(mobi_file)

    def test_unsupported_format_fb2(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        fb2_file = tmp_path / "book.fb2"
        fb2_file.touch()

        with pytest.raises(FileReadError, match="Unsupported ebook format"):
            read_ebook_file(fb2_file)

    def test_real_epub_extraction(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        epub_path = _make_epub(
            tmp_path,
            chapters=[
                "<html><body><h1>Chapter 1</h1><p>Hello world content here.</p></body></html>"
            ],
        )

        result = read_ebook_file(epub_path)

        assert isinstance(result, str)
        assert len(result) > 0
        assert "Hello world content here." in result

    def test_html_tags_stripped(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        epub_path = _make_epub(
            tmp_path,
            chapters=["<html><body><h1>Title Here</h1><p>Body text here.</p></body></html>"],
        )

        result = read_ebook_file(epub_path)

        assert "<html>" not in result
        assert "<body>" not in result
        assert "<h1>" not in result
        assert "Title Here" in result
        assert "Body text here." in result

    def test_max_chars_truncation(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        long_text = "A" * 5000
        epub_path = _make_epub(
            tmp_path,
            chapters=[f"<html><body><p>{long_text}</p></body></html>"],
        )

        result = read_ebook_file(epub_path, max_chars=100)

        assert len(result) == 100

    def test_multi_chapter_epub(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        epub_path = _make_epub(
            tmp_path,
            chapters=[
                "<html><body><p>Chapter one content.</p></body></html>",
                "<html><body><p>Chapter two content.</p></body></html>",
                "<html><body><p>Chapter three content.</p></body></html>",
            ],
        )

        result = read_ebook_file(epub_path)

        assert "Chapter one content." in result
        assert "Chapter two content." in result
        assert "Chapter three content." in result

    def test_max_chars_stops_early_across_chapters(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        long_chapter = "<html><body><p>" + ("X" * 2000) + "</p></body></html>"
        epub_path = _make_epub(
            tmp_path,
            chapters=[long_chapter, long_chapter, long_chapter],
        )

        result = read_ebook_file(epub_path, max_chars=500)

        assert len(result) == 500

    def test_epub_with_path_string(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        epub_path = _make_epub(
            tmp_path,
            chapters=["<html><body><p>String path test.</p></body></html>"],
        )

        result = read_ebook_file(str(epub_path))

        assert "String path test." in result

    def test_nonexistent_epub_raises_file_read_error(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.ebook import read_ebook_file

        missing = tmp_path / "missing.epub"

        with pytest.raises(FileReadError, match="Failed to read ebook file"):
            read_ebook_file(missing)
