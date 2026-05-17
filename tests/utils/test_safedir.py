"""Tests for ``utils.safedir`` — POSIX-safe directory-fd filesystem operations.

Implementation of #266 in the security hardening series (#264).

`SafeDir` holds an open directory file descriptor and exposes operations
that route through `dir_fd=` + `O_NOFOLLOW`. The invariant: every operation
takes a single path *component*, never a path string, so attacker-controlled
segments can't escape the held directory.

Threat model coverage anchored by these tests:

- Symlink swap between enumeration and read — `open_for_reader` /
  `open_child` raise `SymlinkRejected` rather than dereferencing.
- Component-name injection — names containing `/`, `\\`, `..`, or NUL are
  rejected with `ValueError` before any syscall.
- File-descriptor leaks — context-manager exit releases the held fd.
- Cross-directory atomicity — `rename_into` between two `SafeDir`s uses
  `os.rename` with both source and destination `dir_fd`.

Platform: POSIX only. Windows has no equivalent for `O_NOFOLLOW` /
`dir_fd=`; the `SafeDir.open_root` factory raises `NotImplementedError`
there (see #264 for the deferral).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from utils.safedir import SafeDir, SymlinkRejected

pytestmark = [
    pytest.mark.ci,
    pytest.mark.unit,
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only primitive"),
]


# ---------------------------------------------------------------------------
# Component-name validation
# ---------------------------------------------------------------------------


class TestNameValidation:
    """Every method that takes a *name* must reject path-component injection."""

    @pytest.fixture
    def sd(self, tmp_path: Path):
        with SafeDir.open_root(tmp_path) as sd:
            yield sd

    @pytest.mark.parametrize(
        "bad_name",
        [
            "a/b",
            "../escape",
            "..",
            ".",
            "",
            "a\x00b",
            "foo\\bar",
            "/abs",
        ],
    )
    def test_open_child_rejects_bad_name(self, sd: SafeDir, bad_name: str) -> None:
        with pytest.raises(ValueError):
            sd.open_child(bad_name)

    @pytest.mark.parametrize("bad_name", ["a/b", "..", "", "x\x00y"])
    def test_open_for_reader_rejects_bad_name(self, sd: SafeDir, bad_name: str) -> None:
        with pytest.raises(ValueError):
            sd.open_for_reader(bad_name)

    @pytest.mark.parametrize("bad_name", ["a/b", "..", "."])
    def test_open_subdir_rejects_bad_name(self, sd: SafeDir, bad_name: str) -> None:
        with pytest.raises(ValueError):
            sd.open_subdir(bad_name)

    @pytest.mark.parametrize("bad_name", ["a/b", ".."])
    def test_lstat_rejects_bad_name(self, sd: SafeDir, bad_name: str) -> None:
        with pytest.raises(ValueError):
            sd.lstat(bad_name)

    @pytest.mark.parametrize("bad_name", ["a/b", "..", ""])
    def test_unlink_rejects_bad_name(self, sd: SafeDir, bad_name: str) -> None:
        with pytest.raises(ValueError):
            sd.unlink(bad_name)

    @pytest.mark.parametrize("bad_name", ["a/b", "..", ""])
    def test_mkdir_rejects_bad_name(self, sd: SafeDir, bad_name: str) -> None:
        with pytest.raises(ValueError):
            sd.mkdir(bad_name)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_open_root_and_close_on_context_exit(self, tmp_path: Path) -> None:
        """Context exit closes the held fd."""
        sd = SafeDir.open_root(tmp_path)
        fd = sd.fd
        sd.__exit__(None, None, None)
        with pytest.raises(OSError):
            # Closed fd — fstat raises EBADF.
            os.fstat(fd)

    def test_open_for_reader_returns_readable_fd(self, tmp_path: Path) -> None:
        (tmp_path / "data.txt").write_bytes(b"hello")
        with SafeDir.open_root(tmp_path) as sd:
            fd = sd.open_for_reader("data.txt")
            try:
                assert os.read(fd, 5) == b"hello"
            finally:
                os.close(fd)

    def test_open_for_reader_via_fdopen(self, tmp_path: Path) -> None:
        """Confirms the canonical 'pass to library reader' pattern works."""
        (tmp_path / "data.txt").write_bytes(b"payload")
        with SafeDir.open_root(tmp_path) as sd:
            fd = sd.open_for_reader("data.txt")
            with os.fdopen(fd, "rb") as f:
                assert f.read() == b"payload"

    def test_scandir_yields_entries(self, tmp_path: Path) -> None:
        (tmp_path / "a").write_text("x")
        (tmp_path / "b").write_text("y")
        with SafeDir.open_root(tmp_path) as sd:
            names = sorted(entry.name for entry in sd.scandir())
        assert names == ["a", "b"]

    def test_lstat_does_not_follow_symlink(self, tmp_path: Path) -> None:
        (tmp_path / "target").write_text("target content")
        try:
            (tmp_path / "link").symlink_to(tmp_path / "target")
        except OSError:
            pytest.skip("symlink creation not supported")
        with SafeDir.open_root(tmp_path) as sd:
            st = sd.lstat("link")
        import stat as _stat

        assert _stat.S_ISLNK(st.st_mode), "lstat should report the symlink, not the target"

    def test_unlink_removes_via_dir_fd(self, tmp_path: Path) -> None:
        target = tmp_path / "doomed"
        target.write_text("x")
        with SafeDir.open_root(tmp_path) as sd:
            sd.unlink("doomed")
        assert not target.exists()

    def test_mkdir_creates_subdir(self, tmp_path: Path) -> None:
        with SafeDir.open_root(tmp_path) as sd:
            sd.mkdir("new_category")
        assert (tmp_path / "new_category").is_dir()

    def test_open_subdir_returns_new_safedir(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "leaf.txt").write_bytes(b"leaf content")
        with SafeDir.open_root(tmp_path) as root, root.open_subdir("sub") as sub:
            fd = sub.open_for_reader("leaf.txt")
            try:
                assert os.read(fd, 12) == b"leaf content"
            finally:
                os.close(fd)

    def test_rename_into_same_dir(self, tmp_path: Path) -> None:
        (tmp_path / "old").write_text("data")
        with SafeDir.open_root(tmp_path) as sd:
            sd.rename_into("old", sd, "new")
        assert not (tmp_path / "old").exists()
        assert (tmp_path / "new").read_text() == "data"

    def test_rename_into_cross_dir(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        (src_dir / "doc.txt").write_text("moved")
        with (
            SafeDir.open_root(src_dir) as src,
            SafeDir.open_root(dst_dir) as dst,
        ):
            src.rename_into("doc.txt", dst, "doc.txt")
        assert not (src_dir / "doc.txt").exists()
        assert (dst_dir / "doc.txt").read_text() == "moved"


# ---------------------------------------------------------------------------
# Symlink rejection — the headline security guarantee
# ---------------------------------------------------------------------------


class TestSymlinkRejection:
    """`O_NOFOLLOW` on every open. A swapped symlink raises `SymlinkRejected`."""

    def test_open_for_reader_rejects_symlinked_file(self, tmp_path: Path) -> None:
        honey = tmp_path / "honey"
        honey.write_bytes(b"do_not_exfiltrate")
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "report.pdf").symlink_to(honey)
        except OSError:
            pytest.skip("symlink creation not supported")
        with SafeDir.open_root(organize) as sd:
            with pytest.raises(SymlinkRejected):
                sd.open_for_reader("report.pdf")

    def test_open_child_rejects_symlinked_file(self, tmp_path: Path) -> None:
        (tmp_path / "honey").write_bytes(b"do_not_exfiltrate")
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "link").symlink_to(tmp_path / "honey")
        except OSError:
            pytest.skip("symlink creation not supported")
        with SafeDir.open_root(organize) as sd:
            with pytest.raises(SymlinkRejected):
                sd.open_child("link")

    def test_open_subdir_rejects_symlinked_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "honey_dir"
        target.mkdir()
        (target / "secret.txt").write_text("oops")
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "documents").symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not supported")
        with SafeDir.open_root(organize) as sd:
            with pytest.raises(SymlinkRejected):
                sd.open_subdir("documents")

    def test_open_root_rejects_symlinked_root(self, tmp_path: Path) -> None:
        """If the root passed to ``open_root`` is itself a symlink, refuse."""
        real_root = tmp_path / "real"
        real_root.mkdir()
        try:
            link = tmp_path / "link"
            link.symlink_to(real_root, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not supported")
        with pytest.raises(SymlinkRejected):
            SafeDir.open_root(link)

    def test_symlink_rejected_is_oserror(self) -> None:
        """``SymlinkRejected`` subclasses ``OSError`` so existing handlers work."""
        assert issubclass(SymlinkRejected, OSError)


# ---------------------------------------------------------------------------
# Resource lifetime
# ---------------------------------------------------------------------------


class TestResourceLifetime:
    """Verify the held fd is released on context-manager exit and on subdir
    chains.

    `T13` from `test-generation-patterns.md` — using `tmp_path` everywhere
    rather than hardcoded paths.
    """

    def _count_fds(self) -> int | None:
        """Return the number of open fds for this process, or None on platforms
        where ``/proc/self/fd`` is unavailable (e.g. macOS).
        """
        proc = Path("/proc/self/fd")
        if not proc.is_dir():
            return None
        return len(list(proc.iterdir()))

    def test_context_manager_releases_root_fd(self, tmp_path: Path) -> None:
        before = self._count_fds()
        if before is None:
            pytest.skip("/proc/self/fd not available on this platform")
        for _ in range(20):
            with SafeDir.open_root(tmp_path):
                pass
        after = self._count_fds()
        assert after is not None
        # Allow a small slack for any incidental fd churn (logging, gc).
        assert after - before <= 2, f"fd leak: before={before} after={after}"

    def test_context_manager_releases_subdir_fd(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        before = self._count_fds()
        if before is None:
            pytest.skip("/proc/self/fd not available on this platform")
        with SafeDir.open_root(tmp_path) as root:
            for _ in range(20):
                with root.open_subdir("sub"):
                    pass
        after = self._count_fds()
        assert after is not None
        assert after - before <= 2, f"fd leak: before={before} after={after}"

    def test_close_does_not_doublefree(self, tmp_path: Path) -> None:
        """Exiting the context manager twice is harmless."""
        sd = SafeDir.open_root(tmp_path)
        sd.__exit__(None, None, None)
        # Calling exit again must NOT raise (it could happen if the user
        # writes their own try/finally + with-statement).
        sd.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Error semantics: missing entries
# ---------------------------------------------------------------------------


class TestMissingEntries:
    def test_open_for_reader_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        with SafeDir.open_root(tmp_path) as sd:
            with pytest.raises(FileNotFoundError):
                sd.open_for_reader("nope.txt")

    def test_open_subdir_missing_raises_filenotfound(self, tmp_path: Path) -> None:
        with SafeDir.open_root(tmp_path) as sd:
            with pytest.raises(FileNotFoundError):
                sd.open_subdir("nope")

    def test_unlink_missing_raises_filenotfound(self, tmp_path: Path) -> None:
        with SafeDir.open_root(tmp_path) as sd:
            with pytest.raises(FileNotFoundError):
                sd.unlink("nope")

    def test_lstat_missing_raises_filenotfound(self, tmp_path: Path) -> None:
        with SafeDir.open_root(tmp_path) as sd:
            with pytest.raises(FileNotFoundError):
                sd.lstat("nope")


# ---------------------------------------------------------------------------
# T10 predicate-negative cases for the symlink rejection
# ---------------------------------------------------------------------------


class TestPredicateNegatives:
    """`test-generation-patterns.md` T10: every predicate needs a negative
    case with the same surface shape but wrong context.

    The predicate here is "is this entry a symlink that we should refuse?"
    The wrong-context cases below all look like potentially-dangerous
    operations but are in fact safe and must succeed.
    """

    def test_regular_file_named_like_symlink_not_rejected(self, tmp_path: Path) -> None:
        """A regular file whose name is "link" or "shortcut" is fine."""
        (tmp_path / "link").write_bytes(b"actually a regular file")
        with SafeDir.open_root(tmp_path) as sd:
            fd = sd.open_for_reader("link")
            try:
                assert os.read(fd, 64) == b"actually a regular file"
            finally:
                os.close(fd)

    def test_regular_directory_named_like_symlink_not_rejected(self, tmp_path: Path) -> None:
        """A regular directory at a name that *looks* like a link is fine."""
        (tmp_path / "documents_link").mkdir()
        (tmp_path / "documents_link" / "x").write_text("x")
        with SafeDir.open_root(tmp_path) as sd, sd.open_subdir("documents_link") as sub:
            assert {e.name for e in sub.scandir()} == {"x"}

    def test_root_at_real_path_not_rejected(self, tmp_path: Path) -> None:
        """`open_root` on a real directory (whatever the parent has done) is fine.

        Same surface shape as `test_open_root_rejects_symlinked_root` — a
        directory at the supplied path. Wrong context: the directory is real.
        """
        real = tmp_path / "organize"
        real.mkdir()
        with SafeDir.open_root(real) as sd:
            # Verify the fd actually points at the right inode.
            assert os.fstat(sd.fd).st_ino == real.stat().st_ino
