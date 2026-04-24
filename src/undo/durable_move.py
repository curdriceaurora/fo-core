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
from dataclasses import dataclass
from pathlib import Path

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
    payload = {
        "op": "move",
        "src": str(src),
        "dst": str(dst),
        "state": STATE_STARTED,
    }
    _append_journal(journal, payload)

    tmp_path: Path | None = None
    try:
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
        _fsync_directory(dst.parent)
        os.replace(tmp_path, dst)
        tmp_path = None  # successfully moved into place
    except BaseException:
        # Leave the ``started`` journal entry and clean up stray tmp
        # so operators don't accumulate .tmp debris.
        if tmp_path is not None and tmp_path.exists():
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

    Once all entries are processed the journal is truncated.

    Args:
        journal: JSONL journal from a prior :func:`durable_move` run.
            Missing or empty journal is a no-op.
    """
    journal = Path(journal)
    entries = _read_journal(journal)
    if not entries:
        return

    # Collapse to the latest state per (src, dst).
    latest: dict[tuple[str, str], _JournalEntry] = {}
    for entry in entries:
        key = (entry.src, entry.dst)
        latest[key] = entry

    for entry in latest.values():
        _complete_or_rollback(entry)

    # All done — truncate the journal. We write an empty file rather
    # than unlink so the next durable_move finds a path it can
    # append to without a race on file creation.
    journal.write_text("")


def _complete_or_rollback(entry: _JournalEntry) -> None:
    """Act on a single journal entry based on its state."""
    src = Path(entry.src)
    dst = Path(entry.dst)

    if entry.state == STATE_STARTED:
        # Destination may be partial or absent; delete it.
        try:
            dst.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(
                "sweep: failed to remove partial destination %s: %s",
                dst,
                exc,
            )
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
            )
    elif entry.state == STATE_DONE:
        # Nothing to do — operation completed before the crash (or
        # before the last journal flush).
        pass
    else:
        logger.warning("sweep: unknown journal state %r for entry %r", entry.state, entry)


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
    # Collapse to the latest state per (src, dst) — an entry may
    # have been updated across crash-recovery attempts.
    latest: dict[tuple[str, str], _JournalEntry] = {}
    for entry in entries:
        latest[(entry.src, entry.dst)] = entry
    path_str = str(path)
    for entry in latest.values():
        if entry.state == STATE_DONE:
            continue
        if entry.src == path_str or entry.dst == path_str:
            return True
    return False


def _append_journal(journal: Path, payload: dict) -> None:
    """Append one JSON line to the journal, flushing + fsyncing.

    Fsync ensures the entry is durable before the caller proceeds
    to the I/O it describes — otherwise a crash after the I/O but
    before the entry hits disk would leave the orphan invisible to
    :func:`sweep`.
    """
    journal.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload) + "\n"
    # ``open(..., "a")`` is append-mode — each write is atomic up to
    # PIPE_BUF bytes on POSIX, which is always larger than a single
    # JSON line here. Fsync both the file and the directory so both
    # the data AND the directory entry survive a power loss.
    with open(journal, "a") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    _fsync_directory(journal.parent)


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


def _fsync_directory(directory: Path) -> None:
    """Fsync a directory so its entries survive a power loss.

    On Windows, directory fsync is not supported and opening a
    directory raises — gracefully skip on non-POSIX.
    """
    if os.name == "nt":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        logger.debug("Cannot open directory for fsync: %s (%s)", directory, exc)
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        # Some filesystems (tmpfs on Linux) return ENOTSUP on
        # directory fsync. That's benign — the rename is still
        # ordered via the filesystem's journal.
        logger.debug("Directory fsync failed: %s (%s)", directory, exc)
    finally:
        os.close(fd)
