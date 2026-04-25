"""F7 tests for ``src/undo/durable_move.py``.

Hardening roadmap #159 F7 — undo rollback durability. The helper
is *atomic on same-device* and *durable + idempotent on cross-device*
(EXDEV). Tests lock both branches + the recovery sweep that cleans
up after a crash mid-move.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.ci, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestDurableMoveSameDevice:
    """Same-device moves use ``os.replace`` — truly atomic."""

    def test_basic_move(self, tmp_path: Path) -> None:
        from undo.durable_move import durable_move

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("payload")
        journal = tmp_path / "move.journal"

        durable_move(src, dst, journal=journal)

        assert not src.exists()
        assert dst.read_text() == "payload"

    def test_move_to_nested_destination(self, tmp_path: Path) -> None:
        """Destination's parent directory may not exist; helper
        creates it (matches ``shutil.move`` semantics callers relied
        on pre-F7)."""
        from undo.durable_move import durable_move

        src = tmp_path / "src.txt"
        src.write_text("x")
        dst = tmp_path / "nested" / "deep" / "dst.txt"
        journal = tmp_path / "move.journal"

        durable_move(src, dst, journal=journal)
        assert dst.exists()

    def test_same_device_does_not_append_journal(self, tmp_path: Path) -> None:
        """Same-device renames are truly atomic — no journal entry
        needed. Keeping the journal empty on the happy path keeps the
        recovery sweep fast even after millions of successful
        operations."""
        from undo.durable_move import durable_move

        src = tmp_path / "a.txt"
        dst = tmp_path / "b.txt"
        src.write_text("x")
        journal = tmp_path / "move.journal"

        durable_move(src, dst, journal=journal)

        # Journal either doesn't exist or is empty after a clean
        # same-device move.
        if journal.exists():
            assert journal.read_text().strip() == ""

    def test_same_device_preserves_symlink_identity(self, tmp_path: Path) -> None:
        """Round-9 INV-2b: same-device move of a symlink must leave a
        symlink at dst — NOT dereference and copy the target's bytes.
        ``os.replace`` already does this natively on POSIX (symlinks
        are first-class file-system entities to ``rename(2)``); this
        test locks down the contract so a future refactor that
        accidentally adds a ``stat`` follow before the rename can't
        regress it without breaking the test.

        Companion to ``test_cross_device_preserves_symlink_identity``
        which covers the EXDEV branch.
        """
        if os.name == "nt":
            pytest.skip("POSIX symlinks")
        from undo.durable_move import durable_move

        target = tmp_path / "target.txt"
        target.write_text("target bytes")
        src = tmp_path / "link.txt"
        src.symlink_to(target)
        dst = tmp_path / "moved-link.txt"
        journal = tmp_path / "move.journal"

        durable_move(src, dst, journal=journal)

        assert dst.is_symlink(), (
            "same-device move of a symlink must land a symlink at dst "
            "(round-9 INV-2b — locks down the os.replace contract)"
        )
        assert os.readlink(dst) == str(target)
        assert not src.is_symlink() and not src.exists()
        assert target.read_text() == "target bytes"

    def test_preserves_file_contents_and_permissions(self, tmp_path: Path) -> None:
        """File contents + mode bits survive the move (pre-F7 behavior
        via ``shutil.move``)."""
        from undo.durable_move import durable_move

        if os.name == "nt":
            pytest.skip("POSIX mode bits")
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        payload = bytes(range(256))
        src.write_bytes(payload)
        os.chmod(src, 0o640)
        journal = tmp_path / "move.journal"

        durable_move(src, dst, journal=journal)

        assert dst.read_bytes() == payload
        mode = dst.stat().st_mode & 0o777
        assert mode == 0o640, f"expected 0o640 to survive, got {mode:#o}"


# ---------------------------------------------------------------------------
# Cross-device (EXDEV) simulation
# ---------------------------------------------------------------------------


class TestDurableMoveCrossDevice:
    """EXDEV branch: copy-fsync-replace with a journal.

    Real cross-device moves require two mount points, which isn't
    practical in tests. We simulate by monkeypatching ``os.replace``
    to raise ``OSError(EXDEV)`` on the first call so the helper falls
    through to the copy path.
    """

    def _force_exdev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make ``os.replace`` raise EXDEV once to trigger the
        cross-device path. Subsequent calls pass through to the
        real implementation so the sweep/cleanup helpers still work.
        """
        real_replace = os.replace
        state = {"triggered": False}

        def exdev_once(src, dst):  # type: ignore[no-untyped-def]
            if not state["triggered"]:
                state["triggered"] = True
                raise OSError(errno.EXDEV, "Cross-device link", str(src))
            return real_replace(src, dst)

        monkeypatch.setattr("undo.durable_move.os.replace", exdev_once)

    def test_cross_device_copy_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Happy-path EXDEV: helper falls back to copy+fsync+unlink
        and leaves the destination with the source's contents."""
        from undo.durable_move import durable_move

        self._force_exdev(monkeypatch)

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("cross-device payload")
        journal = tmp_path / "move.journal"

        durable_move(src, dst, journal=journal)

        assert not src.exists()
        assert dst.read_text() == "cross-device payload"

    def test_cross_device_journal_reaches_done_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a successful EXDEV move, the journal entry for the
        operation reaches ``state: done``. A subsequent ``sweep`` is
        a no-op."""
        from undo.durable_move import durable_move, sweep

        self._force_exdev(monkeypatch)
        src = tmp_path / "s.txt"
        dst = tmp_path / "d.txt"
        src.write_text("x")
        journal = tmp_path / "move.journal"

        durable_move(src, dst, journal=journal)

        entries = _read_journal(journal)
        assert entries, "journal must contain at least one entry on EXDEV"
        assert entries[-1]["state"] == "done"
        # Sweep is a no-op on done entries.
        sweep(journal)
        assert dst.read_text() == "x"

    def test_cross_device_preserves_symlink_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex P1 PRRT_kwDOR_Rkws59gnab: EXDEV symlink moves must
        preserve the symlink itself — NOT dereference and copy the
        target's bytes. The pre-F7 ``shutil.move`` does this; the F7
        helper must match.
        """
        if os.name == "nt":
            pytest.skip("POSIX symlinks")
        from undo.durable_move import durable_move

        self._force_exdev(monkeypatch)
        target = tmp_path / "target.txt"
        target.write_text("target bytes")
        src = tmp_path / "link.txt"
        src.symlink_to(target)
        dst = tmp_path / "moved-link.txt"
        journal = tmp_path / "move.journal"

        durable_move(src, dst, journal=journal)

        # dst is now a symlink (not a regular file), pointing at the
        # same target. Reading through it still yields target bytes,
        # but the identity check is what matters.
        assert dst.is_symlink(), (
            "EXDEV move of a symlink must land a symlink at dst; "
            "shutil.copyfile dereferences and produces a regular file "
            "(codex P1 PRRT_kwDOR_Rkws59gnab)"
        )
        assert os.readlink(dst) == str(target), (
            f"symlink target must be preserved; got {os.readlink(dst)!r}"
        )
        # Original symlink is gone; target itself is untouched.
        assert not src.is_symlink() and not src.exists()
        assert target.read_text() == "target bytes"

    def test_cross_device_preserves_dangling_symlink(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dangling symlinks (target doesn't exist) are legitimate
        file-system entries — ``shutil.move`` preserves them on EXDEV.
        ``shutil.copyfile`` on a dangling symlink raises
        ``FileNotFoundError`` because it tries to open the target, so
        the old implementation would have destroyed the entry entirely.
        The readlink-based path handles this correctly.
        """
        if os.name == "nt":
            pytest.skip("POSIX symlinks")
        from undo.durable_move import durable_move

        self._force_exdev(monkeypatch)
        src = tmp_path / "dangling.txt"
        # Target deliberately does NOT exist.
        src.symlink_to(tmp_path / "nonexistent.txt")
        dst = tmp_path / "moved-dangling.txt"
        journal = tmp_path / "move.journal"

        durable_move(src, dst, journal=journal)

        assert dst.is_symlink(), "dangling symlink must land as a symlink at dst"
        assert os.readlink(dst) == str(tmp_path / "nonexistent.txt")
        assert not src.is_symlink() and not os.path.lexists(src)

    def test_cross_device_symlink_clears_stale_tmp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a prior crashed attempt left a stale tmp symlink at the
        exact PID-suffixed path, the new attempt removes it and
        proceeds — covers the defensive ``os.path.lexists`` branch
        in the EXDEV symlink path.
        """
        if os.name == "nt":
            pytest.skip("POSIX symlinks")
        from undo.durable_move import durable_move

        self._force_exdev(monkeypatch)
        target = tmp_path / "target.txt"
        target.write_text("t")
        src = tmp_path / "link.txt"
        src.symlink_to(target)
        dst = tmp_path / "dst.txt"

        # Pre-create a stale tmp at the exact same name the symlink
        # branch generates. Matches the ``f".{dst.name}.{pid}.symlink.tmp"``
        # format in ``_durable_cross_device_move``.
        stale = dst.parent / f".{dst.name}.{os.getpid()}.symlink.tmp"
        stale.symlink_to(tmp_path / "something-unrelated")
        assert os.path.lexists(stale)

        durable_move(src, dst, journal=tmp_path / "j.journal")

        assert dst.is_symlink()
        assert os.readlink(dst) == str(target)
        # Stale tmp is gone (either cleaned up pre-symlink or swept
        # by os.replace).
        assert not os.path.lexists(stale)

    def test_cross_device_fsyncs_src_parent_after_unlink(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex P2 PRRT_kwDOR_Rkws59gnah: after unlinking ``src`` in
        the EXDEV path, ``src.parent`` must be fsynced BEFORE the
        ``done`` journal entry is appended. Otherwise a power loss in
        that window could let ``src`` reappear on reboot while the
        journal records ``done`` — sweep drops the entry and skips
        cleanup, leaving a phantom copy on disk.

        Instrumented by capturing every ``fsync_directory`` call and
        asserting ``src.parent`` was fsynced AFTER the ``os.unlink(src)``
        but before the final ``_append_journal`` with state=done.
        """
        from undo import durable_move as dm

        self._force_exdev(monkeypatch)

        fsync_calls: list[tuple[str, Path]] = []  # (stage, path)
        unlinked: list[Path] = []
        journaled_done: list[int] = []  # indices into fsync_calls

        real_fsync = dm.fsync_directory
        real_unlink = os.unlink
        real_append = dm._append_journal

        def tracking_fsync(path):  # type: ignore[no-untyped-def]
            fsync_calls.append(("fsync", Path(path)))
            real_fsync(path)

        def tracking_unlink(p, *a, **k):  # type: ignore[no-untyped-def]
            unlinked.append(Path(p))
            fsync_calls.append(("unlink", Path(p)))
            return real_unlink(p, *a, **k)

        def tracking_append(journal, payload):  # type: ignore[no-untyped-def]
            if payload.get("state") == "done":
                journaled_done.append(len(fsync_calls))
            return real_append(journal, payload)

        monkeypatch.setattr("undo.durable_move.fsync_directory", tracking_fsync)
        monkeypatch.setattr("undo.durable_move.os.unlink", tracking_unlink)
        monkeypatch.setattr("undo.durable_move._append_journal", tracking_append)

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("x")
        journal = tmp_path / "move.journal"

        dm.durable_move(src, dst, journal=journal)

        # Find the unlink(src) event and the fsync(src) that must
        # follow it, and verify both happen before state=done was
        # journaled.
        unlink_idx = next(
            i for i, (stage, p) in enumerate(fsync_calls) if stage == "unlink" and p == src
        )
        post_unlink_src_fsync = [
            i
            for i, (stage, p) in enumerate(fsync_calls)
            if i > unlink_idx and stage == "fsync" and p == src
        ]
        assert post_unlink_src_fsync, (
            f"fsync_directory(src) must be called after os.unlink(src) "
            f"(codex P2 PRRT_kwDOR_Rkws59gnah). call trace: {fsync_calls}"
        )
        done_idx = journaled_done[0]
        assert post_unlink_src_fsync[0] < done_idx, (
            f"fsync_directory(src) must happen BEFORE state=done is journaled; "
            f"src fsync at {post_unlink_src_fsync[0]}, done at {done_idx}; "
            f"trace: {fsync_calls}"
        )


# ---------------------------------------------------------------------------
# Recovery sweep — crash simulation
# ---------------------------------------------------------------------------


class TestDurableMoveSweep:
    """``sweep(journal)`` at CLI startup completes or rolls back
    interrupted EXDEV moves based on the last journal state.
    """

    def test_sweep_noop_on_empty_journal(self, tmp_path: Path) -> None:
        """Missing or empty journal → sweep is a no-op."""
        from undo.durable_move import sweep

        sweep(tmp_path / "nonexistent.journal")  # no raise

        journal = tmp_path / "empty.journal"
        journal.write_text("")
        sweep(journal)

    def test_sweep_started_state_retains_entry_and_preserves_paths(self, tmp_path: Path) -> None:
        """Codex P1 PRRT_kwDOR_Rkws59gbdD + PRRT_kwDOR_Rkws59g2Ex:
        ``started`` is AMBIGUOUS — a crash between ``os.replace`` and
        the ``copied`` journal append leaves a ``started`` entry with
        dst already overwritten. Sweep must:

        - NEVER unlink dst (data-loss in the "crashed before replace"
          sub-case)
        - NEVER unlink src (data-loss in the same sub-case if dst was
          never replaced)
        - RETAIN the journal entry so the next sweep / operator /
          retry can reconcile (dropping it would lose recovery
          metadata and potentially orphan files permanently)
        """
        from undo.durable_move import sweep

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("intact source")
        dst.write_text("legitimate pre-existing destination")
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "started"}],
        )

        sweep(journal)

        # Both paths preserved unchanged — sweep didn't destroy anything.
        assert src.read_text() == "intact source"
        assert dst.read_text() == "legitimate pre-existing destination", (
            "started-state sweep must not destroy a legitimate dst; "
            "this was the codex P1 data-loss path"
        )
        # Entry RETAINED so the next sweep / retry / operator can
        # reconcile (codex P1 PRRT_kwDOR_Rkws59g2Ex — retry metadata).
        entries = _read_journal(journal)
        assert len(entries) == 1, (
            f"started entry must be retained for ambiguity resolution; got {entries}"
        )
        assert entries[0]["state"] == "started"
        assert entries[0]["src"] == str(src)
        assert entries[0]["dst"] == str(dst)

    def test_sweep_completes_copied_state(self, tmp_path: Path) -> None:
        """State ``copied`` = crash after destination fsync but
        before source unlink. Destination is complete; finish the
        operation by unlinking the source.
        """
        from undo.durable_move import sweep

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("leftover source")
        dst.write_text("complete destination")
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "copied"}],
        )

        sweep(journal)

        # Source removed; destination preserved.
        assert not src.exists(), "copied-state src must be unlinked on sweep"
        assert dst.read_text() == "complete destination"
        assert _read_journal(journal) == []

    def test_sweep_copied_state_retains_when_dst_missing(self, tmp_path: Path) -> None:
        """Codex P1 PRRT_kwDOR_Rkws59hGWW: if an out-of-band actor
        removed ``dst`` between the ``copied`` journal write and the
        next sweep (operator cleanup, another process, backup
        restore), sweep MUST NOT unlink ``src`` — that would destroy
        the last remaining copy. The entry is retained for operator
        or next-pass reconciliation instead.
        """
        from undo.durable_move import sweep

        src = tmp_path / "src.txt"
        src.write_text("last remaining copy")
        dst = tmp_path / "dst.txt"  # deliberately absent — removed between journal + sweep
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "copied"}],
        )

        sweep(journal)

        # Source preserved (data not destroyed), entry retained.
        assert src.read_text() == "last remaining copy", (
            "copied-state sweep must not unlink src when dst is missing; "
            "this was a data-loss path (codex P1 PRRT_kwDOR_Rkws59hGWW)"
        )
        entries = _read_journal(journal)
        assert len(entries) == 1 and entries[0]["state"] == "copied", (
            f"entry must be retained for reconciliation; got {entries}"
        )

    def test_sweep_retains_entries_with_unknown_op(self, tmp_path: Path) -> None:
        """Codex P2 PRRT_kwDOR_Rkws59hdFb: the journal is shared —
        ``op`` field is reserved for future ``"copy"``, ``"symlink"``
        etc. Sweep must NEVER apply move semantics to a non-``move``
        op (would data-loss on downgrade from a binary that wrote
        the newer op). Retain for a future handler.
        """
        from undo.durable_move import sweep

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("original")
        dst.write_text("pre-existing")
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [
                # Future op type sweep doesn't understand.
                {
                    "op": "mirror",
                    "src": str(src),
                    "dst": str(dst),
                    "state": "copied",
                },
            ],
        )

        sweep(journal)

        # Neither path touched (crucial: the copied-state move branch
        # would have unlinked src — that MUST NOT happen here).
        assert src.read_text() == "original", (
            "sweep must not apply move semantics to op='mirror'; "
            "unlinking src would be data loss (codex P2 PRRT_kwDOR_Rkws59hdFb)"
        )
        assert dst.read_text() == "pre-existing"
        # Entry retained for a future binary that knows ``mirror``.
        entries = _read_journal(journal)
        assert len(entries) == 1 and entries[0]["op"] == "mirror"

    def test_sweep_copied_state_tolerates_fsync_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unusual filesystems can raise ``OSError`` from directory
        fsync. Sweep logs it and continues — the unlink itself
        succeeded, and the next sweep pass would notice if the entry
        hadn't persisted. This is a defensive branch but worth
        locking down so a future refactor doesn't accidentally turn
        a non-fatal log into a retry.
        """
        from undo import durable_move as dm

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("x")
        dst.write_text("y")
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "copied"}],
        )

        # First fsync (journal fd in sweep locked body) succeeds; the
        # second fsync (src.parent after unlink) raises. We detect
        # the call by path: only the src.parent fsync targets src.
        real_fsync = dm.fsync_directory

        def flaky_fsync(path):  # type: ignore[no-untyped-def]
            if Path(path) == src:
                raise OSError(5, "fsync failed on exotic fs")
            real_fsync(path)

        monkeypatch.setattr("undo.durable_move.fsync_directory", flaky_fsync)

        # Must not raise — sweep tolerates the fsync failure.
        dm.sweep(journal)

        # Reconciliation still completed: src was unlinked and entry
        # dropped from the journal (the fsync failure is non-fatal).
        assert not src.exists()
        assert _read_journal(journal) == []

    def test_sweep_copied_state_fsyncs_src_parent_after_unlink(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex P2 PRRT_kwDOR_Rkws59hT9b: parallel to the in-line
        post-unlink fsync in ``_durable_cross_device_move`` (gnah):
        the sweep's copied-state reconciliation also unlinks ``src``,
        and that unlink must be fsynced to disk BEFORE sweep truncates
        the journal entry. Otherwise a power loss between the unlink
        and the truncate could let ``src`` reappear on reboot while
        the recovery record is already gone — no retry metadata, no
        way to know there's a phantom file to clean up.

        Instruments fsync_directory to verify the order:
        unlink(src) → fsync(src.parent) → return True → sweep truncate.
        """
        from undo import durable_move as dm

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("leftover")
        dst.write_text("complete")
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "copied"}],
        )

        trace: list[tuple[str, Path]] = []
        real_fsync = dm.fsync_directory
        real_unlink = Path.unlink

        def tracking_fsync(path):  # type: ignore[no-untyped-def]
            trace.append(("fsync", Path(path)))
            real_fsync(path)

        def tracking_unlink(self, *a, **k):  # type: ignore[no-untyped-def]
            trace.append(("unlink", Path(self)))
            return real_unlink(self, *a, **k)

        monkeypatch.setattr("undo.durable_move.fsync_directory", tracking_fsync)
        monkeypatch.setattr(Path, "unlink", tracking_unlink)

        dm.sweep(journal)

        # The sweep flock-body itself fsyncs the journal fd; we care
        # about the ordering of src's unlink vs the subsequent
        # fsync_directory(src).
        unlink_ix = next(i for i, (stage, p) in enumerate(trace) if stage == "unlink" and p == src)
        post_unlink_fsync = [
            i
            for i, (stage, p) in enumerate(trace)
            if i > unlink_ix and stage == "fsync" and p == src
        ]
        assert post_unlink_fsync, (
            f"copied-state sweep must fsync_directory(src) AFTER "
            f"os.unlink(src) (codex P2 PRRT_kwDOR_Rkws59hT9b); trace: {trace}"
        )

    def test_sweep_copied_state_accepts_symlink_dst(self, tmp_path: Path) -> None:
        """``os.path.lexists(dst)`` (not ``dst.exists()``) is used so a
        dangling-symlink dst still counts as present — the symlink
        itself is the thing we committed to via ``os.replace`` in the
        symlink-preserving EXDEV branch.
        """
        if os.name == "nt":
            pytest.skip("POSIX symlinks")
        from undo.durable_move import sweep

        src = tmp_path / "src.txt"
        src.write_text("src")
        dst = tmp_path / "dst-link.txt"
        # Dangling symlink (target doesn't exist). `dst.exists()` → False,
        # but `os.path.lexists(dst)` → True.
        dst.symlink_to(tmp_path / "nonexistent-target.txt")
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "copied"}],
        )

        sweep(journal)

        # dst still present (the symlink itself); src removed as normal.
        assert dst.is_symlink(), "dangling symlink dst must satisfy the presence check"
        assert not src.exists()
        assert _read_journal(journal) == []

    def test_sweep_ignores_done_entries(self, tmp_path: Path) -> None:
        """``done`` entries were logged for audit/observability but
        are already complete. Sweep leaves src/dst as-is and clears
        the journal."""
        from undo.durable_move import sweep

        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [
                {
                    "op": "move",
                    "src": str(tmp_path / "old.txt"),
                    "dst": str(tmp_path / "new.txt"),
                    "state": "done",
                }
            ],
        )

        # Neither path exists on disk — sweep must not raise about
        # missing files on a done entry.
        sweep(journal)
        assert _read_journal(journal) == []

    def test_sweep_retains_failed_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex P1 PRRT_kwDOR_Rkws59fwMK: if
        ``_complete_or_rollback`` logs an ``OSError`` and can't
        finish (transient lock / permission denied), sweep must
        retain the entry so the next startup retries. Without this,
        a single failed sweep strands the operation forever.
        """
        from undo.durable_move import sweep

        # Two entries: one that succeeds (done), one that fails on
        # src.unlink during the copied-state reconciliation.
        # (Previously the failing entry was ``started`` + dst.unlink;
        # that scenario no longer applies now that started-state sweep
        # leaves dst alone — codex P1 PRRT_kwDOR_Rkws59gbdD.)
        journal = tmp_path / "move.journal"
        good_src = tmp_path / "good-src.txt"
        good_dst = tmp_path / "good-dst.txt"
        bad_src = tmp_path / "bad-src.txt"
        bad_dst = tmp_path / "bad-dst.txt"
        bad_src.write_text("leftover after copied")
        # bad_dst MUST exist so sweep passes the codex P1
        # PRRT_kwDOR_Rkws59hGWW dst-present check and reaches the
        # src.unlink attempt (which is what the monkeypatched Path.unlink
        # intercepts to simulate an OSError).
        bad_dst.write_text("complete destination")
        _write_journal(
            journal,
            [
                {
                    "op": "move",
                    "src": str(good_src),
                    "dst": str(good_dst),
                    "state": "done",
                },
                {
                    "op": "move",
                    "src": str(bad_src),
                    "dst": str(bad_dst),
                    "state": "copied",
                },
            ],
        )

        real_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if str(self) == str(bad_src):
                raise OSError(13, "simulated permission denied")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        sweep(journal)

        # Failed entry retained for retry.
        entries = _read_journal(journal)
        assert any(e["src"] == str(bad_src) for e in entries), (
            f"failed sweep entry must be retained; journal: {entries}"
        )
        # Successful (done) entry dropped.
        assert not any(e["src"] == str(good_src) for e in entries), (
            "successfully reconciled entry must be dropped"
        )

    def test_sweep_is_idempotent(self, tmp_path: Path) -> None:
        """Sweeping twice is safe — the same retained ``started``
        entry survives both passes without raising. Uses a ``done``
        entry alongside to verify sweep makes progress (drops done,
        retains ambiguous started) on each pass."""
        from undo.durable_move import sweep

        journal = tmp_path / "move.journal"
        src = tmp_path / "src.txt"
        src.write_text("x")
        dst = tmp_path / "dst.txt"
        _write_journal(
            journal,
            [
                # One ambiguous entry retained across sweeps.
                {"op": "move", "src": str(src), "dst": str(dst), "state": "started"},
                # One done entry dropped on first sweep, no-op on second.
                {
                    "op": "move",
                    "src": str(tmp_path / "d_src"),
                    "dst": str(tmp_path / "d_dst"),
                    "state": "done",
                },
            ],
        )

        sweep(journal)
        # After pass 1: started retained, done dropped.
        after_first = _read_journal(journal)
        assert len(after_first) == 1 and after_first[0]["state"] == "started"

        sweep(journal)  # must not raise
        # After pass 2: same started entry still there (still ambiguous).
        after_second = _read_journal(journal)
        assert after_second == after_first, (
            "idempotent sweeps must not accumulate or lose the retained entry"
        )

    def test_sweep_tolerates_missing_files(self, tmp_path: Path) -> None:
        """A journal entry for paths that no longer exist on disk
        doesn't crash the sweep. Uses ``copied`` with dst missing —
        which hits the codex P1 PRRT_kwDOR_Rkws59hGWW guard: the
        entry is RETAINED (not cleared) because unlinking src when
        dst is gone would destroy the last copy. Sweep must not
        raise for the missing files.
        """
        from undo.durable_move import sweep

        src = tmp_path / "gone_src.txt"
        dst = tmp_path / "gone_dst.txt"  # deliberately absent
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "copied"}],
        )

        sweep(journal)  # must not raise

        # Entry retained per codex P1 PRRT_kwDOR_Rkws59hGWW — dst is
        # missing so sweep refuses to unlink src (even though src is
        # also missing here). The retain-on-dst-missing contract is
        # state-based, not content-aware.
        entries = _read_journal(journal)
        assert len(entries) == 1 and entries[0]["state"] == "copied"


