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


# ---------------------------------------------------------------------------
# safe_delete file / symlink fast path (§3.2, §6.1 file subset)
# ---------------------------------------------------------------------------


class TestSafeDeleteFileFastPath:
    """Step 3: ``safe_delete`` for files and symlinks per §3.2.

    Sequence: validate path is inside trash_dir → LOCK_EX on
    <journal>.lock → is_path_in_flight check → lexists → unlink →
    release. Step 4 adds the directory path with the atomic-rename
    pattern; this commit covers the fast path only.
    """

    def test_returns_deleted_for_quiet_file(self, tmp_path: Path) -> None:
        """No journal entries, file exists in trash → DELETED."""
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "entry.txt"
        target.write_text("removable")
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.DELETED
        assert outcome.path == target
        assert outcome.error is None
        assert not target.exists(), "file must be unlinked"

    def test_returns_missing_for_absent_path(self, tmp_path: Path) -> None:
        """Path doesn't exist in trash → MISSING (idempotent)."""
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "absent.txt"
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.MISSING
        assert outcome.error is None

    def test_returns_skipped_when_path_in_flight(self, tmp_path: Path) -> None:
        """Journal contains a ``move started`` entry whose dst is the
        trash path → SKIPPED_IN_FLIGHT, file still present."""
        from undo.durable_move import _append_journal
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "in_flight.txt"
        target.write_text("being moved")
        journal = tmp_path / "move.journal"
        # Writer recorded a move whose dst is this trash path.
        _append_journal(
            journal,
            {
                "op": "move",
                "src": str(tmp_path / "elsewhere.txt"),
                "dst": str(target),
                "state": "started",
            },
        )
        gc = TrashGC(trash, journal_path=journal)

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.SKIPPED_IN_FLIGHT
        assert target.read_text() == "being moved", (
            "file MUST NOT be unlinked while move is in flight"
        )

    def test_returns_skipped_when_done_entry_does_not_block(self, tmp_path: Path) -> None:
        """A completed ``done`` entry must NOT block deletion (the
        operation is finished; the journal record is bookkeeping)."""
        from undo.durable_move import _append_journal
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "completed.txt"
        target.write_text("ready to GC")
        journal = tmp_path / "move.journal"
        _append_journal(
            journal,
            {
                "op": "move",
                "src": str(tmp_path / "x.txt"),
                "dst": str(target),
                "state": "done",
            },
        )
        gc = TrashGC(trash, journal_path=journal)

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.DELETED
        assert not target.exists()

    def test_returns_outside_trash_for_escaped_path(self, tmp_path: Path) -> None:
        """Path outside trash root → OUTSIDE_TRASH, no operation, no
        lock acquired (security guard)."""
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        outside = tmp_path / "outside" / "x.txt"
        outside.parent.mkdir()
        outside.write_text("must not delete")
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        outcome = gc.safe_delete(outside)

        assert outcome.result is TrashDeleteResult.OUTSIDE_TRASH
        assert outside.read_text() == "must not delete", "out-of-bounds path MUST NOT be touched"

    def test_returns_outside_trash_for_traversal_attempt(self, tmp_path: Path) -> None:
        """A path containing .. that resolves outside trash root →
        OUTSIDE_TRASH. Validates the resolve()-then-relative_to()
        guard, not naive substring matching."""
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        outside = tmp_path / "neighbour"
        outside.mkdir()
        (outside / "secret.txt").write_text("not in trash")
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        # Construct a path that LOOKS rooted at trash but escapes via ..
        traversal = trash / ".." / "neighbour" / "secret.txt"
        outcome = gc.safe_delete(traversal)

        assert outcome.result is TrashDeleteResult.OUTSIDE_TRASH
        assert (outside / "secret.txt").exists()

    def test_returns_deleted_for_dangling_symlink(self, tmp_path: Path) -> None:
        """Symlink in trash whose target is missing → DELETED (lexists
        catches it where Path.exists wouldn't), symlink itself gone."""
        if __import__("os").name == "nt":
            pytest.skip("POSIX symlinks")
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        link = trash / "dangling.txt"
        link.symlink_to(tmp_path / "nonexistent")
        assert link.is_symlink() and not link.exists()  # dangling
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        outcome = gc.safe_delete(link)

        assert outcome.result is TrashDeleteResult.DELETED
        import os

        assert not os.path.lexists(link), "dangling symlink must be unlinked"

    def test_does_not_follow_symlink_to_directory(self, tmp_path: Path) -> None:
        """Symlink in trash points at an unrelated directory tree.
        ``safe_delete`` MUST unlink the link, NOT walk into the target
        and delete unrelated content. This is the load-bearing
        symlink-safety test."""
        if __import__("os").name == "nt":
            pytest.skip("POSIX symlinks")
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target_tree = tmp_path / "important_tree"
        target_tree.mkdir()
        (target_tree / "do_not_delete.txt").write_text("precious")

        link = trash / "link_to_tree"
        link.symlink_to(target_tree)
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        outcome = gc.safe_delete(link)

        assert outcome.result is TrashDeleteResult.DELETED
        assert link.is_symlink() is False, "link must be gone"
        # The target tree MUST be intact — we did NOT walk into it.
        assert target_tree.is_dir()
        assert (target_tree / "do_not_delete.txt").read_text() == "precious"

    def test_returns_permission_error_on_unlink_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError during unlink (other than FileNotFoundError) →
        PERMISSION_ERROR, error populated, file still present."""
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "locked.txt"
        target.write_text("can't delete")
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        real_unlink = Path.unlink
        sentinel_exc = OSError(13, "simulated permission denied")

        def failing_unlink(self: Path, *a: object, **k: object) -> None:
            if self == target:
                raise sentinel_exc
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.PERMISSION_ERROR
        assert outcome.error is sentinel_exc
        assert target.exists(), "file must remain on disk after PERMISSION_ERROR"

    def test_filenotfound_during_unlink_maps_to_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec §5.1 idempotency: if the path lexists at check time but
        vanishes between check and unlink (a race against another
        deleter), the OSError is FileNotFoundError → MISSING, NOT
        PERMISSION_ERROR. Mirrors the unlink-src idempotency in
        ``_execute_unlink_src`` from #201."""
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "racy.txt"
        target.write_text("about to vanish")
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        real_unlink = Path.unlink

        def vanishing_unlink(self: Path, *a: object, **k: object) -> None:
            if self == target:
                # Simulate a concurrent deleter that won the race
                # between our lexists() check and our unlink().
                raise FileNotFoundError(2, "vanished mid-delete")
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", vanishing_unlink)

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.MISSING, (
            "FileNotFoundError between lexists and unlink must map to "
            "MISSING (idempotent), not PERMISSION_ERROR"
        )
        assert outcome.error is None


