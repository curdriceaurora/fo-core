"""Regression tests for issue #326 reader-robustness findings.

Covers four unresolved findings from PRs #274, #275, #282, #300:

5.1 — STEP reader byte cap: adversarial single-line file must be truncated
      at ``_MAX_STEP_BYTES`` (512 KB), not read entirely into memory.
5.2 — ebooklib version floor: project must pin ``ebooklib>=0.20`` so the
      fileobj branch works correctly.
5.3 — Dispatcher coverage: every archive extension in ``_SAFEDIR_READERS``
      must route to the correct reader; a silently-dropped mapping would
      fall through to None without the tests catching it.
5.4 — EnhancedEPUBReader SafeDir regression: symlinked EPUB under a scan
      root must be refused closed (SymlinkRejected) by the enhanced reader.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.ci,
    pytest.mark.unit,
]


# ---------------------------------------------------------------------------
# 5.1 — STEP reader byte cap
# ---------------------------------------------------------------------------


class TestStepByteCap:
    """``read_step_file`` must honour ``_MAX_STEP_BYTES`` regardless of
    line count so an adversarial single-line STEP file doesn't fill memory.
    """

    def test_byte_cap_constant_defined(self) -> None:
        """``_MAX_STEP_BYTES`` must be exported from the cad reader module
        with a sensible value (≥ 10 KB to allow real headers, ≤ 1 MB to
        prevent unbounded reads).
        """
        from utils.readers.cad import _MAX_STEP_BYTES

        assert _MAX_STEP_BYTES >= 10 * 1024, "_MAX_STEP_BYTES must be at least 10 KB"
        assert _MAX_STEP_BYTES <= 1024 * 1024, "_MAX_STEP_BYTES must be at most 1 MB"

    def test_path_branch_truncates_single_line_file(self, tmp_path: Path) -> None:
        """A STEP file whose content is one very long line exceeds
        ``_MAX_STEP_BYTES`` and must not be read in full — the result
        includes the truncation marker.
        """
        from utils.readers import read_step_file
        from utils.readers.cad import _MAX_STEP_BYTES

        oversized_line = "A" * (_MAX_STEP_BYTES + 1)
        step_path = tmp_path / "huge.step"
        step_path.write_text(oversized_line)

        result = read_step_file(step_path)

        assert isinstance(result, str)
        assert len(result) < _MAX_STEP_BYTES + 512, (
            "result should not contain the full oversized line"
        )
        assert "truncated" in result, "truncation note must appear in result"

    def test_fileobj_branch_truncates_single_line_file(self, tmp_path: Path) -> None:
        """Same protection via the fileobj branch (SafeDir-friendly path)."""
        from utils.readers import read_step_file
        from utils.readers.cad import _MAX_STEP_BYTES

        oversized_line = "B" * (_MAX_STEP_BYTES + 1)
        data = oversized_line.encode("utf-8")
        step_path = tmp_path / "huge_fileobj.step"

        result = read_step_file(
            file_path=step_path,
            fileobj=io.BytesIO(data),
        )

        assert isinstance(result, str)
        assert len(result) < _MAX_STEP_BYTES + 512, (
            "fileobj branch should not contain the full oversized line"
        )
        assert "truncated" in result, "truncation note must appear in result (fileobj branch)"

    def test_normal_step_file_not_truncated(self, tmp_path: Path) -> None:
        """A well-formed STEP file under the byte cap must be read completely."""
        from utils.readers import read_step_file

        step_content = (
            "ISO-10303-21;\n"
            "HEADER;\n"
            "FILE_DESCRIPTION(('Normal file'),'2;1');\n"
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
            "ENDSEC;\n"
            "DATA;\n"
            "#1=POINT('',(0.,0.,0.));\n"
            "ENDSEC;\n"
            "END-ISO-10303-21;\n"
        )
        step_path = tmp_path / "normal.step"
        step_path.write_text(step_content)

        result = read_step_file(step_path)

        assert "FILE_DESCRIPTION" in result
        assert "truncated" not in result

    def test_max_lines_stops_before_byte_cap(self, tmp_path: Path) -> None:
        """When max_lines is exhausted before the byte cap is reached, no
        truncation marker appears in the output — the reader stopped cleanly
        at the line count, not the memory limit.
        """
        from utils.readers import read_step_file

        # 200 short lines, each well below _MAX_STEP_BYTES in total.
        short_lines = "\n".join([f"#LINE{i}=POINT('',(0.,0.,{i}.));" for i in range(200)])
        step_path = tmp_path / "manylines.step"
        step_path.write_text(short_lines)

        # max_lines=5 stops after 5 lines — well before the byte cap.
        result = read_step_file(step_path, max_lines=5)
        assert isinstance(result, str)
        assert "truncated" not in result
        # The output should be a valid STEP information block.
        assert "STEP File Information" in result

    def test_byte_cap_triggers_within_max_lines(self, tmp_path: Path) -> None:
        """When a single line exceeds the byte cap, the reader breaks out of
        the max_lines loop early and the truncation marker appears.
        """
        from utils.readers import read_step_file
        from utils.readers.cad import _MAX_STEP_BYTES

        giant_line = "C" * (_MAX_STEP_BYTES + 1)
        giant_path = tmp_path / "giant.step"
        giant_path.write_text(giant_line)

        # max_lines=100 would allow reading 100 lines, but the one enormous
        # line exceeds the byte cap on the first iteration.
        result = read_step_file(giant_path, max_lines=100)
        assert "truncated" in result


# ---------------------------------------------------------------------------
# 5.2 — ebooklib version floor
# ---------------------------------------------------------------------------


class TestEbooklibVersionFloor:
    """``ebooklib`` must be pinned at a version that accepts file objects.

    The SafeDir fileobj branch of the EPUB readers relies on
    ``epub.read_epub(fileobj)`` working correctly.  The floor in
    ``pyproject.toml`` must be ``>=0.20`` (which added the ``isinstance``
    guard around ``os.path.isdir`` that previously raised ``TypeError`` for
    file-like inputs).
    """

    def test_installed_ebooklib_version_meets_floor(self) -> None:
        """Installed ebooklib version must be at least 0.20."""
        pytest.importorskip("ebooklib")
        import importlib.metadata

        from packaging.version import Version

        installed = Version(importlib.metadata.version("ebooklib"))
        required_floor = Version("0.20")
        assert installed >= required_floor, (
            f"Installed ebooklib {installed} is below the required floor {required_floor}. "
            "Bump pyproject.toml to ebooklib>=0.20."
        )

    def test_pyproject_toml_floor_is_0_20_or_higher(self, tmp_path: Path) -> None:
        """Verify the constraint in ``pyproject.toml`` enforces ``>=0.20``."""
        import re

        here = Path(__file__).resolve()
        pyproject = here.parent.parent.parent / "pyproject.toml"
        assert pyproject.exists(), f"pyproject.toml not found at {pyproject}"

        text = pyproject.read_text()
        # Find the ebooklib requirement line.
        match = re.search(r'"ebooklib([^"]*)"', text)
        assert match is not None, "ebooklib not found in pyproject.toml dependencies"

        spec_str = match.group(1)
        # Extract the lower bound version number.
        lower = re.search(r">=(\d+\.\d+(?:\.\d+)?)", spec_str)
        assert lower is not None, f"ebooklib spec {spec_str!r} has no lower bound — must be >=0.20"
        from packaging.version import Version

        assert Version(lower.group(1)) >= Version("0.20"), (
            f"pyproject.toml ebooklib floor {lower.group(1)!r} is below 0.20"
        )


# ---------------------------------------------------------------------------
# 5.3 — Dispatcher coverage: archive extensions in _SAFEDIR_READERS
# ---------------------------------------------------------------------------


def _make_zip_bytes(name: str = "inner.txt") -> bytes:
    """Return a minimal ZIP archive as bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, "hello")
    return buf.getvalue()


