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

    def test_sweep_rollback_started_state(self, tmp_path: Path) -> None:
        """State ``started`` = crash before the copy finished. The
        destination may be partial/absent; delete it and drop the
        journal entry. Source is untouched.
        """
        from undo.durable_move import sweep

        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("intact source")
        dst.write_text("partial garbage from crashed copy")
        journal = tmp_path / "move.journal"
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "started"}],
        )

        sweep(journal)

        # Destination cleaned up; source preserved.
        assert not dst.exists(), "started-state dst must be removed on sweep"
        assert src.read_text() == "intact source"
        # Journal is cleared after successful sweep.
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
        # unlink (started, dst we'll make unremovable via monkeypatch).
        journal = tmp_path / "move.journal"
        good_src = tmp_path / "good-src.txt"
        good_dst = tmp_path / "good-dst.txt"
        bad_src = tmp_path / "bad-src.txt"
        bad_dst = tmp_path / "bad-dst.txt"
        bad_dst.write_text("partial")
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
                    "state": "started",
                },
            ],
        )

        real_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if str(self) == str(bad_dst):
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

    def test_sweep_unlocked_body_rollback_started(self, tmp_path: Path) -> None:
        """``_sweep_unlocked_body`` (Windows/no-fcntl fallback) has
        the same rollback contract as the locked path. Exercised
        directly since the real platform gate uses ``os.name`` at
        module level and can't be cleanly monkeypatched from tests
        (the module captures ``os`` at import time for the gate
        check).
        """
        from undo.durable_move import _sweep_unlocked_body

        journal = tmp_path / "move.journal"
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("intact")
        dst.write_text("partial")
        _write_journal(
            journal,
            [{"op": "move", "src": str(src), "dst": str(dst), "state": "started"}],
        )

        _sweep_unlocked_body(journal)

        assert not dst.exists()
        assert src.read_text() == "intact"
        assert _read_journal(journal) == []

    def test_sweep_unlocked_body_retains_failed_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_sweep_unlocked_body`` retains failed entries for retry
        (matches the POSIX-locked path contract)."""
        from undo.durable_move import _sweep_unlocked_body

        journal = tmp_path / "move.journal"
        bad_src = tmp_path / "bad.txt"
        bad_dst = tmp_path / "bad-dst.txt"
        bad_dst.write_text("x")
        _write_journal(
            journal,
            [{"op": "move", "src": str(bad_src), "dst": str(bad_dst), "state": "started"}],
        )

        real_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if str(self) == str(bad_dst):
                raise OSError(13, "simulated")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        _sweep_unlocked_body(journal)

        entries = _read_journal(journal)
        assert any(e["src"] == str(bad_src) for e in entries)

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
