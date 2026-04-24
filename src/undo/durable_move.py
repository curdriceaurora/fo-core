"""Durable move helper for undo rollback operations.

F7 (hardening roadmap #159) — replacement for ``shutil.move`` in the
rollback path.

Contract
--------

:func:`durable_move` is:

- **Atomic on same device**: uses ``os.replace`` which is a single
  rename syscall; POSIX and Windows both guarantee either the old
  name or the new name is visible, never both + neither.
- **Durable + idempotent on cross-device (EXDEV)**: writes a journal
  entry before the copy starts, fsyncs the destination data +
  directory before marking the entry as copied, then unlinks the
  source and marks the entry as done. A crash anywhere in the
  sequence leaves the journal in a recoverable state — either the
  destination doesn't exist yet (safe to retry) or it's complete
  (just need to unlink the source).

:func:`sweep` runs at CLI startup and walks the journal to complete
or roll back any unfinished operations. Without the sweep, a crash
mid-move would leave ``.journal`` entries and possibly orphan files
on disk; with it, the invariant is "after startup, no journal
entries exist that refer to files that require action."

"Atomic" is a strong word. This helper is NOT atomic across EXDEV —
there's a window between ``os.replace`` of the copy and ``unlink``
of the source during which both files exist on disk. The journal +
sweep make that window observable and recoverable, not invisible.

Journal format
--------------

JSON lines, one operation per line. Fields:

- ``op`` — always ``"move"`` in this helper. Reserved for future
  operations (``copy``, ``symlink``, etc.) that might share the
  journal.
- ``src``, ``dst`` — absolute paths as strings.
- ``state`` — one of ``"started"``, ``"copied"``, ``"done"``.

States progress monotonically. An entry may appear multiple times
in the journal if it was updated (we append rather than rewrite for
crash safety — the sweep reads the LAST state for each (src, dst)
pair). The sweep truncates the journal once it finishes, so
steady-state size is bounded.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from utils.atomic_io import fsync_directory
from utils.atomic_write import append_durable

logger = logging.getLogger(__name__)


# Journal state constants. Exposed so callers (and tests) can assert
# against them without stringly-typed literals.
STATE_STARTED = "started"
STATE_COPIED = "copied"
STATE_DONE = "done"


@dataclass(frozen=True)
class _JournalEntry:
    """A parsed journal row."""

    op: str
    src: str
    dst: str
    state: str


def _normalized_path_str(path: Path) -> str:
    """Canonicalize *path* for journal storage + comparison.

    Uses ``os.path.abspath`` — resolves relative paths to absolute
    and collapses ``..``/``.`` segments, but does NOT follow symlinks.
    This is deliberate (codex PRRT_kwDOR_Rkws59gRpv): a symlink is a
    first-class file-system entity, and if the caller moves the
    symlink itself, ``sweep`` must unlink that symlink on recovery —
    not the target it happened to point at.

    Coderabbit PRRT_kwDOR_Rkws59fzVv: this same canonicalization
    makes equivalent paths (relative vs absolute, redundant ``..``,
    Windows case) compare as equal in :func:`is_path_in_flight`.
    """
    return os.path.abspath(os.fspath(path))


def durable_move(src: Path, dst: Path, *, journal: Path) -> None:
    """Move *src* to *dst* atomically (same device) or durably (EXDEV).

    Args:
        src: Source path. Must exist as a regular file or the call
            raises ``FileNotFoundError``. Symlinks are followed.
        dst: Destination path. Parent directories are created if
            missing (matches ``shutil.move`` semantics callers relied
            on pre-F7). If *dst* already exists it is overwritten —
            same as the pre-F7 ``shutil.move`` behavior.
        journal: JSONL journal file for cross-device operations. May
            not exist yet; the file is created on first EXDEV write.
            For same-device moves (the fast path) the journal is
            untouched — no crash recovery is needed because the move
            is one syscall.

    Raises:
        FileNotFoundError: If *src* doesn't exist.
        OSError: Any other OS-level failure (permission denied, disk
            full, etc.). The journal will contain a ``started`` or
            ``copied`` entry for the interrupted operation;
            :func:`sweep` on next startup cleans up.
    """
    src = Path(src)
    dst = Path(dst)
    journal = Path(journal)

    if not src.exists() and not src.is_symlink():
        raise FileNotFoundError(f"Source does not exist: {src}")
    # Coderabbit PRRT_kwDOR_Rkws59fzVo: reject directories explicitly.
    # The same-device ``os.replace`` path happily renames directories
    # but the EXDEV ``shutil.copyfile`` path raises ``IsADirectoryError``
    # AFTER writing the ``started`` journal entry, stranding the journal
    # in a state sweep can't recover from. Surface the contract
    # violation up front with ``IsADirectoryError`` so callers can
    # handle it without digging through the journal.
    if src.is_dir() and not src.is_symlink():
        raise IsADirectoryError(f"durable_move only supports regular files; {src} is a directory")

    dst.parent.mkdir(parents=True, exist_ok=True)

    # Fast path: same-device rename. ``os.replace`` is atomic on
    # POSIX + Windows for same-filesystem renames. We only fall
    # through to the durable EXDEV path when the rename fails with
    # EXDEV; any other error surfaces to the caller unchanged.
    try:
        os.replace(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _durable_cross_device_move(src, dst, journal=journal)


def _durable_cross_device_move(src: Path, dst: Path, *, journal: Path) -> None:
    """EXDEV branch: copy + fsync + os.replace + unlink, with journal.

    Sequence is carefully ordered so a crash at any point leaves
    recoverable state:

    1. Append ``started`` entry. Crash here = no copy yet; sweep
       deletes (never-created or partial) destination.
    2. Copy ``src`` → ``<dst>.tmp`` inside ``dst.parent`` (same-fs
       as ``dst``). Copy errors propagate — the journal has an
       unresolved ``started`` entry that sweep cleans up.
    3. Fsync the temp file and its directory. Without the directory
       fsync, a power loss after ``os.replace`` could leave the
       directory entry pointing at an inode whose pages haven't hit
       disk — a classic atomic-write footgun.
    4. ``os.replace(tmp, dst)``. Atomic because tmp + dst are on the
       same filesystem (dst.parent).
    5. Append ``copied`` entry. Crash here = destination complete,
       source still exists; sweep unlinks the source.
    6. Unlink source. Crash here = destination complete, source
       gone; state is effectively ``done`` but no ``done`` entry is
       logged yet. Sweep would redundantly try to unlink a
       nonexistent source, which is a no-op.
    7. Append ``done`` entry for audit/observability.
    """
    # Resolve to absolute paths before journaling. ``is_path_in_flight``
    # will match on ``str()`` of the resolved form, so callers passing
    # unresolved / relative / symlinked paths still get the right
    # match (coderabbit PRRT_kwDOR_Rkws59fzVv).
    payload = {
        "op": "move",
        "src": _normalized_path_str(src),
        "dst": _normalized_path_str(dst),
        "state": STATE_STARTED,
    }
    _append_journal(journal, payload)

    tmp_path: Path | None = None
    try:
        if src.is_symlink():
            # Codex P1 PRRT_kwDOR_Rkws59gnab: preserve symlink identity
            # on EXDEV moves. ``shutil.copyfile`` follows symlinks and
            # would replace ``dst`` with a regular file containing the
            # target's bytes — breaking rollback fidelity for symlink
            # trash entries and destroying dangling symlinks entirely
            # (shutil.copyfile on a dangling symlink raises
            # ``FileNotFoundError``). Replicates ``shutil.move``'s
            # pre-F7 symlink handling: readlink → create new symlink
            # at a tmp path → os.replace → unlink the original.
            # ``readlink`` preserves absolute-vs-relative target form.
            target = os.readlink(src)
            tmp_path = dst.parent / f".{dst.name}.{os.getpid()}.symlink.tmp"
            # Clean any stale tmp from a prior crashed attempt at the
            # exact same path. The PID suffix makes collisions between
            # concurrent processes impossible; the only realistic way
            # the tmp exists is that a prior invocation with the same
            # PID crashed mid-symlink (before the ``os.replace``).
            # An ``OSError`` here surfaces to the outer handler which
            # logs + leaves the journal's ``started`` entry for sweep.
            if os.path.lexists(tmp_path):
                tmp_path.unlink()
            os.symlink(target, tmp_path)
            # No file data to fsync for a symlink — the target string
            # lives in the inode itself on most filesystems — but the
            # directory entry DOES need to be fsynced before the
            # rename (parity with the regular-file branch below).
            fsync_directory(dst)
            os.replace(tmp_path, dst)
            fsync_directory(dst)
            tmp_path = None
        else:
            # Copy into a temp in dst's parent so the final rename is
            # same-filesystem. ``NamedTemporaryFile(delete=False)`` keeps
            # the file on disk for the os.replace step.
            with tempfile.NamedTemporaryFile(
                dir=str(dst.parent),
                prefix=f".{dst.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
            shutil.copyfile(src, tmp_path)
            # Preserve mode bits so daemons that rely on chmod survive
            # the copy (matches pre-F7 ``shutil.move`` behavior on
            # cross-device moves, which uses copy2 internally).
            try:
                shutil.copystat(src, tmp_path)
            except OSError:
                # copystat failures are non-fatal — better to complete
                # the move with default mode than fail.
                logger.debug(
                    "copystat failed for %s → %s; proceeding with default mode",
                    src,
                    tmp_path,
                    exc_info=True,
                )
            # fsync file + parent dir before the rename so a power loss
            # after ``os.replace`` can't leave the new directory entry
            # pointing at an inode with unflushed pages.
            fd = os.open(tmp_path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            # ``fsync_directory(dst)`` fsyncs ``dst.parent`` — see
            # ``utils.atomic_io.fsync_directory`` docstring. Same effect
            # as an explicit ``_fsync_directory(dst.parent)`` but reuses
            # the shared helper.
            fsync_directory(dst)
            os.replace(tmp_path, dst)
            # Codex P1 PRRT_kwDOR_Rkws59fwMG: fsync ``dst.parent`` AGAIN
            # after the rename so the new directory entry is durable
            # before we log ``copied`` and unlink the source. Without
            # this second fsync, a crash between ``os.replace`` and the
            # next journal append could leave the journal claiming
            # ``copied`` while the rename itself hadn't reached disk —
            # sweep would then unlink the (recoverable) source while
            # the destination directory entry had rolled back.
            fsync_directory(dst)
            tmp_path = None  # successfully moved into place
    except BaseException:
        # Leave the ``started`` journal entry and clean up stray tmp
        # so operators don't accumulate .tmp debris. Use ``lexists``
        # so a dangling-symlink tmp (target doesn't exist) still gets
        # removed — ``Path.exists()`` would return False for those.
        if tmp_path is not None and os.path.lexists(tmp_path):
            try:
                tmp_path.unlink()
            except OSError:
                logger.debug("Failed to clean up stray temp file %s", tmp_path, exc_info=True)
        raise

    # Destination is complete. Log the copied state BEFORE unlinking
    # the source so a crash between these two steps is recoverable
    # by sweep (it sees ``copied`` and unlinks the source).
    _append_journal(journal, {**payload, "state": STATE_COPIED})

    try:
        os.unlink(src)
    except FileNotFoundError:
        # Source already gone — treat as done.
        pass

    # Codex P2 PRRT_kwDOR_Rkws59gnah: fsync ``src.parent`` so the
    # unlink itself is crash-durable before we log ``done``. On POSIX
    # an ``os.unlink`` is not persisted until the containing directory
    # is fsynced; without this call, a power loss here could let ``src``
    # reappear on reboot while the journal already records ``done``.
    # ``sweep()`` would then drop the entry and never reconcile the
    # resurrected file, leaving a phantom copy on disk.
    fsync_directory(src)

    _append_journal(journal, {**payload, "state": STATE_DONE})


def sweep(journal: Path) -> None:
    """Complete or roll back interrupted :func:`durable_move` ops.

    Walks the journal, collapses entries to the latest state per
    (src, dst) pair, and acts:

    - ``started``: crash before destination was ready. Delete any
      partial destination, leave source intact.
    - ``copied``: crash after destination was written but before
      source was cleaned up. Unlink source (destination is already
      in place).
    - ``done``: operation already completed; drop the entry.

    Reconciled entries are dropped from the journal; entries whose
    cleanup raised ``OSError`` (transient permission/lock issues) are
    retained so the next startup can retry. Without that retention,
    a single failed sweep would strand the operation forever (codex
    P1 PRRT_kwDOR_Rkws59fwMK).

    Args:
        journal: JSONL journal from a prior :func:`durable_move` run.
            Missing or empty journal is a no-op.
    """
    journal = Path(journal)
    if not journal.exists():
        return

    # Coderabbit PRRT_kwDOR_Rkws59fzVp: hold an exclusive
    # ``fcntl.flock`` on the journal for the whole read-modify-write.
    # Without it, a concurrent ``fo`` invocation that appends a
    # ``started`` entry between the read and the rewrite would have
    # its entry silently wiped by the truncate. ``fcntl.flock`` is
    # POSIX-only; on Windows we fall back to the pre-lock read-modify-
    # write path and rely on the "single CLI invocation" invariant
    # documented at the module level.
    if os.name != "nt":
        try:
            import fcntl

            with open(journal, "r+") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    _sweep_locked_body(journal, fh)
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return
        except (ImportError, OSError):
            # ``ImportError``: fcntl unavailable (shouldn't happen on
            # POSIX but defensive). ``OSError`` on open can happen if
            # the journal disappeared between ``exists`` and
            # ``open`` — fall through to the unlocked path which
            # handles the missing-file case.
            pass
    _sweep_unlocked_body(journal)


def _sweep_locked_body(journal: Path, fh) -> None:  # type: ignore[no-untyped-def]
    """Sweep body executed while holding an exclusive flock on ``fh``."""
    fh.seek(0)
    entries = _parse_journal_text(fh.read())
    if not entries:
        return
    retained = _reconcile_entries(entries)
    fh.seek(0)
    fh.truncate()
    if retained:
        fh.write("\n".join(_serialize_entry(e) for e in retained) + "\n")
    fh.flush()
    os.fsync(fh.fileno())


def _sweep_unlocked_body(journal: Path) -> None:
    """Sweep body for Windows / environments without ``fcntl``.

    Best-effort — callers rely on single-invocation serialization
    for crash safety; see the module docstring.
    """
    entries = _read_journal(journal)
    if not entries:
        return
    retained = _reconcile_entries(entries)
    if retained:
        lines = [_serialize_entry(e) for e in retained]
        journal.write_text("\n".join(lines) + "\n")
    else:
        journal.write_text("")


def _reconcile_entries(entries: list[_JournalEntry]) -> list[_JournalEntry]:
    """Collapse entries to latest state per (src, dst) and reconcile.

    Returns the subset of entries that still need retry after
    ``_complete_or_rollback`` — i.e. the ones whose cleanup raised
    a transient ``OSError``.
    """
    latest: dict[tuple[str, str], _JournalEntry] = {}
    for entry in entries:
        latest[(entry.src, entry.dst)] = entry
    retained: list[_JournalEntry] = []
    for entry in latest.values():
        if not _complete_or_rollback(entry):
            retained.append(entry)
    return retained


def _serialize_entry(e: _JournalEntry) -> str:
    """JSON-serialize a journal entry for write-back."""
    return json.dumps({"op": e.op, "src": e.src, "dst": e.dst, "state": e.state})


def _parse_journal_text(text: str) -> list[_JournalEntry]:
    """Parse JSONL journal text into typed entries.

    Mirrors :func:`_read_journal` but works on an already-read string
    so ``_sweep_locked_body`` can use the locked fd's read.
    """
    entries: list[_JournalEntry] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("sweep: dropping unparsable journal line: %r", line)
            continue
        op = data.get("op")
        src = data.get("src")
        dst = data.get("dst")
        state = data.get("state")
        if not (
            isinstance(op, str)
            and isinstance(src, str)
            and isinstance(dst, str)
            and isinstance(state, str)
        ):
            logger.warning("sweep: dropping malformed journal entry: %r", data)
            continue
        entries.append(_JournalEntry(op=op, src=src, dst=dst, state=state))
    return entries


def _complete_or_rollback(entry: _JournalEntry) -> bool:
    """Act on a single journal entry based on its state.

    Returns:
        ``True`` if the entry was successfully reconciled (or was
        already ``done``) and can be dropped from the journal.
        ``False`` if a transient error (usually ``OSError``) left the
        state unresolved — caller must retain the entry so the next
        sweep can retry.
    """
    src = Path(entry.src)
    dst = Path(entry.dst)

    if entry.state == STATE_STARTED:
        # Codex P1 PRRT_kwDOR_Rkws59gbdD + PRRT_kwDOR_Rkws59g2Ex:
        # ``started`` is an AMBIGUOUS state, not a "move never
        # committed" state. The EXDEV path logs ``started`` before
        # the copy and does NOT log ``copied`` until AFTER
        # ``os.replace``, so a crash anywhere in this window leaves
        # a ``started`` record with one of three possible on-disk
        # realities:
        #
        #   (a) crash before os.replace: src intact, dst pristine
        #       (pre-existing file OR absent). Retry is safe.
        #   (b) crash during or after os.replace but before the
        #       ``copied`` log fsyncs: dst has the NEW content, src
        #       still exists. A naive retry would double-copy; the
        #       correct recovery is "unlink src" (i.e. complete the
        #       ``copied`` semantics).
        #   (c) same as (b) but os.replace was atomic and the tmp
        #       already got cleaned — indistinguishable from (b)
        #       without content comparison.
        #
        # We cannot safely disambiguate (a) from (b) without
        # introducing a new journal state or storing tmp_path in the
        # record (future F7.1). For now the SAFE reconciliation is:
        # do NOT unlink dst (which would destroy a legitimate
        # pre-existing file in case (a)) and do NOT unlink src
        # (which would cause data loss in case (a) where dst was
        # never replaced), and RETAIN the entry so the next sweep /
        # operator sees it. Dropping the entry would lose all retry
        # metadata and potentially leave both src+dst on disk forever.
        logger.warning(
            "sweep: retaining ambiguous started entry %s -> %s "
            "(crash occurred in the copy→replace window; cannot safely "
            "reconcile without content comparison — operator or retry "
            "must resolve)",
            src,
            dst,
        )
        return False  # retain for next sweep / manual reconciliation
    elif entry.state == STATE_COPIED:
        # Destination is complete; finish by removing source.
        try:
            src.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(
                "sweep: failed to unlink source after copied state %s: %s",
                src,
                exc,
                exc_info=True,
            )
            return False
    elif entry.state == STATE_DONE:
        # Nothing to do — operation completed before the crash (or
        # before the last journal flush).
        pass
    else:
        logger.warning("sweep: unknown journal state %r for entry %r", entry.state, entry)
        # Unknown states are dropped rather than retained — retrying
        # an unrecognized entry won't change anything.
    return True


def is_path_in_flight(path: Path, *, journal: Path) -> bool:
    """Return True iff *path* is the src or dst of an uncompleted move.

    F8 (hardening roadmap #159): exposes the durable_move journal as
    a coordination point for concurrent access to paths. Any consumer
    that wants to delete or mutate a file that might simultaneously
    be the subject of an in-flight :func:`durable_move` (trash GC,
    dedup cleanup, manual ``rm``) should call this first and skip the
    path if True.

    "In-flight" means the journal's latest state entry for the
    (src, dst) pair is ``started`` or ``copied`` — neither ``done``
    nor absent. An operation in those states leaves files on disk
    whose lifecycle the sweep will complete on next startup;
    deleting one of them out from under the sweep would strand the
    operation permanently.

    Args:
        path: Absolute path to check.
        journal: Same journal used by the matching ``durable_move``
            calls. Missing/empty journal → ``False`` (nothing in
            flight).
    """
    entries = _read_journal(journal)
    if not entries:
        return False
    # Normalize the query path the same way writers do so the compare
    # works on equivalent paths (coderabbit PRRT_kwDOR_Rkws59fzVv).
    path_str = _normalized_path_str(path)
    # Collapse to the latest state per (src, dst) — an entry may
    # have been updated across crash-recovery attempts.
    latest: dict[tuple[str, str], _JournalEntry] = {}
    for entry in entries:
        latest[(entry.src, entry.dst)] = entry
    for entry in latest.values():
        if entry.state == STATE_DONE:
            continue
        if entry.src == path_str or entry.dst == path_str:
            return True
    return False


def _append_journal(journal: Path, payload: Mapping[str, object]) -> None:
    """Append one JSON line to the journal with flock + fsync durability.

    Codex P1 PRRT_kwDOR_Rkws59gbdH: :func:`sweep` holds an exclusive
    ``fcntl.flock`` on the journal for the entire read-modify-truncate
    cycle. This helper MUST acquire the same advisory lock before
    writing — otherwise a concurrent writer can append a ``started``
    entry between sweep's read and its truncate, and sweep will then
    silently wipe that record. If the writer then crashes mid-move,
    recovery metadata is lost and the orphan files on disk have no
    journal entry sweep can use to reconcile them on the next startup.

    POSIX path: open the journal in append mode, acquire ``LOCK_EX``
    on the descriptor, write + flush + fsync the data, fsync the
    parent directory, then release the lock. ``O_APPEND`` makes the
    offset advance atomically, but the lock (not the offset) is what
    serializes us with sweep's truncate.

    Non-POSIX fallback: :func:`utils.atomic_write.append_durable`.
    Crash-safety relies on the module-docstring "single CLI
    invocation" invariant; Windows sweep uses the unlocked body too.
    """
    journal.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            import fcntl
        except ImportError:  # pragma: no cover - POSIX ships fcntl
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            line = json.dumps(payload) + "\n"
            with open(journal, "a", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fsync_directory(journal)
            return
    append_durable(journal, json.dumps(payload))


def _read_journal(journal: Path) -> list[_JournalEntry]:
    """Parse the JSONL journal into typed entries. Missing/empty → []."""
    if not journal.exists():
        return []
    entries: list[_JournalEntry] = []
    for line in journal.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("sweep: dropping unparsable journal line: %r", line)
            continue
        # Only ``op: "move"`` entries are produced today; future ops
        # can land in the same journal without disturbing callers.
        op = data.get("op")
        src = data.get("src")
        dst = data.get("dst")
        state = data.get("state")
        if not (
            isinstance(op, str)
            and isinstance(src, str)
            and isinstance(dst, str)
            and isinstance(state, str)
        ):
            logger.warning("sweep: dropping malformed journal entry: %r", data)
            continue
        entries.append(_JournalEntry(op=op, src=src, dst=dst, state=state))
    return entries