# ---------------------------------------------------------------------------
# Atomic failure modes
# ---------------------------------------------------------------------------


class TestDurableMoveFailureModes:
    """Failure modes: missing source, permission errors, crash
    during copy, etc."""

    def test_sweep_unlocked_body_started_retains_entry(self, tmp_path: Path) -> None:
        """``_sweep_unlocked_body`` (Windows/no-fcntl fallback) shares
        the same started-state contract as the locked path: never
        unlink either path AND retain the entry (codex P1
        PRRT_kwDOR_Rkws59gbdD + PRRT_kwDOR_Rkws59g2Ex). Exercised
        directly since the real platform gate uses ``os.name`` at
        module level and can't be cleanly monkeypatched from tests
        (the module captures ``os`` at import time).
        """
        from undo.durable_move import _sweep_unlocked_body

        journal = tmp_path / "move.journal"
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("intact")
        dst.write_text("legitimate pre-existing")
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "started"}],
        )

        _sweep_unlocked_body(journal)

        # Both paths preserved; entry retained for reconciliation.
        assert src.read_text() == "intact"
        assert dst.read_text() == "legitimate pre-existing"
        entries = _read_journal(journal)
        assert len(entries) == 1 and entries[0]["state"] == "started", (
            f"unlocked sweep must retain started entry; got {entries}"
        )

    def test_sweep_unlocked_body_retains_failed_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_sweep_unlocked_body`` retains failed entries for retry
        (matches the POSIX-locked path contract). Uses a ``copied``
        entry since started-state reconciliation no longer unlinks
        anything (codex P1 PRRT_kwDOR_Rkws59gbdD).

        Coderabbit round-10: ``bad_dst`` MUST exist on disk so sweep
        passes the codex hGWW dst-present check and reaches the
        ``src.unlink`` attempt — otherwise the dst-missing guard
        retains the entry first and the monkeypatched failing_unlink
        never runs (false-pass).
        """
        from undo.durable_move import _sweep_unlocked_body

        journal = tmp_path / "move.journal"
        bad_src = tmp_path / "bad-src.txt"
        bad_dst = tmp_path / "bad-dst.txt"
        bad_src.write_text("x")
        # bad_dst MUST exist so sweep doesn't short-circuit on the
        # dst-missing guard before reaching the monkeypatched unlink.
        bad_dst.write_text("complete destination")
        _write_journal(
            journal,
            [{"op": "move", "src": str(bad_src), "dst": str(bad_dst), "state": "copied"}],
        )

        real_unlink = Path.unlink
        unlink_calls: list[Path] = []

        def failing_unlink(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            unlink_calls.append(Path(self))
            if str(self) == str(bad_src):
                raise OSError(13, "simulated")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        _sweep_unlocked_body(journal)

        # Verify the failing_unlink path was actually exercised — proves
        # the dst-present guard was passed and the OSError-retain branch
        # is what kept the entry (not the dst-missing short-circuit).
        assert bad_src in unlink_calls, (
            f"failing_unlink must have been invoked on bad_src; "
            f"if absent, the dst-missing guard short-circuited and the "
            f"OSError-retain branch was never tested. Calls: {unlink_calls}"
        )
        entries = _read_journal(journal)
        assert any(e["src"] == str(bad_src) for e in entries)

    def test_normalized_path_str_does_not_follow_symlinks(self, tmp_path: Path) -> None:
        """Codex P1 PRRT_kwDOR_Rkws59gRpv: a symlink must journal as
        itself, not its target. Otherwise a crash during the
        post-replace unlink would unlink the target instead of the
        symlink on sweep recovery.
        """
        from undo.durable_move import _normalized_path_str

        target = tmp_path / "target.txt"
        target.write_text("real content")
        link = tmp_path / "link.txt"
        link.symlink_to(target)

        normalized = _normalized_path_str(link)
        # Must preserve the symlink path — NOT resolve to the target.
        assert normalized == str(link), (
            f"symlink path normalization must not follow the link; "
            f"got {normalized!r}, expected {link!r}"
        )
        # Sanity: target's normalized form is still itself.
        assert _normalized_path_str(target) == str(target)

    def test_normalized_path_still_resolves_relative_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Normalization still handles the coderabbit
        PRRT_kwDOR_Rkws59fzVv concern: relative / redundant paths
        collapse to the absolute form, even without following
        symlinks."""
        from undo.durable_move import _normalized_path_str

        monkeypatch.chdir(tmp_path)
        # ``./sub/../target.txt`` should collapse to ``<tmp_path>/target.txt``.
        # ``os.path.normcase`` is a no-op on POSIX (where this test runs),
        # so we assert against the exact ``str(tmp_path / "target.txt")``.
        # On Windows the assertion uses normcase on both sides.
        result = _normalized_path_str(Path("./sub/../target.txt"))
        expected = os.path.normcase(str(tmp_path / "target.txt"))
        assert result == expected, result

    def test_normalized_path_case_folds_on_windows(self) -> None:
        """Codex P2 PRRT_kwDOR_Rkws59hp2G: ``_normalized_path_str``
        applies ``os.path.normcase`` so Windows case-insensitive
        paths normalize identically. Without this, a journal entry
        recorded for ``C:\\foo`` and a query for ``c:\\foo`` would
        miss each other and trash GC could delete a path that an
        active rollback move depended on.

        On POSIX, ``os.path.normcase`` is the identity function — we
        verify that contract here so the call is safe on all
        platforms. The Windows case-fold itself is exercised by
        construction of the helper (the call is unconditional).
        """
        from undo.durable_move import _normalized_path_str

        # POSIX no-op contract: a mixed-case path normalizes to itself.
        if os.name != "nt":
            mixed = _normalized_path_str(Path("/Tmp/MixedCase/File.TXT"))
            assert mixed == "/Tmp/MixedCase/File.TXT", (
                "os.path.normcase must be a no-op on POSIX so the wrapper "
                "doesn't break case-sensitive paths"
            )
        else:  # pragma: no cover - exercised on Windows CI only
            # Windows: case-fold to lowercase + normalize separators.
            mixed_a = _normalized_path_str(Path("C:/Foo/Bar.txt"))
            mixed_b = _normalized_path_str(Path("c:/foo/BAR.TXT"))
            assert mixed_a == mixed_b, (
                "Windows case-insensitive paths must normalize identically "
                "(codex hp2G); journal lookups depend on it"
            )

    def test_directory_source_raises_is_a_directory(self, tmp_path: Path) -> None:
        """Coderabbit PRRT_kwDOR_Rkws59fzVo: directory inputs must be
        rejected up front with ``IsADirectoryError``, not silently
        handled differently by the same-device and EXDEV paths.
        """
        from undo.durable_move import durable_move

        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "inside.txt").write_text("x")

        with pytest.raises(IsADirectoryError, match="directory"):
            durable_move(src_dir, tmp_path / "dst", journal=tmp_path / "j")
        # Directory and contents are untouched.
        assert (src_dir / "inside.txt").exists()

    def test_missing_source_raises_file_not_found(self, tmp_path: Path) -> None:
        from undo.durable_move import durable_move

        with pytest.raises(FileNotFoundError):
            durable_move(
                tmp_path / "ghost.txt",
                tmp_path / "dst.txt",
                journal=tmp_path / "j.log",
            )

    def test_non_exdev_os_replace_error_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Permission errors (or any OSError other than EXDEV) from
        ``os.replace`` on the fast path must propagate to the caller —
        we only fall through to the durable path on EXDEV."""
        from undo.durable_move import durable_move

        def deny_replace(src, dst):  # type: ignore[no-untyped-def]
            raise PermissionError("not allowed")

        monkeypatch.setattr("undo.durable_move.os.replace", deny_replace)

        src = tmp_path / "src.txt"
        src.write_text("x")
        with pytest.raises(PermissionError):
            durable_move(src, tmp_path / "dst.txt", journal=tmp_path / "j")

    def test_copystat_failure_is_non_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``copystat`` failure in the EXDEV path is logged at
        DEBUG and the move still completes with default mode."""
        from undo.durable_move import durable_move

        # Force EXDEV
        def always_exdev(src, dst):  # type: ignore[no-untyped-def]
            raise OSError(errno.EXDEV, "Cross-device link")

        # Sabotage copystat
        def broken_copystat(src, dst, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("copystat failed")

        real_replace = os.replace
        call_count = {"n": 0}

        def exdev_then_real(src, dst):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError(errno.EXDEV, "EXDEV", str(src))
            return real_replace(src, dst)

        monkeypatch.setattr("undo.durable_move.os.replace", exdev_then_real)
        monkeypatch.setattr("undo.durable_move.shutil.copystat", broken_copystat)

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("payload")
        durable_move(src, dst, journal=tmp_path / "j")
        # Move still completed.
        assert dst.read_text() == "payload"
        assert not src.exists()

    def test_exdev_source_already_gone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FileNotFoundError from the final source unlink is swallowed
        (source was already gone — treat the move as complete)."""
        from undo.durable_move import durable_move

        real_replace = os.replace
        real_unlink = os.unlink
        call_count = {"replace": 0}

        def exdev_then_real(src, dst):  # type: ignore[no-untyped-def]
            call_count["replace"] += 1
            if call_count["replace"] == 1:
                raise OSError(errno.EXDEV, "EXDEV", str(src))
            return real_replace(src, dst)

        def unlink_missing(p):  # type: ignore[no-untyped-def]
            # Let the temp-file cleanup path go through, but raise
            # FileNotFoundError for the source unlink at the end of
            # _durable_cross_device_move.
            if str(p).endswith("src.txt"):
                raise FileNotFoundError(2, "gone")
            return real_unlink(p)

        monkeypatch.setattr("undo.durable_move.os.replace", exdev_then_real)
        monkeypatch.setattr("undo.durable_move.os.unlink", unlink_missing)

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("payload")
        # Must not raise — source-already-gone is benign.
        durable_move(src, dst, journal=tmp_path / "j")
        assert dst.read_text() == "payload"

    def test_sweep_copied_source_unlink_failure_retained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """STATE_COPIED + source unlink OSError → entry retained
        (covers the copied-branch failure path symmetric to the
        started-branch one)."""
        from undo.durable_move import _sweep_unlocked_body

        src = tmp_path / "leftover-src.txt"
        dst = tmp_path / "final-dst.txt"
        src.write_text("still here")
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "copied"}],
        )

        real_unlink = Path.unlink

        def deny_unlink(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if str(self) == str(src):
                raise OSError(13, "denied")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", deny_unlink)

        _sweep_unlocked_body(journal)

        entries = _read_journal(journal)
        assert any(e["src"] == str(src) for e in entries), (
            "copied-state unlink failure must retain entry"
        )

    def test_read_journal_drops_malformed_lines(self, tmp_path: Path) -> None:
        """Lines that can't parse as JSON, or parse as the wrong
        shape, are logged + dropped (not treated as valid entries).

        Coderabbit PRRT_kwDOR_Rkws59gscQ: the production helper
        returns typed ``_JournalEntry`` objects while the local test
        helper of the same name returns ``list[dict]``. Alias the
        import so the two call sites can't be confused.
        """
        from undo.durable_move import _read_journal as _prod_read_journal

        journal = tmp_path / "corrupt.journal"
        journal.write_text(
            "\n".join(
                [
                    "not json at all",
                    json.dumps({"op": "move"}),  # missing fields
                    json.dumps({"op": "move", "src": 1, "dst": "x", "state": "done"}),  # wrong type
                    json.dumps({"op": "move", "src": "a", "dst": "b", "state": "done"}),  # ok
                ]
            )
            + "\n"
        )
        entries = _prod_read_journal(journal)
        assert len(entries) == 1
        assert entries[0].state == "done"

    def test_crash_between_started_and_copied_leaves_recoverable_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate crash during EXDEV copy: raise an exception from
        the copy step after the journal has been written. The journal
        must retain the ``started`` entry so sweep can clean up."""
        from undo.durable_move import durable_move

        # Force EXDEV then make the copy fail.
        def always_exdev(src, dst):  # type: ignore[no-untyped-def]
            raise OSError(errno.EXDEV, "Cross-device link", str(src))

        monkeypatch.setattr("undo.durable_move.os.replace", always_exdev)

        def bad_copyfile(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("simulated copy crash")

        monkeypatch.setattr("undo.durable_move.shutil.copyfile", bad_copyfile)

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("payload")
        journal = tmp_path / "move.journal"

        with pytest.raises(OSError, match="simulated copy crash"):
            durable_move(src, dst, journal=journal)

        # Journal must have a started entry so sweep can clean up.
        entries = _read_journal(journal)
        assert any(e["state"] == "started" for e in entries), (
            f"started entry must persist after copy crash; got {entries}"
        )


# ---------------------------------------------------------------------------
# Round-3 P1 regression tests
# ---------------------------------------------------------------------------


class TestStartedStateDoesNotUnlinkDestination:
    """Codex P1 PRRT_kwDOR_Rkws59gbdD.

    Belt-and-suspenders coverage for the started-state invariant:
    ``sweep`` must never unlink ``dst`` when reconciling a ``started``
    entry, because the EXDEV path writes the journal BEFORE the copy
    begins. ``dst`` may be a legitimate pre-existing file that our
    transaction never touched.
    """

    def test_started_sweep_preserves_dst_contents_byte_for_byte(self, tmp_path: Path) -> None:
        """Detailed byte-equality check — not just ``dst.exists()``.

        Guards against future "clever" rollback logic that might,
        say, truncate dst to zero instead of unlinking it.
        """
        from undo.durable_move import sweep

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_bytes(b"source bytes")
        original_dst_bytes = b"pre-existing dst content \x00 with \xff NUL + binary"
        dst.write_bytes(original_dst_bytes)
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "started"}],
        )

        sweep(journal)

        assert dst.read_bytes() == original_dst_bytes, (
            "started-state sweep must preserve dst byte-for-byte "
            "(codex P1 PRRT_kwDOR_Rkws59gbdD data-loss path)"
        )
        assert src.read_bytes() == b"source bytes"

    def test_started_sweep_tolerates_absent_dst(self, tmp_path: Path) -> None:
        """If dst never existed (common case), sweep still doesn't
        raise. Entry is retained (same ambiguity class as the
        dst-exists case; codex P1 PRRT_kwDOR_Rkws59g2Ex) so operator
        or retry can reconcile.
        """
        from undo.durable_move import sweep

        src = tmp_path / "src.txt"
        src.write_text("x")
        dst = tmp_path / "never_existed.txt"  # deliberately absent
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "started"}],
        )

        sweep(journal)  # must not raise

        assert src.read_text() == "x"
        assert not dst.exists()
        # Retained for reconciliation (same invariant as dst-exists case).
        entries = _read_journal(journal)
        assert len(entries) == 1 and entries[0]["state"] == "started"

    def test_started_entry_persists_across_multiple_sweeps(self, tmp_path: Path) -> None:
        """Codex P1 PRRT_kwDOR_Rkws59g2Ex: a ``started`` entry is
        AMBIGUOUS — we don't know if ``os.replace`` completed before
        the crash. Sweep retains it so the next startup (or a retry
        that logs new started/copied/done) can supersede it. The
        entry must NEVER silently disappear across sweeps.
        """
        from undo.durable_move import sweep

        src = tmp_path / "ambig-src.txt"
        dst = tmp_path / "ambig-dst.txt"
        src.write_text("src")
        dst.write_text("dst")
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "started"}],
        )

        # Sweep 5 times; entry must survive every pass.
        for pass_num in range(5):
            sweep(journal)
            entries = _read_journal(journal)
            assert len(entries) == 1 and entries[0]["state"] == "started", (
                f"pass {pass_num}: started entry must survive every sweep; got {entries}"
            )

        # A later successful retry supersedes the started entry with
        # copied/done; sweep then drops the stale record.
        _write_journal(
            journal,
            [
                {"op": "move", "src": str(src), "dst": str(dst), "state": "started"},
                {"op": "move", "src": str(src), "dst": str(dst), "state": "done"},
            ],
        )
        sweep(journal)
        assert _read_journal(journal) == [], (
            "a later done entry for the same (src,dst) must supersede the ambiguous started record"
        )


