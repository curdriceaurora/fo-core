"""F8.1 tests for ``src/undo/trash_gc.py`` (issue #202).

Step 1 covers types + constructor only — the locking dance,
init-time orphan recovery, and the actual ``safe_delete`` body land in
subsequent steps. These tests assert the public API surface so reviewers
can validate the contract before any filesystem mutation lands.

Spec reference: ``docs/internal/F8-1-trash-gc-design.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.ci, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Outcome types (§2 / §5.1)
# ---------------------------------------------------------------------------


class TestTrashDeleteResult:
    """The six-variant ``TrashDeleteResult`` enum is the public outcome
    contract. Callers exhaustive-match on these — adding a new variant is
    a breaking change. The string values are stable so they can appear in
    log lines and structured telemetry.
    """

    def test_enum_has_exactly_six_documented_variants(self) -> None:
        """Spec §2: six outcomes — DELETED, DELETED_WITH_STAGING_FAILURE,
        SKIPPED_IN_FLIGHT, MISSING, PERMISSION_ERROR, OUTSIDE_TRASH.

        Test guards against accidental additions: a new outcome must
        update this test (and the design spec + decision table in §5.1).
        """
        from undo.trash_gc import TrashDeleteResult

        assert {member.name for member in TrashDeleteResult} == {
            "DELETED",
            "DELETED_WITH_STAGING_FAILURE",
            "SKIPPED_IN_FLIGHT",
            "MISSING",
            "PERMISSION_ERROR",
            "OUTSIDE_TRASH",
        }

    def test_enum_values_are_lowercase_strings_for_log_telemetry(self) -> None:
        """Spec §5.3: outcomes appear in log lines. Stable lowercase
        string values guarantee log-grep across versions."""
        from undo.trash_gc import TrashDeleteResult

        assert TrashDeleteResult.DELETED.value == "deleted"
        assert TrashDeleteResult.DELETED_WITH_STAGING_FAILURE.value == (
            "deleted_with_staging_failure"
        )
        assert TrashDeleteResult.SKIPPED_IN_FLIGHT.value == "skipped"
        assert TrashDeleteResult.MISSING.value == "missing"
        assert TrashDeleteResult.PERMISSION_ERROR.value == "permission_error"
        assert TrashDeleteResult.OUTSIDE_TRASH.value == "outside_trash"

    def test_enum_inherits_from_str_for_isoformat_compat(self) -> None:
        """``TrashDeleteResult`` is a ``str`` subclass so callers can
        ``json.dumps`` an outcome directly (matches PR #197's pattern
        for journal record fields)."""
        from undo.trash_gc import TrashDeleteResult

        assert isinstance(TrashDeleteResult.DELETED, str)
        assert TrashDeleteResult.DELETED == "deleted"


class TestTrashDeleteOutcome:
    """``TrashDeleteOutcome`` is the dataclass returned by every
    ``safe_delete`` call. Frozen so callers can stash it in sets or
    dict keys; carries enough fields to drive both logging (§5.3) and
    operator-visible reporting.
    """

    def test_outcome_carries_result_path_reason(self) -> None:
        from undo.trash_gc import TrashDeleteOutcome, TrashDeleteResult

        outcome = TrashDeleteOutcome(
            result=TrashDeleteResult.DELETED,
            path=Path("/some/path"),
            reason="cleaned",
        )
        assert outcome.result is TrashDeleteResult.DELETED
        assert outcome.path == Path("/some/path")
        assert outcome.reason == "cleaned"
        assert outcome.error is None

    def test_outcome_carries_optional_error(self) -> None:
        """Spec §2: ``error`` is populated for ``PERMISSION_ERROR`` AND
        ``DELETED_WITH_STAGING_FAILURE`` so operators can correlate the
        outcome with the underlying exception."""
        from undo.trash_gc import TrashDeleteOutcome, TrashDeleteResult

        exc = OSError(13, "Permission denied")
        outcome = TrashDeleteOutcome(
            result=TrashDeleteResult.PERMISSION_ERROR,
            path=Path("/p"),
            reason="unlink raised",
            error=exc,
        )
        assert outcome.error is exc

    def test_outcome_is_frozen(self) -> None:
        """Frozen dataclass — caller mutation is rejected at runtime."""
        from undo.trash_gc import TrashDeleteOutcome, TrashDeleteResult

        outcome = TrashDeleteOutcome(
            result=TrashDeleteResult.DELETED,
            path=Path("/p"),
            reason="ok",
        )
        with pytest.raises((AttributeError, Exception)):
            outcome.result = TrashDeleteResult.MISSING  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TrashGC constructor (§2)
# ---------------------------------------------------------------------------


class TestTrashGCConstructor:
    """Spec §2: ``TrashGC(trash_dir, *, journal_path=None)``.

    Step 1 only validates the constructor surface — eager init-time
    orphan recovery (§3.4) lands in step 2; ``safe_delete`` lands in
    steps 3 (file/symlink) and 4 (directory).
    """

    def test_constructor_stores_trash_dir(self, tmp_path: Path) -> None:
        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        gc = TrashGC(trash)
        assert gc.trash_dir == trash

    def test_constructor_creates_missing_trash_dir(self, tmp_path: Path) -> None:
        """A missing trash dir is created (mirrors
        ``OperationValidator.__init__`` behavior so callers don't need
        to pre-create the directory)."""
        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash" / "nested"
        assert not trash.exists()
        TrashGC(trash)
        assert trash.is_dir()

    def test_constructor_accepts_journal_path_override(self, tmp_path: Path) -> None:
        """The journal path is keyword-only and overrides the default
        ``undo._journal.default_journal_path()``. Tests pass a per-test
        path to isolate from the real user journal (same pattern as
        ``OperationValidator``)."""
        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash"
        journal = tmp_path / "test.journal"
        gc = TrashGC(trash, journal_path=journal)
        assert gc.journal_path == journal

    def test_constructor_journal_path_defaults_to_shared(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ``journal_path`` is not passed, it falls through to
        ``undo._journal.default_journal_path()`` — same default as
        ``OperationValidator`` and ``RollbackExecutor`` so all three
        coordinate on the same on-disk journal."""
        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash"
        sentinel = tmp_path / "default.journal"
        monkeypatch.setattr("undo._journal.default_journal_path", lambda: sentinel)
        gc = TrashGC(trash)
        assert gc.journal_path == sentinel

    def test_constructor_keyword_only_journal_path(self, tmp_path: Path) -> None:
        """``journal_path`` MUST be keyword-only — passing it
        positionally is a TypeError. Prevents the trash_dir / journal
        argument-order confusion that any path-typed pair invites."""
        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash"
        journal = tmp_path / "j"
        with pytest.raises(TypeError):
            TrashGC(trash, journal)  # type: ignore[misc]
