"""F8 tests: trash GC race protection via the durable_move journal.

Hardening roadmap #159 F8 — a concurrent ``fo organize`` that is
restoring a file from trash (via the rollback path) must not have
its trash entry deleted by a concurrent GC sweep. F8 uses the F7
durable_move journal as a coordination point: any trash path that
appears as ``src`` or ``dst`` in an unfinished journal entry is
"in-flight" and must not be GC'd.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.ci, pytest.mark.integration]


# ---------------------------------------------------------------------------
# is_path_in_flight — journal-lookup helper
# ---------------------------------------------------------------------------


class TestIsPathInFlight:
    """``durable_move.is_path_in_flight(path, journal=...)`` returns
    True iff the path is the src/dst of an uncompleted move.
    """

    def test_empty_journal_is_never_in_flight(self, tmp_path: Path) -> None:
        from undo.durable_move import is_path_in_flight

        journal = tmp_path / "empty.journal"
        assert is_path_in_flight(tmp_path / "any.txt", journal=journal) is False

    def test_missing_journal_is_never_in_flight(self, tmp_path: Path) -> None:
        from undo.durable_move import is_path_in_flight

        journal = tmp_path / "does-not-exist.journal"
        assert is_path_in_flight(tmp_path / "x", journal=journal) is False

    def test_started_src_is_in_flight(self, tmp_path: Path) -> None:
        """A ``started`` entry marks both src and dst as in-flight —
        the move isn't committed yet, so either could be the live
        version depending on crash timing."""
        from undo.durable_move import is_path_in_flight

        journal = tmp_path / "move.journal"
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "started"}],
        )
        assert is_path_in_flight(src, journal=journal) is True
        assert is_path_in_flight(dst, journal=journal) is True

    def test_copied_both_paths_still_in_flight(self, tmp_path: Path) -> None:
        """A ``copied`` entry still has the source on disk (destination
        just landed; unlink hasn't happened). Both paths count as
        in-flight because a sweep may touch either."""
        from undo.durable_move import is_path_in_flight

        journal = tmp_path / "move.journal"
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "copied"}],
        )
        assert is_path_in_flight(src, journal=journal) is True
        assert is_path_in_flight(dst, journal=journal) is True

    def test_done_is_not_in_flight(self, tmp_path: Path) -> None:
        """``done`` operations are complete — neither path is
        in-flight and GC is safe to proceed."""
        from undo.durable_move import is_path_in_flight

        journal = tmp_path / "move.journal"
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "done"}],
        )
        assert is_path_in_flight(src, journal=journal) is False
        assert is_path_in_flight(dst, journal=journal) is False

    def test_latest_state_wins(self, tmp_path: Path) -> None:
        """Per-(src,dst) entries collapse to the latest state. A
        started-then-copied-then-done sequence is treated as done."""
        from undo.durable_move import is_path_in_flight

        journal = tmp_path / "move.journal"
        src = tmp_path / "s.txt"
        dst = tmp_path / "d.txt"
        _write_journal(
            journal,
            [
                {"op": "move", "src": str(src), "dst": str(dst), "state": "started"},
                {"op": "move", "src": str(src), "dst": str(dst), "state": "copied"},
                {"op": "move", "src": str(src), "dst": str(dst), "state": "done"},
            ],
        )
        assert is_path_in_flight(src, journal=journal) is False
        assert is_path_in_flight(dst, journal=journal) is False

    def test_unrelated_entry_does_not_block(self, tmp_path: Path) -> None:
        """A journal entry for a completely different file doesn't
        block GC of our target."""
        from undo.durable_move import is_path_in_flight

        journal = tmp_path / "move.journal"
        other_src = tmp_path / "other.txt"
        other_dst = tmp_path / "other2.txt"
        _write_journal(
            journal,
            [{"op": "move", "src": str(other_src), "dst": str(other_dst), "state": "started"}],
        )
        assert is_path_in_flight(tmp_path / "target.txt", journal=journal) is False

    def test_is_path_in_flight_matches_relative_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Coderabbit PRRT_kwDOR_Rkws59fzVv: a query via a relative
        path must match a journal entry storing the absolute form.

        Symlinks are intentionally NOT normalized (codex
        PRRT_kwDOR_Rkws59gRpv — resolving would strand the wrong
        file during crash recovery); see
        ``test_is_path_in_flight_does_not_follow_symlinks``.
        """
        from undo.durable_move import _normalized_path_str, is_path_in_flight

        target = tmp_path / "target.txt"
        target.write_text("x")
        canonical = _normalized_path_str(target)
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [
                {
                    "op": "move",
                    "src": canonical,
                    "dst": str(tmp_path / "dst.txt"),
                    "state": "started",
                }
            ],
        )

        # Relative path resolving to target must match absolute.
        monkeypatch.chdir(tmp_path)
        assert is_path_in_flight(Path("target.txt"), journal=journal) is True
        # Absolute path (already canonical) matches.
        assert is_path_in_flight(target, journal=journal) is True

    def test_is_path_in_flight_does_not_follow_symlinks(self, tmp_path: Path) -> None:
        """Codex P1 PRRT_kwDOR_Rkws59gRpv: a symlink and its target
        are DIFFERENT paths for journal purposes. A journal entry
        for the symlink must NOT match a query for the target
        (and vice versa) — otherwise sweep recovery could unlink
        the wrong file.
        """
        from undo.durable_move import _normalized_path_str, is_path_in_flight

        target = tmp_path / "target.txt"
        target.write_text("x")
        link = tmp_path / "link.txt"
        link.symlink_to(target)

        # Journal stores the symlink path.
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [
                {
                    "op": "move",
                    "src": _normalized_path_str(link),
                    "dst": str(tmp_path / "dst.txt"),
                    "state": "started",
                }
            ],
        )

        # Query via the symlink → match.
        assert is_path_in_flight(link, journal=journal) is True
        # Query via the resolved target → NO match. Following the
        # symlink during normalization would incorrectly report the
        # target as in-flight.
        assert is_path_in_flight(target, journal=journal) is False


