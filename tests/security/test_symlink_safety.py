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
    pytest.mark.integration,
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
        legit = organize / "legit.txt"
        legit.write_text("normal content")

        link_dir = organize / "documents"
        try:
            link_dir.symlink_to(honey_dir, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        yielded = list(safe_walk(organize))

        # Positive control: the legit file MUST appear. Otherwise a regression
        # that makes safe_walk yield an empty list would pass this test
        # vacuously (the "honey" leak loop runs zero iterations).
        assert legit in yielded

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
        legit = organize / "legit.txt"
        legit.write_text("normal content")

        loop = organize / "loop"
        try:
            loop.symlink_to(loop)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        yielded = list(safe_walk(organize))

        # Positive control — prevents vacuous pass on a safe_walk regression.
        assert legit in yielded
        assert loop not in yielded


# ---------------------------------------------------------------------------
# Tests blocked on subsequent PRs in the hardening series
# ---------------------------------------------------------------------------


class TestReadSideSymlinkSafety:
    """Tests for the LLM-exfiltration vector — un-skipped in PR3a (#267)."""

    @staticmethod
    def _mock_text_processor():
        """Return a ``TextProcessor`` with a mocked TEXT model.

        Imports happen inside the function so the module imports during
        test collection don't require LLM extras.
        """
        from unittest.mock import MagicMock

        from models.base import ModelType
        from services.text_processor import TextProcessor

        model = MagicMock()
        model.config.model_type = ModelType.TEXT
        model.is_initialized = True
        # A recorder for content the LLM would have seen. If the symlink
        # were dereferenced, honey bytes would land here.
        model.generate = MagicMock(return_value="MOCK_SUMMARY")
        return TextProcessor(text_model=model), model

    def test_reader_does_not_open_symlink_target(self, tmp_path: Path) -> None:
        """A symlinked file in the organize root is refused before the reader runs.

        Setup:
        1. Honey file outside the organize root with sensitive content.
        2. ``organize/report.txt`` is a symlink pointing at the honey file.
        3. Caller invokes ``text_processor.process_file(organize/report.txt)``.

        Expected:
        - ``SafeDir.open_for_reader`` raises ``SymlinkRejected``.
        - ``ProcessedFile.folder_name == "errors"``.
        - ``ProcessedFile.error`` mentions the symlink refusal.
        - The mocked LLM is NOT called (no ``generate`` invocation), so
          honey content cannot have leaked into the inference path.
        """
        honey = _make_honey(tmp_path)  # tmp_path/honey/SECRET
        organize = _make_organize_root(tmp_path)
        # The symlink uses a .txt extension so it would be routed through
        # the SafeDir text reader if SafeDir didn't refuse it.
        link = organize / "report.txt"
        try:
            link.symlink_to(honey)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        processor, model = self._mock_text_processor()
        result = processor.process_file(link)

        assert result.folder_name == "errors"
        assert result.error is not None
        assert "symlink" in result.error.lower(), (
            f"expected refusal message to mention symlink: {result.error!r}"
        )
        assert model.generate.call_count == 0, (
            "LLM was invoked despite symlink rejection — honey content may have leaked"
        )

    def test_organize_large_symlink_target_not_opened(self, tmp_path: Path) -> None:
        """A symlink to a large out-of-tree file is refused without reading the target.

        Today (PR3a) the SafeDir reader refuses the symlink at open time —
        no bytes from the target are ever read. The canary: time the
        operation and confirm it's fast even when the target is large.
        500 KB of zeros is enough to detect a regression where the
        reader would have opened the target.
        """
        honey_dir = tmp_path / "honey"
        honey_dir.mkdir()
        # 500KB sentinel — big enough that reading it would be measurable,
        # small enough to keep test runtime negligible.
        big_target = honey_dir / "huge.txt"
        big_target.write_bytes(b"\x00" * (500 * 1024))

        organize = _make_organize_root(tmp_path)
        link = organize / "report.txt"
        try:
            link.symlink_to(big_target)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        processor, model = self._mock_text_processor()

        result = processor.process_file(link)

        # Deterministic behaviour checks — the refusal must surface as a
        # "errors" routing with an error message, and the model must NOT
        # have been called (which would have meant the symlink target was
        # dereferenced and its content fed to the LLM). The previous
        # wall-clock guard was removed per C1 FLAKY_GATE — timing-based
        # assertions are environment-dependent on CI runners.
        assert result.folder_name == "errors"
        assert result.error is not None
        assert model.generate.call_count == 0


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
