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

    def test_sweep_started_state_preserves_both_paths(self, tmp_path: Path) -> None:
        """Codex P1 PRRT_kwDOR_Rkws59gbdD: state ``started`` = crash
        BEFORE the EXDEV copy reached ``os.replace``. ``dst`` has not
        been written by our transaction, so it may still be a
        legitimate pre-existing file (or absent). Sweep MUST NOT
        unlink it — doing so was a data-loss path during crash
        recovery. ``src`` is the live copy and is also untouched.
        The journal entry is dropped after logging.
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

        # Both paths preserved unchanged — the move never committed.
        assert src.read_text() == "intact source"
        assert dst.read_text() == "legitimate pre-existing destination", (
            "started-state sweep must not destroy a legitimate dst; "
            "this was the codex P1 data-loss path"
        )
        # Journal cleared after sweep (no retry needed for this state).
        assert _read_journal(journal) == []

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
        """Sweeping twice is safe — the journal is empty after the
        first pass."""
        from undo.durable_move import sweep

        journal = tmp_path / "move.journal"
        src = tmp_path / "src.txt"
        src.write_text("x")
        dst = tmp_path / "dst.txt"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "started"}],
        )

        sweep(journal)
        sweep(journal)  # must not raise

    def test_sweep_tolerates_missing_files(self, tmp_path: Path) -> None:
        """A journal entry for paths that no longer exist on disk
        (e.g. operator deleted them manually) doesn't crash the
        sweep — sweep is best-effort cleanup."""
        from undo.durable_move import sweep

        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [
                {
                    "op": "move",
                    "src": str(tmp_path / "gone_src.txt"),
                    "dst": str(tmp_path / "gone_dst.txt"),
                    "state": "started",
                }
            ],
        )

        sweep(journal)  # must not raise


# ---------------------------------------------------------------------------
# Atomic failure modes
# ---------------------------------------------------------------------------


class TestDurableMoveFailureModes:
    """Failure modes: missing source, permission errors, crash
    during copy, etc."""

    def test_sweep_unlocked_body_started_preserves_dst(self, tmp_path: Path) -> None:
        """``_sweep_unlocked_body`` (Windows/no-fcntl fallback) shares
        the same started-state contract as the locked path: dst is
        NEVER unlinked on ``started`` (codex P1 PRRT_kwDOR_Rkws59gbdD).
        Exercised directly since the real platform gate uses
        ``os.name`` at module level and can't be cleanly monkeypatched
        from tests (the module captures ``os`` at import time for the
        gate check).
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

        # Both paths preserved; journal cleared.
        assert src.read_text() == "intact"
        assert dst.read_text() == "legitimate pre-existing"
        assert _read_journal(journal) == []

    def test_sweep_unlocked_body_retains_failed_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_sweep_unlocked_body`` retains failed entries for retry
        (matches the POSIX-locked path contract). Uses a ``copied``
        entry since started-state reconciliation no longer unlinks
        anything (codex P1 PRRT_kwDOR_Rkws59gbdD).
        """
        from undo.durable_move import _sweep_unlocked_body

        journal = tmp_path / "move.journal"
        bad_src = tmp_path / "bad-src.txt"
        bad_dst = tmp_path / "bad-dst.txt"
        bad_src.write_text("x")
        _write_journal(
            journal,
            [{"op": "move", "src": str(bad_src), "dst": str(bad_dst), "state": "copied"}],
        )

        real_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if str(self) == str(bad_src):
                raise OSError(13, "simulated")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        _sweep_unlocked_body(journal)

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
        result = _normalized_path_str(Path("./sub/../target.txt"))
        assert result == str(tmp_path / "target.txt"), result

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
        shape, are logged + dropped (not treated as valid entries)."""
        from undo.durable_move import _read_journal

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
        entries = _read_journal(journal)
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
        """If dst never existed (common case), sweep still drops the
        started entry cleanly — no FileNotFoundError surfacing."""
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
        assert _read_journal(journal) == []


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

        append_done = threading.Event()
        append_error: list[BaseException] = []

        def _appender() -> None:
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

        # Without flock coordination the appender would finish in
        # microseconds. With flock, it blocks on LOCK_EX while the
        # holder still has it.
        assert not append_done.wait(timeout=0.6), (
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