# ---------------------------------------------------------------------------
# OperationValidator.is_trash_safe_to_delete — GC hook
# ---------------------------------------------------------------------------


class TestTrashSafeToDelete:
    """Any future trash-GC sweep must consult
    ``OperationValidator.is_trash_safe_to_delete`` before deleting a
    trash entry. The method returns False if the trash path is
    in-flight in the durable_move journal.
    """

    def test_no_journal_entry_is_safe_to_delete(self, tmp_path: Path) -> None:
        """A trash entry with no corresponding journal record is
        safe to GC — not currently being touched by any rollback."""
        from undo.validator import OperationValidator

        trash_dir = tmp_path / "trash"
        trash_dir.mkdir()
        entry = trash_dir / "old-op" / "stale.txt"
        entry.parent.mkdir()
        entry.write_text("old")

        journal = tmp_path / "move.journal"
        validator = OperationValidator(trash_dir=trash_dir, journal_path=journal)
        assert validator.is_trash_safe_to_delete(entry) is True

    def test_in_flight_trash_is_not_safe_to_delete(self, tmp_path: Path) -> None:
        """F8 core case: a trash path that's the src of an active
        restore must NOT be deleted — deleting it mid-restore would
        leave the undo history claiming success while the file was
        never restored.
        """
        from undo.validator import OperationValidator

        trash_dir = tmp_path / "trash"
        trash_dir.mkdir()
        entry = trash_dir / "active-op" / "data.txt"
        entry.parent.mkdir()
        entry.write_text("x")
        restore_target = tmp_path / "restored.txt"

        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(entry), "dst": str(restore_target), "state": "started"}],
        )

        validator = OperationValidator(trash_dir=trash_dir, journal_path=journal)
        assert validator.is_trash_safe_to_delete(entry) is False

    def test_in_flight_protects_dst_too(self, tmp_path: Path) -> None:
        """If a trash path is the destination (delete-to-trash in
        progress), we also can't GC it — the copy might be mid-flight
        and deleting the destination would orphan the operation."""
        from undo.validator import OperationValidator

        trash_dir = tmp_path / "trash"
        trash_dir.mkdir()
        entry = trash_dir / "active-op" / "data.txt"
        entry.parent.mkdir()
        source = tmp_path / "source-being-deleted.txt"

        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(source), "dst": str(entry), "state": "started"}],
        )

        validator = OperationValidator(trash_dir=trash_dir, journal_path=journal)
        assert validator.is_trash_safe_to_delete(entry) is False

    def test_completed_move_is_safe_to_delete(self, tmp_path: Path) -> None:
        """After the durable_move finishes (state: done) the trash
        path is free for GC — the operation no longer needs it."""
        from undo.validator import OperationValidator

        trash_dir = tmp_path / "trash"
        trash_dir.mkdir()
        entry = trash_dir / "old-op" / "done.txt"
        entry.parent.mkdir()
        entry.write_text("x")
        restore_target = tmp_path / "restored.txt"

        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(entry), "dst": str(restore_target), "state": "done"}],
        )

        validator = OperationValidator(trash_dir=trash_dir, journal_path=journal)
        assert validator.is_trash_safe_to_delete(entry) is True

    def test_default_journal_path_used_when_unspecified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``OperationValidator`` defaults to the shared rollback
        journal (``<state_dir>/undo/durable_move.journal``) so any
        future trash-GC consumer works out of the box without
        passing a journal path.

        Isolates via ``XDG_STATE_HOME`` so the test never reads the
        real user state dir (would be xdist-flaky if a crashed prior
        run left a journal entry behind).
        """
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))

        from undo.validator import OperationValidator

        trash_dir = tmp_path / "trash"
        trash_dir.mkdir()
        # No journal_path argument — validator must pick up the
        # isolated default (via path_manager + our XDG override).
        validator = OperationValidator(trash_dir=trash_dir)
        # Verify the validator actually resolved the isolated path,
        # not a real-user path: journal should be under our tmp_path.
        assert str(tmp_path) in str(validator.journal_path), (
            f"validator.journal_path={validator.journal_path} didn't "
            "pick up the XDG_STATE_HOME override"
        )
        # With no journal entries (isolated empty state dir), any
        # path is safe to delete.
        some_entry = trash_dir / "any" / "file.txt"
        assert validator.is_trash_safe_to_delete(some_entry) is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_journal(journal: Path, entries: list[dict]) -> None:
    """Write JSONL test-helper."""
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
