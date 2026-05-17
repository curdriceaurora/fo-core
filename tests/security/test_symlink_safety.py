"""Symlink / TOCTOU regression tests — security hardening (tracking: #264).

This module is the *anchor* for the security hardening series. Every subsequent
PR un-skips one of the tests below:

- ``test_safe_walk_skips_file_symlink``           — passes today (PR1)
- ``test_safe_walk_skips_directory_symlink``      — passes today (PR1)
- ``test_dedupe_refuses_unlink_on_inode_swap``    — blocked on #268 (PR4)
- ``test_organize_destination_symlink_swap``      — blocked on #270 (PR6)
- ``test_daemon_skips_symlink_created_post_start``— blocked on #270 (PR6)
- ``test_undo_refuses_replay_on_inode_change``    — blocked on #269 (PR5)
- ``test_reader_does_not_open_symlink_target``    — blocked on #267 (PR3)

The threat model:

1. **LLM exfiltration**: A symlink dropped into the organize root targets a
   sensitive file outside it (``~/.ssh/id_rsa``, browser cookies, etc.). A
   content reader called by ``fo organize`` follows the link, the secret leaves
   the host through the inference path.
2. **Destination symlink swap**: A category subdir is replaced with a symlink
   to an attacker-controlled location between mkdir and rename.
3. **Dedupe TOCTOU**: A victim file's inode is swapped for a symlink to
   important data between the hash and the unlink.
4. **Undo replay TOCTOU**: The file at the original location at undo time is
   a different file than the one that was moved.

Tests are POSIX-only. Windows doesn't have ``O_NOFOLLOW`` / ``dir_fd=`` and
the SafeDir primitive isn't implemented for it (see #264 → #266).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.path_guard import safe_walk

pytestmark = [
    pytest.mark.ci,
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HONEY_CONTENT = b"do_not_exfiltrate"


def _make_honey(tmp_path: Path) -> Path:
    """Create a sensitive file outside the organize root and return its path."""
    honey_dir = tmp_path / "honey"
    honey_dir.mkdir()
    honey_file = honey_dir / "SECRET"
    honey_file.write_bytes(_HONEY_CONTENT)
    return honey_file


def _make_organize_root(tmp_path: Path) -> Path:
    organize = tmp_path / "organize"
    organize.mkdir()
    return organize


# ---------------------------------------------------------------------------
# Tests that pass against current `main` (safe_walk filters symlinks)
# ---------------------------------------------------------------------------


class TestSafeWalkSymlinkFiltering:
    """Anchor the existing ``safe_walk`` guarantees as security regressions.

    These pass today. They exist so any future "convenience" PR that flips
    ``safe_walk`` to follow symlinks fails CI loudly.
    """

    def test_safe_walk_skips_file_symlink(self, tmp_path: Path) -> None:
        """A symlink to a file outside the root is never yielded."""
        honey = _make_honey(tmp_path)
        organize = _make_organize_root(tmp_path)

        legit = organize / "legit.txt"
        legit.write_text("normal content")

        # Planted symlink with a benign-looking name.
        link = organize / "report.pdf"
        try:
            link.symlink_to(honey)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        yielded = list(safe_walk(organize))

        assert legit in yielded
        assert link not in yielded
        # Belt-and-braces: nothing under the honey directory leaks in.
        for path in yielded:
            assert "honey" not in path.parts, f"safe_walk leaked: {path}"

    def test_safe_walk_skips_directory_symlink(self, tmp_path: Path) -> None:
        """A directory symlink is not descended into."""
        honey_dir = tmp_path / "honey"
        honey_dir.mkdir()
        (honey_dir / "secrets.txt").write_bytes(_HONEY_CONTENT)
        (honey_dir / "more_secrets.txt").write_bytes(_HONEY_CONTENT)

        organize = _make_organize_root(tmp_path)
        (organize / "legit.txt").write_text("normal content")

        link_dir = organize / "documents"
        try:
            link_dir.symlink_to(honey_dir, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        yielded = list(safe_walk(organize))

        # No yielded entry should live under the honey tree, regardless of how
        # it resolves. Use lexical parts (safe_walk yields lexical paths) and
        # then double-check via resolve() — neither path nor target may leak.
        for path in yielded:
            assert "honey" not in path.parts, f"lexical leak: {path}"
            resolved = path.resolve()
            assert "honey" not in resolved.parts, f"resolved leak: {path} -> {resolved}"

    def test_safe_walk_skips_symlink_to_self(self, tmp_path: Path) -> None:
        """A self-referential symlink doesn't crash the walker or leak its name.

        Defensive — ``safe_walk`` calls ``entry.is_symlink()`` first so it
        should never resolve a cycle, but lock the behavior in.
        """
        organize = _make_organize_root(tmp_path)
        (organize / "legit.txt").write_text("normal content")

        loop = organize / "loop"
        try:
            loop.symlink_to(loop)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        yielded = list(safe_walk(organize))

        assert loop not in yielded


# ---------------------------------------------------------------------------
# Tests blocked on subsequent PRs in the hardening series
# ---------------------------------------------------------------------------


class TestReadSideSymlinkSafety:
    """Tests for the LLM-exfiltration vector (blocked on PR3, #267)."""

    def test_reader_does_not_open_symlink_target(self, tmp_path: Path) -> None:
        """A swapped symlink between enumeration and read is rejected.

        Sequence the test will exercise once SafeDir is wired in:

        1. ``safe_walk`` yields ``organize/report.pdf`` as a regular file.
        2. Between yield and read, the file is replaced by a symlink to the
           honey file (simulated via patched ``open`` callback or a sleep +
           subprocess swap).
        3. ``SafeDir.open_for_reader`` uses ``O_NOFOLLOW`` and raises
           ``SymlinkRejected``. The content reader is never called.

        Acceptance: a recorder mock attached to the LLM processor never
        receives ``_HONEY_CONTENT``.
        """
        pytest.skip("blocked on PR3 (#267) — SafeDir read-side migration")

    def test_organize_large_symlink_target_not_opened(self, tmp_path: Path) -> None:
        """A 200MB out-of-tree symlink target is not read.

        Today ``collect_files`` (legacy ``os.walk``) yields the symlink and
        the content reader opens it. After PR3+PR6 this path goes through
        SafeDir and the read is refused. The test measures wall-clock + I/O
        — if the reader actually opens 200MB the test will trip a timeout
        guard. Once green, it becomes the canary for "fo organize doesn't
        accidentally pull arbitrary files into memory via a planted link".
        """
        pytest.skip("blocked on PR3 (#267) and PR6 (#270) — legacy collect_files")


class TestDedupeSymlinkSafety:
    """TOCTOU between hash and unlink (blocked on PR4, #268)."""

    def test_dedupe_refuses_unlink_on_inode_swap(self, tmp_path: Path) -> None:
        """Dedupe refuses to unlink if (dev, ino, size) changed since hash.

        Sequence the test will exercise once inode-pinning lands:

        1. Two identical files A and B; A is the victim.
        2. ``compute_hash(A)`` records ``HashResult(digest, dev, ino, size)``.
        3. Between hash and unlink, A is replaced by a symlink to the honey
           file (simulated by manipulating the path between scan and resolve
           phases).
        4. Dedupe re-lstats and detects the mismatch, refuses the unlink,
           logs a ``security_event``.

        Acceptance: honey file still exists, anomaly is logged, exit code
        reflects the partial-skip.
        """
        pytest.skip("blocked on PR4 (#268) — inode pinning in dedupe")


class TestDestinationSymlinkSafety:
    """Category-dir-replaced-with-symlink (blocked on PR6, #270)."""

    def test_organize_destination_symlink_swap(self, tmp_path: Path) -> None:
        """A category dir replaced by a symlink between mkdir and rename is rejected.

        Sequence the test will exercise once SafeDir-based moves land:

        1. ``fo organize`` plans to move ``input/doc.txt`` to
           ``output/category_X/doc.txt``.
        2. After ``mkdir`` but before ``rename``, ``output/category_X`` is
           replaced by a symlink to an attacker-controlled directory outside
           the organize root.
        3. The SafeDir-based ``os.rename(src_name, dst_name, dst_dir_fd=...)``
           opens the dst dir with ``O_NOFOLLOW`` and fails; the file is not
           written to the attacker location.

        Acceptance: the attacker directory is empty; the operation either
        fails or lands inside the real output root.
        """
        pytest.skip("blocked on PR6 (#270) — destination SafeDir + dir_fd=")


class TestDaemonSymlinkSafety:
    """Watcher event → open gap (blocked on PR6, #270)."""

    def test_daemon_skips_symlink_created_post_start(self, tmp_path: Path) -> None:
        """A symlink created inside the watch root after daemon start is skipped.

        Sequence the test will exercise once the daemon routes through
        SafeDir:

        1. Daemon starts watching ``organize/``.
        2. ``organize/report.pdf`` is created as a symlink to the honey file.
        3. The watcher event handler routes through
           ``safe_dir.open_child('report.pdf')`` which raises
           ``SymlinkRejected``.
        4. Event is dropped; no content reader is invoked.

        Acceptance: the LLM processor recorder never sees ``_HONEY_CONTENT``;
        a ``security_event`` is logged.
        """
        pytest.skip("blocked on PR6 (#270) — watcher SafeDir routing")


class TestUndoSymlinkSafety:
    """Replay TOCTOU (blocked on PR5, #269)."""

    def test_undo_refuses_replay_on_inode_change(self, tmp_path: Path) -> None:
        """Undo refuses to overwrite a destination whose (dev, ino) changed.

        Sequence the test will exercise once history records (dev, ino):

        1. ``fo organize`` moves ``input/A`` to ``output/cat/A``; history
           records ``dest_dev`` / ``dest_ino`` from ``os.fstat`` on the open
           fd.
        2. ``output/cat/A`` is deleted and replaced by a different file with
           the same name (different inode).
        3. ``fo undo`` re-stats the destination; the recorded
           ``(dest_dev, dest_ino)`` doesn't match.
        4. Undo refuses, logs a ``security_event``.

        Acceptance: the replacement file at ``output/cat/A`` is untouched
        and ``input/A`` is not re-created.
        """
        pytest.skip("blocked on PR5 (#269) — history (dev, ino) + verify on undo")
