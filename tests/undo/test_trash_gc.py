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


# ---------------------------------------------------------------------------
# Init-time orphan recovery (§3.4, §6.3a)
# ---------------------------------------------------------------------------


class TestTrashGCInitRecovery:
    """Step 2: ``TrashGC.__init__`` eagerly cleans
    ``<trash_dir>/.pending-delete-*`` orphan staging dirs left by prior
    crashed ``safe_delete`` calls (rename succeeded but the unlocked
    rmtree didn't run / didn't finish).

    Lockless — these names are GC-owned and isolated from the user's
    path namespace. No journal coordination needed because the original
    paths were already isolated by the rename.
    """

    def test_init_cleans_orphan_pending_delete_entries(self, tmp_path: Path) -> None:
        """Spec §3.4: pre-seed two .pending-delete-* orphans (each with
        nested content); construct TrashGC; both gone."""
        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        orphan_a = trash / ".pending-delete-aaaa"
        orphan_b = trash / ".pending-delete-bbbb"
        for orphan in (orphan_a, orphan_b):
            orphan.mkdir()
            (orphan / "leftover.txt").write_text("from a prior crash")
        # A normal trash entry that must NOT be touched.
        normal = trash / "normal_entry.txt"
        normal.write_text("regular trash content")

        TrashGC(trash)

        assert not orphan_a.exists(), "orphan A must be cleaned by init recovery"
        assert not orphan_b.exists(), "orphan B must be cleaned by init recovery"
        assert normal.read_text() == "regular trash content", (
            "normal trash entries MUST NOT be touched by init recovery"
        )

    def test_init_skips_cleanup_for_unrelated_dotfiles(self, tmp_path: Path) -> None:
        """The prefix match is exactly ``.pending-delete-`` plus at
        least one suffix character. Other dotfiles (.gitkeep,
        .DS_Store) and prefix-collision-prone names (.pending-delete
        with no suffix, .pending-deleted-x with a different stem) MUST
        NOT be deleted."""
        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        # Things that MUST survive init recovery.
        survivors = [
            trash / ".gitkeep",
            trash / ".DS_Store",
            trash / ".pending-delete",  # no suffix — not a GC orphan
            trash / ".pending-deleted-x",  # different prefix
        ]
        for f in survivors:
            f.write_text("must survive")

        TrashGC(trash)

        for f in survivors:
            assert f.exists(), f"unrelated dotfile {f.name} must not be deleted"

    def test_init_continues_when_one_orphan_rmtree_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure on one orphan must not skip the others. The
        failing orphan is logged at WARNING and left for the next
        construction to retry."""
        import shutil

        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        good = trash / ".pending-delete-good"
        bad = trash / ".pending-delete-bad"
        good.mkdir()
        bad.mkdir()
        (good / "x").write_text("ok")
        (bad / "x").write_text("ok")

        real_rmtree = shutil.rmtree

        def selective_failing_rmtree(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if str(path).endswith(".pending-delete-bad"):
                raise OSError(13, "simulated permission denied")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr("undo.trash_gc.shutil.rmtree", selective_failing_rmtree)

        TrashGC(trash)

        assert not good.exists(), "good orphan must still be cleaned"
        assert bad.exists(), "failing orphan must survive for next-init retry"

    def test_init_aggregated_log_line_emitted(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Spec §5.3: aggregate ``trash GC init recovery: N orphans
        cleaned, M failed`` line at INFO so operators can spot a
        stuck-orphan pattern without scanning every DEBUG line."""
        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        for i in range(3):
            o = trash / f".pending-delete-{i:04d}"
            o.mkdir()

        with caplog.at_level("INFO", logger="undo.trash_gc"):
            TrashGC(trash)

        agg_lines = [
            r.getMessage()
            for r in caplog.records
            if r.levelname == "INFO"
            and r.name == "undo.trash_gc"
            and "init recovery" in r.getMessage()
        ]
        assert agg_lines, (
            f"expected an INFO 'init recovery' aggregate line; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        msg = agg_lines[0]
        # Exact format the spec promised — "3 orphans cleaned, 0 failed".
        assert "3" in msg and "cleaned" in msg
        assert "0" in msg and "failed" in msg

    def test_init_handles_missing_trash_dir(self, tmp_path: Path) -> None:
        """``trash_dir`` doesn't exist on construction; init must not
        raise (creates the directory, scan finds zero entries, no
        recovery work)."""
        from undo.trash_gc import TrashGC

        trash = tmp_path / "trash" / "nested" / "deep"
        assert not trash.exists()

        # Constructor must not raise.
        gc = TrashGC(trash)

        assert gc.trash_dir.is_dir()
        # No orphans existed to clean — no .pending-delete-* entries left.
        assert list(trash.iterdir()) == []