# ---------------------------------------------------------------------------
# safe_delete directory path: atomic rename + unlocked rmtree (§3.3, §6.1 dir)
# ---------------------------------------------------------------------------


class TestSafeDeleteDirectory:
    """Step 4: ``safe_delete`` for directories per §3.3.

    Sequence: validate → LOCK_EX → in-flight check → lexists → atomic
    rename to ``<trash_dir>/.pending-delete-<uuid>`` → release LOCK_EX
    → unlocked ``rmtree`` of the staging path.

    The atomic-rename pivot (round 2 review fix) is what makes lock-
    hold time bounded by a single rename syscall regardless of
    directory subtree size.
    """

    def test_directory_delete_via_staging_rename(self, tmp_path: Path) -> None:
        """Populated directory in trash → DELETED, dir gone, no
        ``.pending-delete-*`` orphan left behind (rmtree finished
        successfully)."""
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "deletable_dir"
        target.mkdir()
        (target / "child.txt").write_text("inside")
        (target / "subdir").mkdir()
        (target / "subdir" / "nested.txt").write_text("nested")
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.DELETED
        assert not target.exists()
        # No orphan staging entries left over.
        leftover = [p.name for p in trash.iterdir() if p.name.startswith(".pending-delete-")]
        assert leftover == [], f"unexpected staging orphans: {leftover}"

    def test_directory_delete_uses_rename_under_lock_then_rmtree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec §3.3 contract: the rename happens before LOCK_UN; the
        rmtree happens AFTER LOCK_UN. Instruments fcntl.flock + rename
        + rmtree call order."""
        if __import__("os").name == "nt":
            pytest.skip("POSIX flock semantics")
        import fcntl
        import os
        import shutil

        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "ordered"
        target.mkdir()
        (target / "x").write_text("x")
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        events: list[str] = []
        real_flock = fcntl.flock
        real_rename = os.rename
        real_rmtree = shutil.rmtree

        def tracking_flock(fd: int, op: int) -> None:
            tag = (
                "LOCK_EX"
                if op == fcntl.LOCK_EX
                else ("LOCK_SH" if op == fcntl.LOCK_SH else "LOCK_UN")
            )
            events.append(f"flock:{tag}")
            real_flock(fd, op)

        def tracking_rename(src: object, dst: object) -> None:
            events.append(f"rename:{Path(str(src)).name}->{Path(str(dst)).name}")
            real_rename(src, dst)

        def tracking_rmtree(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            events.append(f"rmtree:{Path(str(path)).name}")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr("undo.durable_move.fcntl.flock", tracking_flock)
        monkeypatch.setattr("undo.trash_gc.os.rename", tracking_rename)
        monkeypatch.setattr("undo.trash_gc.shutil.rmtree", tracking_rmtree)

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.DELETED

        # Find indices of the key events. The contract is:
        #   LOCK_EX < rename < LOCK_UN < rmtree
        ex_idx = next(i for i, e in enumerate(events) if e == "flock:LOCK_EX")
        rename_idx = next(i for i, e in enumerate(events) if e.startswith("rename:"))
        un_idx = next(i for i, e in enumerate(events) if e == "flock:LOCK_UN")
        rmtree_idx = next(i for i, e in enumerate(events) if e.startswith("rmtree:"))

        assert ex_idx < rename_idx < un_idx < rmtree_idx, (
            f"§3.3 contract violated — expected LOCK_EX < rename < LOCK_UN < rmtree; "
            f"got events={events}"
        )

    def test_directory_returns_permission_error_when_rename_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``os.rename`` raises ``PermissionError`` → outcome is
        PERMISSION_ERROR, original dir still at original path, no
        staging dir created."""
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "no_rename_perm"
        target.mkdir()
        (target / "x").write_text("x")
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        sentinel_exc = OSError(13, "simulated permission denied")

        def failing_rename(src: object, dst: object) -> None:
            raise sentinel_exc

        monkeypatch.setattr("undo.trash_gc.os.rename", failing_rename)

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.PERMISSION_ERROR
        assert outcome.error is sentinel_exc
        assert target.is_dir(), "target must remain at original path"
        # No staging entries either (the rename never succeeded).
        leftover = [p.name for p in trash.iterdir() if p.name.startswith(".pending-delete-")]
        assert leftover == []

    def test_directory_returns_partial_failure_when_rmtree_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``shutil.rmtree`` raises after the rename succeeded → outcome
        is DELETED_WITH_STAGING_FAILURE, original path is gone (rename
        succeeded under lock), the orphan staging dir survives in
        trash_dir for the next-init recovery to clean."""
        import shutil

        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "rmtree_fails"
        target.mkdir()
        (target / "x").write_text("data")
        gc = TrashGC(trash, journal_path=tmp_path / "j")

        sentinel_exc = OSError(13, "simulated rmtree denied")

        def failing_rmtree(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise sentinel_exc

        monkeypatch.setattr("undo.trash_gc.shutil.rmtree", failing_rmtree)

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.DELETED_WITH_STAGING_FAILURE
        assert outcome.error is sentinel_exc
        assert not target.exists(), "original path MUST be gone — rename succeeded under lock"
        # The orphan staging dir survives for next-init cleanup.
        orphans = [p for p in trash.iterdir() if p.name.startswith(".pending-delete-")]
        assert len(orphans) == 1, (
            f"expected exactly one orphan staging dir; got {[p.name for p in orphans]}"
        )
        # The orphan should still contain the data — rmtree's failure means it
        # didn't get to delete anything (or only deleted partially).
        assert orphans[0].is_dir()

    def test_directory_with_in_flight_dir_move_skipped(self, tmp_path: Path) -> None:
        """Spec §6.2 race row: a ``dir_move started`` journal entry
        whose dst is the trash dir → SKIPPED_IN_FLIGHT. Critical
        because directory move/restore happens precisely via this
        journal record, and GC must wait for it to complete."""
        from undo.durable_move import _append_journal
        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "in_flight_dir"
        target.mkdir()
        (target / "x").write_text("x")
        journal = tmp_path / "move.journal"
        _append_journal(
            journal,
            {
                "op": "dir_move",
                "src": str(tmp_path / "src_dir"),
                "dst": str(target),
                "state": "started",
            },
        )
        gc = TrashGC(trash, journal_path=journal)

        outcome = gc.safe_delete(target)

        assert outcome.result is TrashDeleteResult.SKIPPED_IN_FLIGHT
        assert target.is_dir(), "directory must NOT be removed during dir_move"
        # No staging dir created either (rename skipped).
        leftover = [p.name for p in trash.iterdir() if p.name.startswith(".pending-delete-")]
        assert leftover == []

    def test_directory_orphan_cleaned_on_next_init(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end recovery flow: failed rmtree leaves an orphan
        staging dir; the next ``TrashGC`` construction sweeps it up.
        Validates that step 2's eager init recovery composes correctly
        with step 4's atomic-rename pattern."""
        import shutil

        from undo.trash_gc import TrashDeleteResult, TrashGC

        trash = tmp_path / "trash"
        trash.mkdir()
        target = trash / "to_recover"
        target.mkdir()
        (target / "x").write_text("x")

        # Capture the REAL shutil.rmtree before patching so we can
        # restore it later. After monkeypatch.setattr replaces the
        # attribute, ``shutil.rmtree`` everywhere in this process
        # references the patched value (modules are singletons).
        real_rmtree = shutil.rmtree

        # First TrashGC instance: patch rmtree to fail and produce an orphan.
        gc1 = TrashGC(trash, journal_path=tmp_path / "j")
        sentinel_exc = OSError(28, "simulated rmtree disk full")

        def failing_rmtree(*a: object, **k: object) -> None:
            raise sentinel_exc

        monkeypatch.setattr("undo.trash_gc.shutil.rmtree", failing_rmtree)

        outcome = gc1.safe_delete(target)
        assert outcome.result is TrashDeleteResult.DELETED_WITH_STAGING_FAILURE
        orphans_before = [p for p in trash.iterdir() if p.name.startswith(".pending-delete-")]
        assert len(orphans_before) == 1

        # Restore the real rmtree (captured before the patch) so the
        # next-init recovery can actually delete the orphan.
        monkeypatch.setattr("undo.trash_gc.shutil.rmtree", real_rmtree)

        # Second TrashGC instance: eager init recovery sweeps the orphan.
        TrashGC(trash, journal_path=tmp_path / "j")
        orphans_after = [p for p in trash.iterdir() if p.name.startswith(".pending-delete-")]
        assert orphans_after == [], f"next-init recovery must clean orphans; left {orphans_after}"