class TestAppendJournalFlockCoordination:
    """Codex P1 PRRT_kwDOR_Rkws59gbdH.

    ``sweep()`` holds ``fcntl.flock(LOCK_EX)`` during the read-
    modify-truncate cycle. ``_append_journal`` MUST acquire the same
    advisory lock before writing — otherwise sweep can truncate away
    a freshly-appended ``started`` record, losing crash-recovery
    metadata.
    """

    def test_append_journal_blocks_while_sweep_holds_flock(self, tmp_path: Path) -> None:
        """Core invariant: an ``_append_journal`` call issued while
        sweep (or any other holder) has ``LOCK_EX`` on the journal
        blocks until the lock is released. Proves the appender
        respects the same advisory lock sweep uses.
        """
        fcntl = pytest.importorskip("fcntl")
        import threading
        import time

        from undo.durable_move import _append_journal

        journal = tmp_path / "move.journal"
        journal.write_text("")  # create so the held-open fd has an inode

        # Acquire LOCK_EX in the main thread — mimics sweep holding
        # the journal during its read-modify-truncate cycle.
        holder = open(journal, "r+", encoding="utf-8")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)

        # ``appender_entered`` is set IMMEDIATELY before the
        # _append_journal call — eliminates the coderabbit
        # PRRT_kwDOR_Rkws59gscT false-positive path where a slow CI
        # runner just never scheduled the appender thread within the
        # timeout. If this event fires then the remaining
        # ``append_done.wait(...)`` is a real invariant: thread is
        # running, is about to flock, MUST block on LOCK_EX.
        appender_entered = threading.Event()
        append_done = threading.Event()
        append_error: list[BaseException] = []

        def _appender() -> None:
            appender_entered.set()
            try:
                _append_journal(
                    journal,
                    {"op": "move", "src": "/a", "dst": "/b", "state": "started"},
                )
            except BaseException as exc:  # pragma: no cover - failure surface
                append_error.append(exc)
            finally:
                append_done.set()

        t = threading.Thread(target=_appender, daemon=True)
        t.start()

        # Confirm the appender thread actually ran — disambiguates
        # "blocked on flock" (intended) from "never scheduled"
        # (false pass on shared CI runners).
        assert appender_entered.wait(timeout=5.0), (
            "appender thread never scheduled — test environment issue"
        )

        # Thread reached _append_journal and is now in (or about to
        # enter) the open() + flock() sequence. Without flock
        # coordination it would finish in microseconds; with flock,
        # it blocks on LOCK_EX while our holder still has it.
        assert not append_done.wait(timeout=0.5), (
            "_append_journal must block while another holder has LOCK_EX "
            "(codex P1 PRRT_kwDOR_Rkws59gbdH)"
        )
        # Journal still empty — appender has not written its line.
        assert journal.read_text() == ""

        # Release the lock; appender should now complete promptly.
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
        assert append_done.wait(timeout=5.0), "appender never completed after flock release"
        t.join(timeout=2.0)
        assert append_error == [], f"appender raised: {append_error}"

        # Allow a brief moment for the append to flush (fsync is
        # synchronous, but scheduling can vary on shared CI runners).
        deadline = time.monotonic() + 2.0
        lines: list[str] = []
        while time.monotonic() < deadline:
            lines = [line for line in journal.read_text().splitlines() if line]
            if lines:
                break
        assert len(lines) == 1, f"expected 1 appended line, got {lines}"
        assert '"state": "started"' in lines[0]

    def test_append_journal_writes_when_no_holder(self, tmp_path: Path) -> None:
        """Baseline: with no competing lock holder, the flock-aware
        appender still writes the line + flushes + fsyncs (i.e. the
        non-blocking path works)."""
        pytest.importorskip("fcntl")

        from undo.durable_move import _append_journal

        journal = tmp_path / "move.journal"
        _append_journal(journal, {"op": "move", "src": "/x", "dst": "/y", "state": "done"})

        lines = [line for line in journal.read_text().splitlines() if line]
        assert len(lines) == 1
        assert '"state": "done"' in lines[0]