def _make_tar_bytes(mode: str = "w") -> bytes:
    """Return a minimal TAR archive as bytes."""
    import tarfile

    buf = io.BytesIO()
    payload = io.BytesIO(b"tar content")
    info = tarfile.TarInfo(name="readme.txt")
    info.size = len(payload.getvalue())
    payload.seek(0)
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        tf.addfile(info, payload)
    return buf.getvalue()


@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestSafedirReaderDispatcherArchiveCoverage:
    """Every archive extension in ``_SAFEDIR_READERS`` must map to the
    correct reader.  A silently-dropped mapping would return ``None`` from
    ``read_file_via_safedir`` rather than raising — these tests assert that
    real content arrives for each alias.

    Issue #326 finding 5.3: only ``.zip`` and ``.tar.gz`` were tested
    through the dispatcher before this suite was added.
    """

    def test_zip_extension_dispatches(self, tmp_path: Path) -> None:
        """Extension ``.zip`` routes to ``read_zip_file``."""
        from utils.readers import read_file_via_safedir
        from utils.safedir import SafeDir

        p = tmp_path / "archive.zip"
        p.write_bytes(_make_zip_bytes())
        with SafeDir.open_root(tmp_path) as sd:
            out = read_file_via_safedir(sd, "archive.zip")
        assert out is not None
        assert "ZIP Archive" in out

    def test_7z_extension_dispatches(self, tmp_path: Path) -> None:
        """``.7z`` routes to ``read_7z_file`` via the dispatcher."""
        from utils.readers import read_file_via_safedir
        from utils.safedir import SafeDir

        (tmp_path / "archive.7z").write_bytes(b"7z placeholder")

        mock_file_info = MagicMock()
        mock_file_info.filename = "inside.txt"
        mock_file_info.uncompressed = 4
        mock_file_info.compressed = 4
        mock_archive = MagicMock()
        mock_archive.__enter__.return_value = mock_archive
        mock_archive.__exit__.return_value = None
        mock_archive.list.return_value = [mock_file_info]
        mock_archive.password_protected = False

        with SafeDir.open_root(tmp_path) as sd:
            with patch("utils.readers.archives.py7zr") as mock_py7zr:
                mock_py7zr.SevenZipFile.return_value = mock_archive
                out = read_file_via_safedir(sd, "archive.7z")
        assert out is not None
        assert "7Z Archive: archive.7z" in out
        mock_py7zr.SevenZipFile.assert_called_once()

    def test_rar_extension_dispatches(self, tmp_path: Path) -> None:
        """``.rar`` routes to ``read_rar_file`` via the dispatcher."""
        from utils.readers import read_file_via_safedir
        from utils.safedir import SafeDir

        (tmp_path / "archive.rar").write_bytes(b"rar placeholder")

        mock_info = MagicMock()
        mock_info.filename = "inside.txt"
        mock_info.file_size = 8
        mock_info.compress_size = 8
        mock_rf = MagicMock()
        mock_rf.__enter__.return_value = mock_rf
        mock_rf.__exit__.return_value = None
        mock_rf.infolist.return_value = [mock_info]
        mock_rf.needs_password.return_value = False

        with SafeDir.open_root(tmp_path) as sd:
            with patch("utils.readers.archives.rarfile") as mock_rarfile:
                mock_rarfile.RarFile.return_value = mock_rf
                mock_rarfile.RarCannotExec = type("RarCannotExec", (Exception,), {})
                out = read_file_via_safedir(sd, "archive.rar")
        assert out is not None
        assert "RAR Archive: archive.rar" in out
        mock_rarfile.RarFile.assert_called_once()

    @pytest.mark.parametrize(
        ("filename", "tar_mode", "expected_compression"),
        [
            ("plain.tar", "w", "None"),
            ("compressed.tar.bz2", "w:bz2", "BZ2"),
            ("compressed.tbz2", "w:bz2", "BZ2"),
            ("compressed.tar.xz", "w:xz", "XZ"),
        ],
    )
    def test_tar_variants_dispatch(
        self,
        tmp_path: Path,
        filename: str,
        tar_mode: str,
        expected_compression: str,
    ) -> None:
        """Plain ``.tar`` and TAR alias extensions (``.tar.bz2``, ``.tbz2``,
        ``.tar.xz``) must all route to ``read_tar_file`` via the dispatcher.

        A dropped or mis-keyed mapping would silently return ``None`` — this
        test asserts content arrives and the correct compression label appears.
        """
        import tarfile as _tf

        from utils.readers import read_file_via_safedir
        from utils.safedir import SafeDir

        archive_path = tmp_path / filename
        payload_path = tmp_path / f"__payload_{filename}.txt"
        payload_path.write_text("tar payload")
        with _tf.open(archive_path, tar_mode) as tf:
            tf.add(payload_path, arcname="readme.txt")
        payload_path.unlink()

        with SafeDir.open_root(tmp_path) as sd:
            out = read_file_via_safedir(sd, filename)

        assert out is not None, f"dispatcher returned None for {filename!r}"
        assert f"TAR Archive: {filename}" in out
        assert f"Compression: {expected_compression}" in out
        assert "readme.txt" in out

    def test_safedir_readers_contains_all_archive_extensions(self) -> None:
        """Verify ``_SAFEDIR_READERS`` contains every archive extension listed
        in the ``read_file`` dispatcher — detects drift between the two
        tables without a round-trip filesystem call.
        """
        from utils.readers import _SAFEDIR_READERS

        # Flatten all keys in _SAFEDIR_READERS into a single set.
        registered: set[str] = set()
        for exts in _SAFEDIR_READERS:
            registered.update(exts)

        required_archive_extensions = {
            ".zip",
            ".7z",
            ".rar",
            ".tar",
            ".tar.gz",
            ".tgz",
            ".tar.bz2",
            ".tbz2",
            ".tar.xz",
        }
        missing = required_archive_extensions - registered
        assert not missing, (
            f"Archive extensions missing from _SAFEDIR_READERS: {sorted(missing)}\n"
            "Add the missing extensions so they don't silently fall back to None."
        )


