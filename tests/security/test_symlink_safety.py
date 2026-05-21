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

import logging
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
        """Dedupe refuses to unlink if the victim was swapped for a symlink.

        Sequence:

        1. Two identical files A (victim) and B (keeper) in the scan root.
        2. Attacker replaces A with a symlink to a honey file outside the root
           (simulates swap between scan phase and resolve phase).
        3. ``_dedupe_unlink`` opens A with O_NOFOLLOW via SafeDir;
           ``SymlinkRejected`` is raised and caught — unlink is refused.
        4. Honey file must be intact; a ``security_event`` must be logged.

        Acceptance: honey file still exists, security event is logged.
        """
        import logging

        from cli.dedupe_v2 import _dedupe_unlink

        scan_root = tmp_path / "organize"
        scan_root.mkdir()
        honey_dir = tmp_path / "honey"
        honey_dir.mkdir()

        honey = honey_dir / "secret.txt"
        honey.write_text("sensitive data")
        honey_content = honey.read_text()

        victim = scan_root / "duplicate.txt"
        victim.write_text("identical content")
        keeper = scan_root / "keeper.txt"
        keeper.write_text("identical content")

        try:
            victim.unlink()
            victim.symlink_to(honey)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        class _FakeRenderer:
            def __init__(self) -> None:
                self.actions: list[tuple[str, ...]] = []

            def render_resolve_action(self, action: str, path: Path, **_: object) -> None:
                self.actions.append((action, str(path)))

        renderer = _FakeRenderer()
        security_events: list[str] = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                msg = self.format(record)
                if "security_event" in msg:
                    security_events.append(msg)

        handler = _CapturingHandler()
        logging.getLogger("cli.dedupe_v2").addHandler(handler)
        try:
            result = _dedupe_unlink(victim, renderer)
        finally:
            logging.getLogger("cli.dedupe_v2").removeHandler(handler)

        assert result is False, "unlink should have been refused"
        assert honey.exists(), "honey file must not be deleted"
        assert honey.read_text() == honey_content, "honey content must be intact"
        assert any("security_event" in e for e in security_events), (
            f"no security_event logged; actions={renderer.actions}"
        )

    def test_dedupe_unlink_success(self, tmp_path: Path) -> None:
        """_dedupe_unlink removes a regular file and returns True."""
        from cli.dedupe_v2 import _dedupe_unlink

        victim = tmp_path / "duplicate.txt"
        victim.write_text("content")

        class _FakeRenderer:
            def __init__(self) -> None:
                self.actions: list[tuple[str, ...]] = []

            def render_resolve_action(self, action: str, path: Path, **_: object) -> None:
                self.actions.append((action, str(path)))

        renderer = _FakeRenderer()
        result = _dedupe_unlink(victim, renderer)

        assert result is True
        assert not victim.exists()
        assert any(a[0] == "removed" for a in renderer.actions)

    def test_dedupe_unlink_oserror_handled(self, tmp_path: Path) -> None:
        """_dedupe_unlink returns False and logs on OSError during SafeDir unlink."""
        from unittest.mock import patch

        from cli.dedupe_v2 import _dedupe_unlink

        victim = tmp_path / "victim.txt"
        victim.write_text("content")

        class _FakeRenderer:
            def __init__(self) -> None:
                self.actions: list[tuple[str, ...]] = []

            def render_resolve_action(self, action: str, path: Path, **_: object) -> None:
                self.actions.append((action,))

        renderer = _FakeRenderer()
        with patch("utils.safedir.SafeDir.unlink", side_effect=OSError("busy")):
            result = _dedupe_unlink(victim, renderer)

        assert result is False
        assert any(a[0] == "error" for a in renderer.actions)

    def test_dedupe_unlink_inode_mismatch_detected(self, tmp_path: Path) -> None:
        """_dedupe_unlink refuses if lstat triple differs from pin_inode triple."""
        import logging
        from unittest.mock import patch

        from cli.dedupe_v2 import _dedupe_unlink
        from services.deduplication.hasher import InodePin

        victim = tmp_path / "victim.txt"
        victim.write_text("content")

        # Fake pin with mismatched ino so the comparison always fails.
        fake_pin = InodePin(dev=0, ino=0, size=0)

        class _FakeRenderer:
            def __init__(self) -> None:
                self.actions: list[tuple[str, ...]] = []

            def render_resolve_action(self, action: str, path: Path, **_: object) -> None:
                self.actions.append((action, str(path)))

        renderer = _FakeRenderer()
        security_events: list[str] = []

        class _Cap(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if "security_event" in self.format(record):
                    security_events.append(self.format(record))

        handler = _Cap()
        logging.getLogger("cli.dedupe_v2").addHandler(handler)
        try:
            with patch(
                "services.deduplication.hasher.FileHasher.pin_inode",
                return_value=fake_pin,
            ):
                result = _dedupe_unlink(victim, renderer)
        finally:
            logging.getLogger("cli.dedupe_v2").removeHandler(handler)

        assert result is False
        assert victim.exists(), "file must not be deleted on mismatch"
        assert any("inode_swap" in e for e in security_events)


class TestDestinationSymlinkSafety:
    """Category-dir-replaced-with-symlink — PR6 (#270)."""

    def test_organize_destination_symlink_swap(self, tmp_path: Path) -> None:
        """PostprocessorStage refuses when the category dir is a symlink.

        Sequence:

        1. ``PostprocessorStage`` is initialised with an output root.
        2. After the output root is created, the category subdir is
           replaced by a symlink pointing outside the root (attacker).
        3. ``PostprocessorStage.process()`` tries ``safe_dir.mkdir(category)``
           which raises ``SymlinkRejected`` because ``O_NOFOLLOW`` sees the
           symlink.
        4. The stage sets ``context.error`` — the file is NOT written to
           the attacker location.

        Acceptance: attacker directory remains empty; ``context.failed`` is
        True; a ``security_event destination_symlink_swap`` is logged.
        """
        import logging

        from interfaces.pipeline import StageContext
        from pipeline.stages.postprocessor import PostprocessorStage

        output_root = tmp_path / "output"
        output_root.mkdir()
        attacker_dir = tmp_path / "attacker"
        attacker_dir.mkdir()

        # Pre-create the category subdir as a symlink to the attacker dir.
        category_link = output_root / "documents"
        try:
            category_link.symlink_to(attacker_dir, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        src = tmp_path / "doc.txt"
        src.write_bytes(b"sensitive content")

        security_events: list[str] = []

        class _CapHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                msg = self.format(record)
                if "security_event" in msg:
                    security_events.append(msg)

        cap = _CapHandler()
        postprocessor_log = logging.getLogger("pipeline.stages.postprocessor")
        postprocessor_log.addHandler(cap)
        try:
            stage = PostprocessorStage(output_root)
            try:
                ctx = StageContext(file_path=src, dry_run=False)
                ctx.category = "documents"
                ctx.filename = "doc"
                result = stage.process(ctx)
            finally:
                stage.close()
        finally:
            postprocessor_log.removeHandler(cap)

        assert result.failed, "stage must fail when category dir is a symlink"
        assert not any(attacker_dir.iterdir()), "attacker dir must remain empty"
        assert result.error is not None, "context.error must be set"
        assert any("destination_symlink_swap" in e for e in security_events), (
            "security_event destination_symlink_swap must be logged; got: " + str(security_events)
        )


class TestDaemonSymlinkSafety:
    """Watcher event → open gap — PR6 (#270)."""

    def test_daemon_skips_symlink_created_post_start(self, tmp_path: Path) -> None:
        """FileEventHandler drops symlink events when SafeDir is injected.

        Sequence:

        1. Watch root opened as a ``SafeDir`` and injected into
           ``FileEventHandler``.
        2. A symlink named ``report.pdf`` is created inside the root
           pointing at a honey file outside it.
        3. A synthetic CREATE event for ``report.pdf`` is injected into
           ``_handle_event``.
        4. ``_safedir_allows`` opens the entry with ``O_NOFOLLOW`` via
           the SafeDir, gets ``SymlinkRejected``, logs
           ``security_event watcher_symlink_rejected``, and returns False.
        5. The event is NOT enqueued.

        Acceptance: ``queue`` remains empty; a ``security_event`` log is
        emitted.
        """

        from watchdog.events import FileCreatedEvent

        from utils.safedir import SafeDir
        from watcher.config import WatcherConfig
        from watcher.handler import FileEventHandler
        from watcher.queue import EventQueue, EventType

        honey = _make_honey(tmp_path)
        watch_root = tmp_path / "watch"
        watch_root.mkdir()

        link = watch_root / "report.pdf"
        try:
            link.symlink_to(honey)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        queue = EventQueue()
        config = WatcherConfig(watch_directories=[watch_root], debounce_seconds=0.0)

        security_events: list[str] = []

        class _Cap(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if "security_event" in record.getMessage():
                    security_events.append(record.getMessage())

        cap = _Cap()
        log = logging.getLogger("watcher.handler")
        log.addHandler(cap)
        try:
            with SafeDir.open_root(watch_root) as sd:
                handler = FileEventHandler(config, queue, safe_dir=sd)
                event = FileCreatedEvent(str(link))
                handler._handle_event(event, EventType.CREATED)
        finally:
            log.removeHandler(cap)

        assert queue.size == 0, "symlink event must not reach the queue"
        assert any("watcher_symlink_rejected" in e for e in security_events), (
            "security_event watcher_symlink_rejected must be logged"
        )


class TestUndoSymlinkSafety:
    """Replay TOCTOU — PR5c (#269)."""

    def test_undo_refuses_replay_on_inode_change(self, tmp_path: Path) -> None:
        """Undo refuses to overwrite a destination whose (dev, ino) changed.

        Sequence:

        1. Create ``output/cat/A`` and build a history record that pins its
           ``(dev, ino)`` via PR5a accessors.
        2. Delete ``output/cat/A`` and replace it with a new file (different
           inode) at the same path.
        3. Call ``RollbackExecutor.rollback_move`` — the inode check fires,
           sees a mismatch, logs ``security_event undo_inode_mismatch``, and
           returns ``False``.

        Acceptance:
        - ``rollback_move`` returns ``False``
        - The replacement file at ``output/cat/A`` is untouched
        - ``input/A`` is NOT re-created
        - A log record containing ``security_event undo_inode_mismatch`` is emitted
        """
        from datetime import UTC, datetime

        from history.models import Operation, OperationStatus, OperationType
        from undo.rollback import RollbackExecutor
        from undo.validator import OperationValidator

        # Set up paths
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output" / "cat"
        input_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)

        source = input_dir / "A"
        destination = output_dir / "A"

        # Step 1: Simulate an original organize move — destination exists.
        destination.write_bytes(b"original content")
        st = destination.stat()

        # Build a history record with the original inode pinned.
        op = Operation(
            operation_type=OperationType.MOVE,
            timestamp=datetime.now(UTC),
            source_path=source,
            destination_path=destination,
            status=OperationStatus.COMPLETED,
            metadata={
                "dest_dev": st.st_dev,
                "dest_ino": st.st_ino,
            },
        )
        assert op.dest_dev == st.st_dev
        assert op.dest_ino == st.st_ino

        # Step 2: Attacker replaces destination with a different file.
        # Use rename rather than unlink+write so the replacement file gets a
        # fresh inode even on Linux where deleted inodes are immediately reused.
        replacement = output_dir / "A.replacement"
        replacement.write_bytes(b"attacker content")
        replacement_ino = replacement.stat().st_ino
        replacement.rename(destination)
        new_st = destination.stat()
        assert new_st.st_ino == replacement_ino, "sanity: rename must preserve inode"
        if new_st.st_ino == st.st_ino:
            pytest.skip("filesystem reuses inodes immediately; inode-change test is vacuous")

        # Step 3: Attempt undo — rollback_move should refuse.
        journal = tmp_path / "test.journal"
        validator = OperationValidator(journal_path=journal)
        executor = RollbackExecutor(validator=validator, journal_path=journal)

        with self._capture_security_log() as records:
            result = executor.rollback_move(op)

        # Acceptance checks
        assert result is False, "rollback_move must refuse when inode changed"
        assert destination.read_bytes() == b"attacker content", "replacement file must be untouched"
        assert not source.exists(), "source must NOT be re-created"
        assert any("security_event undo_inode_mismatch" in r.getMessage() for r in records), (
            "a security_event undo_inode_mismatch log record must be emitted"
        )

    def test_undo_proceeds_when_inode_matches(self, tmp_path: Path) -> None:
        """Undo succeeds when (dev, ino) still match — happy path."""
        from datetime import UTC, datetime

        from history.models import Operation, OperationStatus, OperationType
        from undo.rollback import RollbackExecutor
        from undo.validator import OperationValidator

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        dst.write_bytes(b"correct file")
        st = dst.stat()

        op = Operation(
            operation_type=OperationType.MOVE,
            timestamp=datetime.now(UTC),
            source_path=src,
            destination_path=dst,
            status=OperationStatus.COMPLETED,
            metadata={"dest_dev": st.st_dev, "dest_ino": st.st_ino},
        )

        journal = tmp_path / "test.journal"
        validator = OperationValidator(journal_path=journal)
        executor = RollbackExecutor(validator=validator, journal_path=journal)
        result = executor.rollback_move(op)

        assert result is True
        assert src.read_bytes() == b"correct file"
        assert not dst.exists()

    def test_undo_legacy_row_skips_inode_check(self, tmp_path: Path) -> None:
        """Legacy rows (dest_dev=None) proceed without inode verification."""
        from datetime import UTC, datetime

        from history.models import Operation, OperationStatus, OperationType
        from undo.rollback import RollbackExecutor
        from undo.validator import OperationValidator

        src = tmp_path / "legacy_src.txt"
        dst = tmp_path / "legacy_dst.txt"
        dst.write_bytes(b"legacy content")

        # No dest_dev / dest_ino in metadata — simulates a pre-PR5 row.
        op = Operation(
            operation_type=OperationType.MOVE,
            timestamp=datetime.now(UTC),
            source_path=src,
            destination_path=dst,
            status=OperationStatus.COMPLETED,
            metadata={},
        )
        assert op.dest_dev is None  # pre-PR5 legacy row

        journal = tmp_path / "test.journal"
        validator = OperationValidator(journal_path=journal)
        executor = RollbackExecutor(validator=validator, journal_path=journal)
        result = executor.rollback_move(op)

        assert result is True
        assert src.read_bytes() == b"legacy content"

    def test_undo_partial_metadata_treated_as_legacy(self, tmp_path: Path) -> None:
        """A row with dest_dev but no dest_ino is treated as legacy — no inode check."""
        from datetime import UTC, datetime

        from history.models import Operation, OperationStatus, OperationType
        from undo.rollback import RollbackExecutor
        from undo.validator import OperationValidator

        src = tmp_path / "partial_src.txt"
        dst = tmp_path / "partial_dst.txt"
        dst.write_bytes(b"partial content")

        # dest_dev present but dest_ino missing — partial / broken row.
        op = Operation(
            operation_type=OperationType.MOVE,
            timestamp=datetime.now(UTC),
            source_path=src,
            destination_path=dst,
            status=OperationStatus.COMPLETED,
            metadata={"dest_dev": 42},  # dest_ino intentionally absent
        )
        assert op.dest_dev == 42
        assert op.dest_ino is None  # partial row

        journal = tmp_path / "test.journal"
        validator = OperationValidator(journal_path=journal)
        executor = RollbackExecutor(validator=validator, journal_path=journal)
        result = executor.rollback_move(op)

        # Falls back to legacy path — no inode mismatch refuse
        assert result is True
        assert src.read_bytes() == b"partial content"

    def test_undo_refuses_when_destination_missing(self, tmp_path: Path) -> None:
        """Undo refuses when the destination file no longer exists."""
        from datetime import UTC, datetime

        from history.models import Operation, OperationStatus, OperationType
        from undo.rollback import RollbackExecutor
        from undo.validator import OperationValidator

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"

        # Record inode from a file we immediately delete to simulate "gone".
        dst.write_bytes(b"original")
        st = dst.stat()
        dst.unlink()

        op = Operation(
            operation_type=OperationType.MOVE,
            timestamp=datetime.now(UTC),
            source_path=src,
            destination_path=dst,
            status=OperationStatus.COMPLETED,
            metadata={"dest_dev": st.st_dev, "dest_ino": st.st_ino},
        )

        journal = tmp_path / "test.journal"
        validator = OperationValidator(journal_path=journal)
        executor = RollbackExecutor(validator=validator, journal_path=journal)

        with self._capture_security_log() as records:
            result = executor.rollback_move(op)

        assert result is False
        assert not src.exists(), "source must NOT be re-created"
        assert any("security_event undo_dst_missing" in r.getMessage() for r in records)

    def test_undo_refuses_when_lstat_raises_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Undo refuses when os.lstat raises a generic OSError."""
        from datetime import UTC, datetime

        from history.models import Operation, OperationStatus, OperationType
        from undo.rollback import RollbackExecutor
        from undo.validator import OperationValidator

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        dst.write_bytes(b"content")
        st = dst.stat()

        op = Operation(
            operation_type=OperationType.MOVE,
            timestamp=datetime.now(UTC),
            source_path=src,
            destination_path=dst,
            status=OperationStatus.COMPLETED,
            metadata={"dest_dev": st.st_dev, "dest_ino": st.st_ino},
        )

        def _raise_oserror(_path: object) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr("undo.rollback.os.lstat", _raise_oserror)

        journal = tmp_path / "test.journal"
        validator = OperationValidator(journal_path=journal)
        executor = RollbackExecutor(validator=validator, journal_path=journal)

        with self._capture_security_log() as records:
            result = executor.rollback_move(op)

        assert result is False
        assert any("security_event undo_dst_lstat_error" in r.getMessage() for r in records)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _capture_security_log():
        """Context manager: capture log records from the rollback module."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            handler = _ListHandler()
            log = logging.getLogger("undo.rollback")
            log.addHandler(handler)
            try:
                yield handler.records
            finally:
                log.removeHandler(handler)

        return _ctx()


class _ListHandler(logging.Handler):
    """Accumulates LogRecord objects for test assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