# ---------------------------------------------------------------------------
# Round-10 regression tests
# ---------------------------------------------------------------------------


class TestDirectoryMoveCoordination:
    """Coderabbit round-10 (Major): directory restores previously
    bypassed the F8 coordination channel — ``shutil.move`` doesn't
    write to the journal, so concurrent trash GC could delete a
    directory mid-restore. ``directory_move`` wraps the call in
    ``op="dir_move"`` started/done entries so
    :func:`is_path_in_flight` sees the path as in-flight.
    """

    def test_directory_move_writes_started_done_pair(self, tmp_path: Path) -> None:
        """The wrapper writes exactly two journal entries: started
        before the move, done after."""
        from undo.durable_move import directory_move

        src = tmp_path / "src_dir"
        src.mkdir()
        (src / "inside.txt").write_text("content")
        dst = tmp_path / "dst_dir"
        journal = tmp_path / "move.journal"

        directory_move(src, dst, journal=journal)

        # Move completed.
        assert dst.is_dir() and (dst / "inside.txt").read_text() == "content"
        assert not src.exists()
        # Journal contains exactly the started → done pair.
        entries = _read_journal(journal)
        assert len(entries) == 2
        assert entries[0]["op"] == "dir_move"
        assert entries[0]["state"] == "started"
        assert entries[1]["op"] == "dir_move"
        assert entries[1]["state"] == "done"

    def test_directory_move_marks_path_in_flight_during_move(self, tmp_path: Path) -> None:
        """Codex hdFb / coderabbit round-10: while
        :func:`directory_move` is mid-shutil.move, both src and dst
        must register as in-flight via :func:`is_path_in_flight`.
        Without this, trash GC could delete src or dst between the
        started entry and the move's completion.
        """
        import threading

        from undo.durable_move import directory_move, is_path_in_flight

        src = tmp_path / "src_dir"
        src.mkdir()
        # Generate enough content that shutil.move takes a non-trivial
        # amount of time, giving the test thread a window to observe
        # the in-flight state.
        for i in range(50):
            (src / f"file_{i}.txt").write_text("x" * 1024)
        dst = tmp_path / "dst_dir"
        journal = tmp_path / "move.journal"

        observations: list[bool] = []
        in_flight_seen = threading.Event()
        move_done = threading.Event()

        def observer() -> None:
            # Spin until the started entry is visible OR the move
            # completes. Then record one observation.
            while not move_done.is_set():
                if is_path_in_flight(src, journal=journal) or is_path_in_flight(
                    dst, journal=journal
                ):
                    observations.append(True)
                    in_flight_seen.set()
                    return
            observations.append(False)

        t = threading.Thread(target=observer, daemon=True)
        t.start()
        try:
            directory_move(src, dst, journal=journal)
        finally:
            move_done.set()
            t.join(timeout=2)

        assert observations and observations[0] is True, (
            "is_path_in_flight must see the directory as in-flight while "
            "directory_move is running (round-10 F8 coordination)"
        )
        # After the move completes the in-flight marker is cleared
        # (done entry supersedes started).
        assert is_path_in_flight(src, journal=journal) is False
        assert is_path_in_flight(dst, journal=journal) is False

    def test_directory_move_writes_done_even_if_move_fails(self, tmp_path: Path) -> None:
        """If shutil.move raises, the wrapper still writes ``done`` so
        the in-flight marker is released. The exception propagates;
        callers must reconcile partial on-disk state themselves
        (directory moves are non-recoverable from sweep)."""
        from undo.durable_move import directory_move, is_path_in_flight

        src = tmp_path / "src_dir"
        src.mkdir()
        (src / "f.txt").write_text("x")
        # Use a path that shutil.move will reject (parent doesn't
        # exist would normally be created by us, so use a destination
        # under a file).
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        dst = blocker / "child" / "dst_dir"
        journal = tmp_path / "move.journal"

        with pytest.raises((OSError, FileNotFoundError, NotADirectoryError)):
            directory_move(src, dst, journal=journal)

        # done was still written (released in-flight marker).
        entries = _read_journal(journal)
        assert any(e["state"] == "done" for e in entries), (
            f"directory_move must write done in the finally block even when "
            f"shutil.move raises; got entries: {entries}"
        )
        assert is_path_in_flight(src, journal=journal) is False