# ---------------------------------------------------------------------------
# 5.4 — EnhancedEPUBReader SafeDir regression
# ---------------------------------------------------------------------------


def _make_minimal_epub(path: Path) -> None:
    """Build a smallest-possible valid EPUB archive."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            (
                '<?xml version="1.0"?>'
                '<container version="1.0" '
                'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                "<rootfiles>"
                '<rootfile full-path="content.opf" '
                'media-type="application/oebps-package+xml"/>'
                "</rootfiles></container>"
            ),
        )
        zf.writestr(
            "content.opf",
            (
                '<?xml version="1.0"?>'
                '<package version="2.0" xmlns="http://www.idpf.org/2007/opf" '
                'unique-identifier="bookid">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                "<dc:title>Robustness Test Book</dc:title>"
                '<dc:identifier id="bookid">test-326</dc:identifier>'
                "<dc:language>en</dc:language></metadata>"
                '<manifest><item id="ch1" href="ch1.xhtml" '
                'media-type="application/xhtml+xml"/></manifest>'
                '<spine><itemref idref="ch1"/></spine></package>'
            ),
        )
        zf.writestr(
            "ch1.xhtml",
            "<html><body><p>Issue 326 regression chapter</p></body></html>",
        )


@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestEnhancedEPUBReaderSafeDirRegression:
    """Regression tests for ``EnhancedEPUBReader`` SafeDir path (issue #326 finding 5.4).

    ``EnhancedEPUBReader`` was migrated to SafeDir in PR #282.  These tests
    verify that the enhanced reader's public ``read_epub`` entry point refuses
    a symlinked EPUB rather than dereferencing it — the same protection
    verified by the low-level SafeDir EPUB tests but exercised through the
    enhanced reader interface.
    """

    def test_enhanced_reader_refuses_symlinked_epub(self, tmp_path: Path) -> None:
        """``EnhancedEPUBReader.read_epub`` must raise ``SymlinkRejected``
        when the target path is a symlink, not silently read the symlink
        target.  Regression for issue #326 finding 5.4.
        """
        pytest.importorskip("ebooklib")
        pytest.importorskip("bs4")
        from utils.epub_enhanced import EnhancedEPUBReader
        from utils.safedir import SymlinkRejected

        real = tmp_path / "secret.epub"
        _make_minimal_epub(real)
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "decoy.epub").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        reader = EnhancedEPUBReader()
        with pytest.raises(SymlinkRejected):
            reader.read_epub(organize / "decoy.epub")

    def test_enhanced_reader_does_not_call_epub_read_epub_with_symlink(
        self, tmp_path: Path
    ) -> None:
        """Belt-and-suspenders: ``epub.read_epub`` must never be reached when
        the path is a symlink — ``SymlinkRejected`` must fire first.
        """
        pytest.importorskip("ebooklib")
        pytest.importorskip("bs4")
        from utils.epub_enhanced import EnhancedEPUBReader
        from utils.safedir import SymlinkRejected

        real = tmp_path / "real.epub"
        _make_minimal_epub(real)
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "decoy.epub").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        reader = EnhancedEPUBReader()
        with patch("utils.epub_enhanced.epub.read_epub") as spy:
            with pytest.raises(SymlinkRejected):
                reader.read_epub(organize / "decoy.epub")
            # SafeDir must reject before the library is ever invoked.
            assert spy.call_count == 0, (
                "epub.read_epub was called despite symlink — SafeDir guard not active"
            )

    def test_enhanced_reader_happy_path_unaffected(self, tmp_path: Path) -> None:
        """Regression guard must not break the happy path: a real (non-symlinked)
        EPUB file is read and parsed correctly.
        """
        pytest.importorskip("ebooklib")
        pytest.importorskip("bs4")
        from utils.epub_enhanced import EnhancedEPUBReader

        target = tmp_path / "book.epub"
        _make_minimal_epub(target)

        reader = EnhancedEPUBReader()
        content = reader.read_epub(target)
        assert content.metadata.title == "Robustness Test Book"