class TestSweepDirMoveHandling:
    """Sweep handles ``op="dir_move"`` entries by dropping them
    (with a warning if state != done). Move semantics MUST NOT
    apply since shutil.move is non-atomic and non-idempotent.
    """

    def test_sweep_drops_dir_move_done(self, tmp_path: Path) -> None:
        from undo.durable_move import sweep

        src = tmp_path / "src_dir"
        src.mkdir()
        dst = tmp_path / "dst_dir"
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "dir_move", "src": str(src), "dst": str(dst), "state": "done"}],
        )

        sweep(journal)
        assert _read_journal(journal) == []
        # Disk state untouched.
        assert src.is_dir() and not dst.exists()

    def test_sweep_drops_dir_move_started_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A stranded ``dir_move`` started entry (move crashed) is
        dropped — sweep can't safely retry shutil.move. The warning
        prompts operator inspection of the on-disk state."""
        from undo.durable_move import sweep

        src = tmp_path / "src_dir"
        src.mkdir()
        dst = tmp_path / "dst_dir"
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "dir_move", "src": str(src), "dst": str(dst), "state": "started"}],
        )

        with caplog.at_level("WARNING", logger="undo.durable_move"):
            sweep(journal)
        # Entry dropped (in-flight marker released).
        assert _read_journal(journal) == []
        # Disk state is whatever shutil.move left it — sweep doesn't
        # touch either path. We just verify the warning fired.
        assert any("dir_move entry" in r.getMessage() for r in caplog.records), (
            f"warning must mention the dir_move entry; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_sweep_does_not_apply_move_semantics_to_dir_move(self, tmp_path: Path) -> None:
        """A ``dir_move`` entry in a ``copied`` state must NOT trigger
        ``src.unlink`` (move-semantics for op=move). Belt-and-
        suspenders against a future refactor accidentally collapsing
        the op-dispatch."""
        from undo.durable_move import sweep

        src = tmp_path / "must-survive.txt"
        src.write_text("must not be unlinked")
        dst = tmp_path / "elsewhere.txt"
        dst.write_text("dst content")
        journal = tmp_path / "move.journal"
        # Synthetic: copied state on a dir_move op shouldn't appear
        # in practice (dir_move only writes started/done) but the
        # sweep code path must defend against it.
        _write_journal(
            journal,
            [{"op": "dir_move", "src": str(src), "dst": str(dst), "state": "copied"}],
        )

        sweep(journal)

        assert src.read_text() == "must not be unlinked", (
            "dir_move must NEVER trigger src.unlink even in unexpected states"
        )
        assert dst.read_text() == "dst content"


class TestIsPathInFlightSharedLock:
    """Codex P2 PRRT_kwDOR_Rkws59ir1P: ``is_path_in_flight`` must
    acquire ``LOCK_SH`` so it cannot return False while a writer is
    mid-append. Without this, F8 trash GC can delete a path that's
    about to be marked in-flight.
    """

    def test_is_path_in_flight_blocks_while_writer_holds_lock_ex(self, tmp_path: Path) -> None:
        """Hold ``LOCK_EX`` from main thread; the reader thread's
        ``is_path_in_flight`` call must block until the lock is
        released."""
        fcntl = pytest.importorskip("fcntl")
        import threading

        from undo.durable_move import _append_journal, is_path_in_flight

        journal = tmp_path / "move.journal"
        # Pre-populate so the file exists for the reader's open().
        _append_journal(journal, {"op": "move", "src": "/a", "dst": "/b", "state": "done"})

        # Hold LOCK_EX from the main thread (simulates an active
        # _append_journal mid-write).
        holder = open(journal, "a", encoding="utf-8")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)

        reader_entered = threading.Event()
        reader_done = threading.Event()
        result_holder: list[bool | None] = [None]

        def _reader() -> None:
            reader_entered.set()
            try:
                result_holder[0] = is_path_in_flight(Path("/x"), journal=journal)
            finally:
                reader_done.set()

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        assert reader_entered.wait(timeout=5.0), "reader never scheduled"
        # Reader must block on LOCK_SH while we hold LOCK_EX.
        assert not reader_done.wait(timeout=0.5), (
            "is_path_in_flight must block while another holder has LOCK_EX "
            "(codex P2 PRRT_kwDOR_Rkws59ir1P); without the shared lock, GC "
            "could observe a stale journal mid-append and delete an "
            "in-flight path."
        )

        # Release; reader should now complete.
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
        assert reader_done.wait(timeout=5.0), "reader never completed"
        t.join(timeout=2)
        # Result is False because /x is not in the (single done) entry.
        assert result_holder[0] is False


# ---------------------------------------------------------------------------
# F7.1 journal schema v2 — parser + rejection-rule coverage
# (tracks #201, docs/internal/F7-1-journal-protocol-design.md §4, §9.1)
# ---------------------------------------------------------------------------


class TestJournalSchemaV2Parser:
    """F7.1 step 1: parser accepts v1 and v2 records, preserves unknown-op
    raw lines, and rejects each §4.1 malformed case with a WARNING log.
    """

    def test_v1_record_no_schema_field_accepted(self, tmp_path: Path) -> None:
        """A v1 record (no ``schema`` field) must still parse — PR #197
        back-compat. The resulting entry has ``schema == 1``, ``op_id is
        None``, ``tmp_path is None``."""
        from undo.durable_move import _parse_journal_text

        line = json.dumps({"op": "move", "src": "/a", "dst": "/b", "state": "done"})
        entries = _parse_journal_text(line + "\n")

        assert len(entries) == 1
        e = entries[0]
        assert e.op == "move"
        assert e.src == "/a"
        assert e.dst == "/b"
        assert e.state == "done"
        assert e.schema == 1
        assert e.op_id is None
        assert e.tmp_path is None

    def test_v2_known_op_record_round_trips(self, tmp_path: Path) -> None:
        """v2 record with all known-op fields round-trips through parse →
        serialize → parse and preserves every field."""
        from undo.durable_move import _parse_journal_text, _serialize_entry

        src = str(tmp_path / "source.txt")
        dst = str(tmp_path / "dest.txt")
        tmp = str(tmp_path / "tmp-file.tmp")
        src_line = json.dumps(
            {
                "schema": 2,
                "op": "move",
                "op_id": "abc123",
                "src": src,
                "dst": dst,
                "state": "started",
                "tmp_path": tmp,
                "ts": 1714000000.5,
                "host_pid": 12345,
            }
        )
        parsed = _parse_journal_text(src_line + "\n")
        assert len(parsed) == 1
        e = parsed[0]
        assert e.schema == 2
        assert e.op_id == "abc123"
        assert e.tmp_path == tmp
        assert e.ts == 1714000000.5
        assert e.host_pid == 12345

        # Re-parse the serialized form — must produce an identical entry.
        reparsed = _parse_journal_text(_serialize_entry(e) + "\n")
        assert len(reparsed) == 1
        assert reparsed[0] == e

    def test_non_object_json_rejected(self, tmp_path: Path) -> None:
        """§4.1 rule 2 (codex iy4w): JSON that parses but isn't an object
        (null, list, scalar, string) is logged + skipped, not AttributeError.
        Covers BOTH ``_parse_journal_text`` AND ``_read_journal`` — the
        round-8 fix missed ``_read_journal`` per the #201 body."""
        from undo.durable_move import _parse_journal_text, _read_journal

        corrupt = "\n".join(
            [
                "null",
                "[]",
                '"bare string"',
                "42",
                json.dumps({"op": "move", "src": "/a", "dst": "/b", "state": "done"}),
            ]
        )
        journal = tmp_path / "corrupt.journal"
        journal.write_text(corrupt + "\n")

        # Parser via text: 4 rejects + 1 accept.
        via_text = _parse_journal_text(corrupt)
        assert len(via_text) == 1
        assert via_text[0].op == "move"

        # Parser via file: same contract, no AttributeError from the
        # pre-F7.1 _read_journal missing-dict-guard bug.
        via_file = _read_journal(journal)
        assert len(via_file) == 1
        assert via_file[0].op == "move"

    def test_missing_required_field_rejected(self) -> None:
        """§4.1 rule 3: missing op/src/dst/state logged + skipped."""
        from undo.durable_move import _parse_journal_text

        corrupt = "\n".join(
            [
                json.dumps({"op": "move"}),  # missing src, dst, state
                json.dumps({"src": "/a", "dst": "/b", "state": "done"}),  # missing op
                json.dumps({"op": "move", "src": "/a"}),  # missing dst, state
                json.dumps({"op": "move", "src": "/a", "dst": "/b", "state": "done"}),  # ok
            ]
        )
        entries = _parse_journal_text(corrupt + "\n")
        assert len(entries) == 1
        assert entries[0].op == "move"

    def test_oversized_line_rejected(self) -> None:
        """§4.1 rule 7: line >64 KiB rejected to prevent pathological payloads."""
        from undo.durable_move import _parse_journal_text

        good = json.dumps({"op": "move", "src": "/a", "dst": "/b", "state": "done"})
        huge = json.dumps(
            {
                "op": "move",
                "src": "/a",
                "dst": "/b",
                "state": "done",
                "_padding": "x" * (65 * 1024),
            }
        )
        entries = _parse_journal_text(huge + "\n" + good + "\n")
        assert len(entries) == 1
        assert entries[0].src == "/a"

    def test_v2_known_op_missing_op_id_rejected(self) -> None:
        """§4.1 rule 8: v2 writer always emits op_id; a known-op v2 record
        missing it is corrupt/external input and MUST be rejected (not
        silently collapsed with v1 identity)."""
        from undo.durable_move import _parse_journal_text

        corrupt = json.dumps(
            {
                "schema": 2,
                "op": "move",
                # op_id missing — parse-time reject
                "src": "/a",
                "dst": "/b",
                "state": "done",
            }
        )
        ok = json.dumps(
            {
                "schema": 2,
                "op": "move",
                "op_id": "legit",
                "src": "/c",
                "dst": "/d",
                "state": "done",
            }
        )
        entries = _parse_journal_text(corrupt + "\n" + ok + "\n")
        assert len(entries) == 1
        assert entries[0].op_id == "legit"

    def test_v2_move_started_missing_tmp_path_rejected(self) -> None:
        """§4.1 rule 9: v2 ``move started`` without ``tmp_path`` is
        rejected — the tmp-exists invariant (§7.1) depends on every
        such record carrying the field. Without it sweep could
        misinfer post-replace and unlink src (data loss)."""
        from undo.durable_move import _parse_journal_text

        corrupt = json.dumps(
            {
                "schema": 2,
                "op": "move",
                "op_id": "abc",
                "src": "/a",
                "dst": "/b",
                "state": "started",
                # tmp_path missing — parse-time reject for v2 move started
            }
        )
        # Same record but copied/done — no tmp_path required.
        ok_copied = json.dumps(
            {
                "schema": 2,
                "op": "move",
                "op_id": "def",
                "src": "/c",
                "dst": "/d",
                "state": "copied",
            }
        )
        entries = _parse_journal_text(corrupt + "\n" + ok_copied + "\n")
        assert len(entries) == 1
        assert entries[0].state == "copied"

    def test_unknown_op_preserves_raw_line(self) -> None:
        """§4.2: unknown-op records preserve the FULL raw JSON line on
        ``_raw`` so compaction re-serializes them verbatim. A future
        binary with a handler for the op receives all fields the writer
        persisted, NOT just the v2 parser's known core."""
        from undo.durable_move import _parse_journal_text, _serialize_entry

        future_record = {
            "schema": 3,
            "op": "future_copy",
            "op_id": "xyz",
            "src": "/a",
            "dst": "/b",
            "state": "started",
            "future_field_1": "content-hash-abc",
            "future_field_2": {"nested": [1, 2, 3]},
        }
        raw_line = json.dumps(future_record)
        entries = _parse_journal_text(raw_line + "\n")

        assert len(entries) == 1
        e = entries[0]
        assert e.op == "future_copy"
        assert e._raw == raw_line

        # Compaction writes the entry back — must equal the original
        # raw line, NOT a v2-projected subset.
        serialized = _serialize_entry(e)
        # Allow for whitespace/key-ordering differences via re-parse.
        assert json.loads(serialized) == future_record

    def test_known_op_unknown_future_field_ignored(self) -> None:
        """§4.2: for KNOWN ops, extra JSON fields are ignored (no _raw).
        Those ops have a stable schema we control; extras are noise."""
        from undo.durable_move import _parse_journal_text

        line = json.dumps(
            {
                "schema": 2,
                "op": "move",
                "op_id": "abc",
                "src": "/a",
                "dst": "/b",
                "state": "done",
                "experimental_field": "should-be-dropped",
            }
        )
        entries = _parse_journal_text(line + "\n")
        assert len(entries) == 1
        # Known-op entries don't retain _raw — field is a clean None.
        assert entries[0]._raw is None

    def test_malformed_json_still_rejected(self) -> None:
        """§4.1 rule 1: JSON parse errors logged + skipped (pre-F7.1
        behavior preserved)."""
        from undo.durable_move import _parse_journal_text

        corrupt = "\n".join(
            [
                "not json at all",
                "{broken",
                json.dumps({"op": "move", "src": "/a", "dst": "/b", "state": "done"}),
            ]
        )
        entries = _parse_journal_text(corrupt + "\n")
        assert len(entries) == 1

    def test_hash16_is_stable(self) -> None:
        """``_hash16`` (§3.1 rule 4) is stable: same input → same output.
        Used for unknown-op collapse-key identity so future ops don't
        silently conflate."""
        from undo.durable_move import _hash16

        raw = '{"op":"future","op_id":"x","src":"/a","dst":"/b","state":"done"}'
        h = _hash16(raw)
        assert len(h) == 16
        assert _hash16(raw) == h  # deterministic
        # Different content → different hash.
        assert _hash16(raw.replace('"done"', '"started"')) != h


# ---------------------------------------------------------------------------
# F7.1 step 2: pure planner `plan_recovery_actions`
# (tracks #201, docs/internal/F7-1-journal-protocol-design.md §8.1, §9)
# ---------------------------------------------------------------------------


class TestPlanRecoveryActions:
    """Planner is pure: computes a list of :class:`_PlannedAction` from a
    list of :class:`_JournalEntry` + an optional ``fs_observer``, with zero
    disk mutation. Sweep's ``_apply_planned_actions`` executor performs the
    mutations. Step 2 preserves PR #197 behavior; step 3 changes the collapse
    key; step 6 adds tmp_path disambiguation.
    """

    def test_planner_is_pure_no_disk_mutation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Planning on a journal containing a COPIED entry with dst present
        on disk MUST NOT unlink src during the plan step. The planner is
        pure; mutations happen only in the executor."""
        from undo.durable_move import _JournalEntry, plan_recovery_actions

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("must not be unlinked by planner")
        dst.write_text("complete destination")

        # Instrument Path.unlink and fsync_directory — if the planner
        # touches either, the test fails.
        calls: list[tuple[str, object]] = []
        real_unlink = Path.unlink

        def tracking_unlink(self: Path, *a: object, **k: object) -> None:
            calls.append(("unlink", self))
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", tracking_unlink)
        monkeypatch.setattr(
            "undo.durable_move.fsync_directory",
            lambda p: calls.append(("fsync", p)),
        )

        entries = [
            _JournalEntry(op="move", src=str(src), dst=str(dst), state="copied"),
        ]
        plan = plan_recovery_actions(entries)

        assert calls == [], (
            f"planner must not mutate disk (§8.1 pure-planner contract); observed: {calls}"
        )
        assert len(plan) == 1
        # Planner decided "unlink src, drop entry" based on the real
        # lexists(dst) observation — but DIDN'T execute yet.
        assert plan[0].verb == "unlink_src_then_drop"
        # src is still on disk — executor hasn't run.
        assert src.read_text() == "must not be unlinked by planner"

    def test_planner_deterministic(self, tmp_path: Path) -> None:
        """Given the same inputs + fs_observer, planner returns the same plan
        twice. Required by the §8.1 CLI/sweep parity contract."""
        from undo.durable_move import _JournalEntry, plan_recovery_actions

        src = tmp_path / "s.txt"
        dst = tmp_path / "d.txt"
        src.write_text("x")
        dst.write_text("y")
        entries = [
            _JournalEntry(op="move", src=str(src), dst=str(dst), state="copied"),
            _JournalEntry(
                op="dir_move",
                src=str(tmp_path / "dir_a"),
                dst=str(tmp_path / "dir_b"),
                state="started",
            ),
            _JournalEntry(op="move", src="/x", dst="/y", state="done"),
        ]
        plan1 = plan_recovery_actions(entries)
        plan2 = plan_recovery_actions(entries)
        assert plan1 == plan2

    def test_planner_fs_observer_stub(self) -> None:
        """Planner accepts a custom ``fs_observer`` so tests can exercise
        the §5.1 recovery-state-table rows without setting up real files.
        This is also what ``fo undo recover`` uses for the "what WOULD
        sweep do" preview."""
        from undo.durable_move import _JournalEntry, plan_recovery_actions

        entries = [
            _JournalEntry(op="move", src="/a", dst="/b", state="copied"),
        ]
        # fs_observer says dst does NOT exist → §5.1 COPIED+dst-missing row → retain.
        plan = plan_recovery_actions(entries, fs_observer=lambda _p: False)
        assert len(plan) == 1
        assert plan[0].verb == "retain"

        # Same entry but fs_observer says dst exists → unlink_src_then_drop.
        plan_present = plan_recovery_actions(entries, fs_observer=lambda _p: True)
        assert plan_present[0].verb == "unlink_src_then_drop"

    def test_planner_verb_matrix(self) -> None:
        """PR #197 behavior at step 2: each (op, state, dst-present)
        combination produces the expected verb. Table mirrors the
        pre-step-6 subset of §5.1."""
        from undo.durable_move import _JournalEntry, plan_recovery_actions

        # (op, state, dst_present) -> expected verb
        cases: list[tuple[str, str, bool, str]] = [
            ("move", "started", True, "retain"),
            ("move", "started", False, "retain"),
            ("move", "copied", True, "unlink_src_then_drop"),
            ("move", "copied", False, "retain"),
            ("move", "done", True, "drop"),
            ("move", "done", False, "drop"),
            ("move", "unknown_state", True, "drop"),
            ("dir_move", "started", True, "drop"),
            ("dir_move", "done", True, "drop"),
            ("dir_move", "unknown_state", True, "drop"),
            ("future_op", "started", True, "retain"),  # unknown op retain
            ("future_op", "done", True, "retain"),  # unknown op retain
        ]
        for op, state, dst_present, expected in cases:
            entries = [_JournalEntry(op=op, src="/s", dst="/d", state=state)]
            # Bind dst_present at lambda-creation time so each iteration
            # captures its own value (B023).
            plan = plan_recovery_actions(
                entries, fs_observer=lambda _p, present=dst_present: present
            )
            assert len(plan) == 1
            assert plan[0].verb == expected, (
                f"op={op} state={state} dst_present={dst_present}: "
                f"expected verb={expected}, got {plan[0].verb}"
            )

    def test_planner_collapse_key_separates_ops(self) -> None:
        """Step 3 / codex iy4u: collapse key includes ``op`` so same paths
        in different ops cannot mask each other. The pre-fix behavior
        (PR #197 step 2) was: ``dir_move done`` would overwrite ``move
        started``, dropping the move's recovery metadata. Now both
        identities survive the collapse and produce independent plans.
        """
        from undo.durable_move import _JournalEntry, plan_recovery_actions

        entries = [
            _JournalEntry(op="move", src="/a", dst="/b", state="started"),
            _JournalEntry(op="dir_move", src="/a", dst="/b", state="started"),
            _JournalEntry(op="dir_move", src="/a", dst="/b", state="done"),
        ]
        plan = plan_recovery_actions(entries, fs_observer=lambda _p: False)
        # Two identities now: move (retained as ambiguous) + dir_move
        # (collapses to done, dropped).
        assert len(plan) == 2
        verb_by_op = {a.entry.op: a.verb for a in plan}
        assert verb_by_op["move"] == "retain"
        assert verb_by_op["dir_move"] == "drop"

    def test_planner_collapse_key_separates_v2_op_ids(self) -> None:
        """§3.1 rule 1: v2 ``(op, op_id)`` identity. Two retries of the
        same move with different op_ids stay distinct so a superseding
        retry's ``done`` cannot erase an older retained ``started``."""
        from undo.durable_move import _JournalEntry, plan_recovery_actions

        # Same paths, same op, different op_id → distinct identities.
        entries = [
            _JournalEntry(
                op="move",
                src="/a",
                dst="/b",
                state="started",
                schema=2,
                op_id="attempt-1",
                tmp_path="/a.tmp",
            ),
            _JournalEntry(
                op="move",
                src="/a",
                dst="/b",
                state="done",
                schema=2,
                op_id="attempt-2",
            ),
        ]
        plan = plan_recovery_actions(entries, fs_observer=lambda _p: False)
        assert len(plan) == 2
        # Both identities present; attempt-1's recovery metadata survives.
        op_ids = {a.entry.op_id for a in plan}
        assert op_ids == {"attempt-1", "attempt-2"}

    def test_planner_collapse_key_v2_progression_collapses_same_op_id(self) -> None:
        """§3.2: within a single op_id, states progress and collapse —
        a later ``done`` for the SAME op_id supersedes earlier
        started/copied entries (the legitimate collapse case)."""
        from undo.durable_move import _JournalEntry, plan_recovery_actions

        entries = [
            _JournalEntry(
                op="move",
                src="/a",
                dst="/b",
                state="started",
                schema=2,
                op_id="single",
                tmp_path="/a.tmp",
            ),
            _JournalEntry(
                op="move",
                src="/a",
                dst="/b",
                state="copied",
                schema=2,
                op_id="single",
            ),
            _JournalEntry(
                op="move",
                src="/a",
                dst="/b",
                state="done",
                schema=2,
                op_id="single",
            ),
        ]
        plan = plan_recovery_actions(entries, fs_observer=lambda _p: True)
        # Single identity collapses to the latest state: done → drop.
        assert len(plan) == 1
        assert plan[0].entry.state == "done"
        assert plan[0].verb == "drop"

    def test_planner_collapse_key_v1_v2_never_collide(self) -> None:
        """§3.1 ``v1`` / ``v2`` discriminator: a v1 record and a v2
        record with the same ``(op, src, dst)`` get distinct identities,
        so v2 cannot mask v1 retain metadata or vice-versa."""
        from undo.durable_move import _JournalEntry, plan_recovery_actions

        entries = [
            # v1 record — no op_id, no schema.
            _JournalEntry(op="move", src="/a", dst="/b", state="copied"),
            # v2 record — explicit op_id.
            _JournalEntry(
                op="move",
                src="/a",
                dst="/b",
                state="copied",
                schema=2,
                op_id="abc",
            ),
        ]
        plan = plan_recovery_actions(entries, fs_observer=lambda _p: True)
        assert len(plan) == 2

    def test_planner_collapse_key_unknown_op_uses_raw_hash(self) -> None:
        """§3.1 rule 4: unknown-op identity uses ``_hash16(_raw)`` so
        future ops with semantically-distinct payloads don't conflate.
        Two unknown-op entries with the same paths but different raw
        lines produce distinct identities and BOTH retain."""
        from undo.durable_move import _JournalEntry, plan_recovery_actions

        entries = [
            _JournalEntry(
                op="future_copy",
                src="/a",
                dst="/b",
                state="started",
                schema=3,
                _raw='{"schema":3,"op":"future_copy","src":"/a","dst":"/b","state":"started","content_hash":"aaa"}',
            ),
            _JournalEntry(
                op="future_copy",
                src="/a",
                dst="/b",
                state="started",
                schema=3,
                _raw='{"schema":3,"op":"future_copy","src":"/a","dst":"/b","state":"started","content_hash":"bbb"}',
            ),
        ]
        plan = plan_recovery_actions(entries, fs_observer=lambda _p: False)
        # Both records survive — _hash16 differs per raw payload, so the
        # collapse key separates them. A future binary's handler decides
        # via its own field semantics.
        assert len(plan) == 2
        for action in plan:
            assert action.verb == "retain"

    def test_executor_applies_unlink_src_then_drop(self, tmp_path: Path) -> None:
        """Executor's ``unlink_src_then_drop`` verb: unlinks src, fsyncs
        src.parent, returns empty retained list."""
        from undo.durable_move import (
            _apply_planned_actions,
            _JournalEntry,
            plan_recovery_actions,
        )

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("leftover")
        dst.write_text("complete")
        entries = [_JournalEntry(op="move", src=str(src), dst=str(dst), state="copied")]
        plan = plan_recovery_actions(entries)
        retained = _apply_planned_actions(plan)

        assert retained == []  # dropped
        assert not src.exists()  # unlinked
        assert dst.read_text() == "complete"  # untouched

    def test_executor_retain_does_not_mutate(self, tmp_path: Path) -> None:
        """Executor's ``retain`` verb: no disk mutation, entry returned
        in retained list."""
        from undo.durable_move import (
            _apply_planned_actions,
            _JournalEntry,
            plan_recovery_actions,
        )

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"  # deliberately absent → COPIED retains
        src.write_text("must survive")
        entry = _JournalEntry(op="move", src=str(src), dst=str(dst), state="copied")
        plan = plan_recovery_actions([entry])
        retained = _apply_planned_actions(plan)

        assert retained == [entry]
        assert src.read_text() == "must survive"
        assert not dst.exists()

    def test_executor_drop_does_not_mutate(self, tmp_path: Path) -> None:
        """Executor's ``drop`` verb: no disk mutation, entry dropped."""
        from undo.durable_move import (
            _apply_planned_actions,
            _JournalEntry,
            plan_recovery_actions,
        )

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("untouched")
        dst.write_text("untouched")
        entry = _JournalEntry(op="move", src=str(src), dst=str(dst), state="done")
        plan = plan_recovery_actions([entry])
        retained = _apply_planned_actions(plan)

        assert retained == []
        assert src.read_text() == "untouched"
        assert dst.read_text() == "untouched"

    def test_executor_os_error_on_unlink_retains(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Preserves PR #197 round-5 behavior: OSError during unlink_src
        retains the entry (transient permission / lock is retry-eligible).
        The PLANNER produces ``unlink_src_then_drop`` optimistically; the
        EXECUTOR downgrades to retain when the unlink raises."""
        from undo.durable_move import (
            _apply_planned_actions,
            _JournalEntry,
            plan_recovery_actions,
        )

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("x")
        dst.write_text("y")
        entry = _JournalEntry(op="move", src=str(src), dst=str(dst), state="copied")
        plan = plan_recovery_actions([entry])
        assert plan[0].verb == "unlink_src_then_drop"

        real_unlink = Path.unlink

        def failing_unlink(self: Path, *a: object, **k: object) -> None:
            if str(self) == str(src):
                raise OSError(13, "simulated permission denied")
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        retained = _apply_planned_actions(plan)
        assert retained == [entry], (
            "transient OSError on unlink must fall back to retain so the "
            "next sweep can retry (codex fwMK, PR #197 round-5)"
        )
        assert src.exists()  # unlink raised → src survives


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_journal(journal: Path) -> list[dict]:
    """Read a JSONL journal file — list of entries, one per line."""
    if not journal.exists():
        return []
    entries = []
    for line in journal.read_text().splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def _write_journal(journal: Path, entries: list[dict]) -> None:
    """Write JSONL journal entries (test helper)."""
    journal.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
