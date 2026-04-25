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
import hashlib
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from utils.atomic_io import fsync_directory
from utils.atomic_write import append_durable

logger = logging.getLogger(__name__)


# Journal state constants. Exposed so callers (and tests) can assert
# against them without stringly-typed literals.
STATE_STARTED = "started"
STATE_COPIED = "copied"
STATE_DONE = "done"

# Journal op constants. ``move`` is the F7 atomic/durable single-file
# helper. ``dir_move`` (round-10 / coderabbit r3140-class) is the
# non-atomic shutil.move-based helper for directories — sweep cannot
# recover from a crash here, but the journal entry exists so concurrent
# :func:`is_path_in_flight` callers (F8 trash GC) see the directory as
# in-flight during the move.
OP_MOVE = "move"
OP_DIR_MOVE = "dir_move"
_KNOWN_OPS = frozenset({OP_MOVE, OP_DIR_MOVE})


_MAX_JOURNAL_LINE_BYTES = 64 * 1024
"""§4.1 rule 7: cap on a single journal line in bytes. Lines above this are
rejected at parse time to prevent pathological payload-size attacks. 64 KiB
leaves ample headroom above realistic path lengths plus the v2 envelope."""


def _hash16(raw: str) -> str:
    """Return the first 16 hex chars of ``sha256(raw)``.

    §3.1 rule 4: used for unknown-op collapse-key identity so semantically-
    distinct future records don't silently conflate when collapsed by this
    binary's v2 parser (which doesn't understand their additional fields).
    16 hex = 64 bits of identity — ample for the per-journal record counts
    the protocol expects (≤ dozens).
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class _JournalEntry:
    """A parsed journal row.

    F7.1 schema v2 (see ``docs/internal/F7-1-journal-protocol-design.md`` §2):

    - ``schema``: 1 for legacy PR #197 records (no ``schema`` field on disk),
      2 for records this binary writes. Writers always emit 2; parsers accept
      both for back-compat.
    - ``op_id``: unique per-invocation UUID (v2 only). ``None`` on v1 records.
      v2 known-op records MUST carry ``op_id`` — parse-time rejected otherwise
      per §4.1 rule 8.
    - ``tmp_path``: absolute path of the EXDEV copy's tmp file/symlink. Only
      populated on v2 ``op=move state=started`` records; ``None`` elsewhere.
      The §7.1 tmp-exists invariant requires it on every such record —
      parse-time rejected otherwise per §4.1 rule 9.
    - ``ts``/``host_pid``: diagnostic metadata written by v2 writers, never
      consulted by the protocol itself (PID reuse makes ``host_pid`` unsafe
      for liveness checks per F2).
    - ``_raw``: for unknown-op records ONLY — the full JSON line as read,
      preserved so compaction re-serializes verbatim (§4.2). Known-op entries
      keep ``_raw = None``; the serializer reconstructs from fields.
    """

    op: str
    src: str
    dst: str
    state: str
    schema: int = 1
    op_id: str | None = None
    tmp_path: str | None = None
    ts: float | None = None
    host_pid: int | None = None
    _raw: str | None = None


def _normalized_path_str(path: Path) -> str:
    r"""Canonicalize *path* for journal storage + comparison.

    Uses ``os.path.abspath`` — resolves relative paths to absolute
    and collapses ``..``/``.`` segments, but does NOT follow symlinks.
    This is deliberate (codex PRRT_kwDOR_Rkws59gRpv): a symlink is a
    first-class file-system entity, and if the caller moves the
    symlink itself, ``sweep`` must unlink that symlink on recovery —
    not the target it happened to point at.

    Wrapped in ``os.path.normcase`` so Windows case-insensitive
    paths compare correctly (codex PRRT_kwDOR_Rkws59hp2G): without
    this, ``C:\foo`` and ``c:\foo`` are the same file but different
    journal strings, so :func:`is_path_in_flight` would miss
    in-flight entries and trash GC could delete a path that an
    active rollback move depended on. ``normcase`` is a no-op on
    POSIX (where the case + separator forms are already canonical),
    so this change has no observable effect on Linux/macOS.

    Coderabbit PRRT_kwDOR_Rkws59fzVv: the same canonicalization
    makes equivalent paths (relative vs absolute, redundant ``..``,
    Windows case) compare as equal in :func:`is_path_in_flight`.
    """
    return os.path.normcase(os.path.abspath(os.fspath(path)))


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


def directory_move(src: Path, dst: Path, *, journal: Path) -> None:
    """Non-atomic directory move with journal coordination for F8.

    Coderabbit (round-10): ``RollbackExecutor._move()`` previously
    called ``shutil.move`` directly for directories, bypassing the
    durable_move journal entirely. That left a window where
    concurrent trash GC (via :func:`is_path_in_flight`) saw no
    in-flight entry for the directory and could delete the trash
    path mid-restore — exactly the F8 race ``durable_move`` was
    introduced to prevent.

    This helper writes ``op="dir_move"`` started/done entries
    around a non-atomic ``shutil.move``. The entry exists ONLY for
    coordination, NOT for crash recovery — directory moves are
    not idempotent, so a crash mid-shutil.move leaves operator-
    inspectable on-disk state. Sweep drops ``dir_move`` entries
    (with a warning if state != done) since it cannot safely
    re-run the move.

    Args:
        src: Source directory. Must exist as a directory; symlinks
            should route through :func:`durable_move` instead since
            the symlink itself is a single inode.
        dst: Destination directory path. Parent is created if absent.
        journal: Same journal used by :func:`durable_move` calls so
            :func:`is_path_in_flight` sees a unified view.
    """
    src = Path(src)
    dst = Path(dst)
    payload = {
        "op": OP_DIR_MOVE,
        "src": _normalized_path_str(src),
        "dst": _normalized_path_str(dst),
        "state": STATE_STARTED,
    }
    _append_journal(journal, payload)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    finally:
        # ``done`` is appended even if shutil.move raised, so a
        # caught exception still clears the in-flight marker for
        # GC. If shutil.move partially completed before raising,
        # the operator must reconcile on-disk state — there's no
        # safe automated recovery for directory moves.
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
    """Sweep body executed while holding an exclusive flock on ``fh``.

    The ``journal`` path is carried through for diagnostic logging
    (coderabbit PRRT_kwDOR_Rkws59hgCS) — it parallels the
    ``_sweep_unlocked_body(journal)`` signature and gives debug log
    lines the journal location when the sweep retains entries.
    """
    fh.seek(0)
    entries = _parse_journal_text(fh.read())
    if not entries:
        return
    retained = _reconcile_entries(entries)
    fh.seek(0)
    fh.truncate()
    if retained:
        fh.write("\n".join(_serialize_entry(e) for e in retained) + "\n")
        logger.debug(
            "sweep: retained %d unreconciled entries in %s",
            len(retained),
            journal,
        )
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


_PlannedVerb = Literal[
    "drop",
    "retain",
    "unlink_src_then_drop",
]
"""Step 2: closed verb set per §5.1 PR #197 subset. Step 6 will add
``drop_tmp_then_drop`` for the v2 STARTED-with-tmp_path-present row."""


@dataclass(frozen=True)
class _PlannedAction:
    """A single sweep decision + the entry it applies to.

    Produced by :func:`plan_recovery_actions` (pure, no mutation), executed
    by :func:`_apply_planned_actions`. Round-3 spec §8.1: keeping these
    separate means ``fo undo recover`` and sweep agree on what would
    happen — the CLI calls the planner and renders; sweep calls the
    planner and executes. No "report-only mode" branch can drift.
    """

    identity: tuple
    entry: _JournalEntry
    verb: _PlannedVerb
    reason: str
    log_level: int = logging.DEBUG


def _identity(entry: _JournalEntry) -> tuple:
    """Operation identity for collapse-key reduction (§3.1).

    Three rules in order:

    1. v2 known-op record (``schema == 2``, op ∈ ``_KNOWN_OPS``,
       ``op_id`` present): identity is ``("v2", op, op_id)`` — a single
       invocation; different retries of the same move stay distinct.
       The parser enforces ``op_id`` presence (§4.1 rule 8) so a v2
       known-op entry without ``op_id`` never reaches this point.
    2. v1 known-op record (no ``schema`` field on disk): identity is
       ``("v1", op, src, dst)`` — path-keyed fallback matching PR #197
       behavior. The ``"v1"`` discriminator means a v1 record cannot
       collapse with a v2 record sharing the same ``(op, src, dst)``,
       so v2's stronger identity is never silently downgraded.
    3. Unknown op: identity is ``("unknown", op, _hash16(_raw))`` —
       hashed from the full raw line so semantically-distinct future
       records (with fields our parser doesn't understand) don't
       conflate. The parser captures ``_raw`` for unknown ops via
       §4.2; a defensive ``or ""`` covers the ``_raw is None`` case
       a hand-constructed test entry might create.

    Closes codex iy4u (same-path different-op masking) per #201.
    """
    if entry.op in _KNOWN_OPS:
        if entry.schema == 2 and entry.op_id is not None:
            return ("v2", entry.op, entry.op_id)
        return ("v1", entry.op, entry.src, entry.dst)
    return ("unknown", entry.op, _hash16(entry._raw or ""))


def plan_recovery_actions(
    entries: list[_JournalEntry],
    fs_observer: Callable[[str], bool] = os.path.lexists,
) -> list[_PlannedAction]:
    """Pure planner: decide what sweep would do for each retained entry.

    Round-3 design §8.1: this function performs ZERO disk mutation. It
    collapses ``entries`` by the §3.1 identity (step 2 uses PR #197's
    ``(src, dst)``; step 3 will swap in op_id-aware identity), observes
    on-disk state via ``fs_observer`` (defaults to ``os.path.lexists``;
    tests stub for deterministic table coverage), and returns a list of
    :class:`_PlannedAction` that :func:`_apply_planned_actions` then
    executes.

    Both sweep and ``fo undo recover`` (step 8) call this function with
    the same inputs — sweep proceeds to the executor, the CLI just
    renders. That keeps "what sweep would do" and "what the CLI shows"
    bit-identical.

    Args:
        entries: parsed journal entries (from
            :func:`_parse_journal_text` or :func:`_read_journal`).
        fs_observer: callable taking a path string and returning whether
            the path exists. Defaults to ``os.path.lexists`` so dangling
            symlinks count as "present" (matches PR #197 round-6 codex
            hGWW guard).

    Returns:
        One :class:`_PlannedAction` per collapsed identity, in iteration
        order. Verb selection mirrors PR #197 step-2 behavior; the §5.1
        recovery state table covers the full row matrix that step 6 +
        future steps will fill in.
    """
    latest: dict[tuple, _JournalEntry] = {}
    for entry in entries:
        latest[_identity(entry)] = entry
    plan: list[_PlannedAction] = []
    for identity, entry in latest.items():
        plan.append(_plan_one(identity, entry, fs_observer))
    return plan


def _plan_one(
    identity: tuple,
    entry: _JournalEntry,
    fs_observer: Callable[[str], bool],
) -> _PlannedAction:
    """Decision matrix for a single collapsed entry.

    PR #197 step-2 subset of §5.1. Step 6 will extend the ``move started``
    row to consult ``fs_observer(entry.tmp_path)`` for disambiguation.
    """
    # Unknown op: future binary's handler owns it; preserve raw payload
    # via _raw (already captured by the parser per §4.2).
    if entry.op not in _KNOWN_OPS:
        return _PlannedAction(
            identity=identity,
            entry=entry,
            verb="retain",
            reason=(
                f"unknown op {entry.op!r} (state={entry.state!r}); waiting for "
                f"a binary that knows this op. Known ops: {sorted(_KNOWN_OPS)}"
            ),
            log_level=logging.WARNING,
        )
    # dir_move: coordination-only (round-10). Sweep can't safely retry
    # shutil.move; drop in any state. WARN if state != done so operator
    # knows on-disk state may need inspection.
    if entry.op == OP_DIR_MOVE:
        if entry.state == STATE_DONE:
            return _PlannedAction(
                identity=identity,
                entry=entry,
                verb="drop",
                reason="dir_move done — coordination complete",
            )
        return _PlannedAction(
            identity=identity,
            entry=entry,
            verb="drop",
            reason=(
                f"dir_move entry {entry.src} -> {entry.dst} in state "
                f"{entry.state!r} — directory moves are non-atomic; "
                f"on-disk state needs operator inspection. Dropping "
                f"entry to release the in-flight marker."
            ),
            log_level=logging.WARNING,
        )
    # entry.op == OP_MOVE — the F7 atomic/durable single-file path.
    if entry.state == STATE_STARTED:
        # PR #197 step-2 behavior: ambiguous, retain. Step 6 will
        # disambiguate via fs_observer(entry.tmp_path).
        return _PlannedAction(
            identity=identity,
            entry=entry,
            verb="retain",
            reason=(
                f"started entry {entry.src} -> {entry.dst} is ambiguous "
                "(crash in copy→replace window); cannot reconcile without "
                "content comparison — operator or retry must resolve"
            ),
            log_level=logging.WARNING,
        )
    if entry.state == STATE_COPIED:
        # Codex hGWW: dst-presence guard. Missing dst means out-of-band
        # cleanup happened between the journal write and now; unlinking
        # src would destroy the last copy.
        if not fs_observer(entry.dst):
            return _PlannedAction(
                identity=identity,
                entry=entry,
                verb="retain",
                reason=(
                    f"copied entry {entry.src} -> {entry.dst} — dst is missing; "
                    "unlinking src would destroy the last remaining copy"
                ),
                log_level=logging.WARNING,
            )
        return _PlannedAction(
            identity=identity,
            entry=entry,
            verb="unlink_src_then_drop",
            reason=f"copied entry {entry.src} -> {entry.dst}; finishing by unlinking src",
        )
    if entry.state == STATE_DONE:
        return _PlannedAction(
            identity=identity,
            entry=entry,
            verb="drop",
            reason=f"done entry {entry.src} -> {entry.dst}; nothing to do",
        )
    # Known op with unrecognized state — drop with warning. Same as PR #197.
    return _PlannedAction(
        identity=identity,
        entry=entry,
        verb="drop",
        reason=f"unknown state {entry.state!r} for op {entry.op!r}; dropping",
        log_level=logging.WARNING,
    )


def _apply_planned_actions(plan: list[_PlannedAction]) -> list[_JournalEntry]:
    """Execute each :class:`_PlannedAction`, returning the retained entries.

    Round-3 §8.1: companion to :func:`plan_recovery_actions`. The planner
    decided WHAT to do; this function does it. Mutations are confined
    here so the planner stays pure and the CLI/sweep parity contract
    holds.

    Verbs:

    - ``drop``: log at the planner-chosen level, drop the entry.
    - ``retain``: log at the planner-chosen level, keep the entry.
    - ``unlink_src_then_drop``: unlink src + fsync(src.parent), then drop.
      ``FileNotFoundError`` on unlink is swallowed (already gone). Other
      ``OSError`` falls back to retain — the next sweep retries
      (codex fwMK + PR #197 round-5).

    Step 6 will add ``drop_tmp_then_drop`` for the v2 STARTED-tmp-present
    row.
    """
    retained: list[_JournalEntry] = []
    for action in plan:
        if action.log_level >= logging.WARNING or action.log_level >= logging.INFO:
            logger.log(action.log_level, "sweep: %s", action.reason)
        if action.verb == "retain":
            retained.append(action.entry)
            continue
        if action.verb == "drop":
            continue
        if action.verb == "unlink_src_then_drop":
            if not _execute_unlink_src(action.entry):
                retained.append(action.entry)
            continue
        # Defensive: an unknown verb is a programming error, not an
        # input concern — log and retain so we don't silently swallow it.
        logger.error(
            "sweep: unknown planned verb %r on entry %r; retaining defensively",
            action.verb,
            action.entry,
        )
        retained.append(action.entry)
    return retained


def _execute_unlink_src(entry: _JournalEntry) -> bool:
    """Unlink ``entry.src`` and fsync ``src.parent``.

    Returns True on success (entry should drop), False on transient
    ``OSError`` (entry should retain). Extracted from
    :func:`_apply_planned_actions` for testability and to keep the
    executor's main loop within the cyclomatic budget.
    """
    src = Path(entry.src)
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
    # Codex P2 PRRT_kwDOR_Rkws59hT9b: fsync src.parent so the unlink
    # is crash-durable BEFORE sweep truncates this entry out of the
    # journal. fsync OSError on unusual FS is non-fatal — the unlink
    # already succeeded.
    try:
        fsync_directory(src)
    except OSError as exc:
        logger.debug(
            "sweep: fsync of src.parent after unlink failed: %s",
            exc,
            exc_info=True,
        )
    return True


def _reconcile_entries(entries: list[_JournalEntry]) -> list[_JournalEntry]:
    """Plan + execute reconciliation in one call.

    Thin wrapper preserving the existing sweep call site. The work is
    split between :func:`plan_recovery_actions` (pure decision) and
    :func:`_apply_planned_actions` (mutation). Step 8's ``fo undo
    recover`` calls just the planner.
    """
    return _apply_planned_actions(plan_recovery_actions(entries))


def _serialize_entry(e: _JournalEntry) -> str:
    """JSON-serialize a journal entry for write-back.

    F7.1 §4.2: unknown-op entries are re-serialized VERBATIM from ``_raw``
    so a future binary with a handler for the op receives every field the
    writer persisted — NOT just the v2 parser's known core. Known-op entries
    reconstruct from fields.

    v2 entries (``schema == 2``) emit the extended v2 envelope; v1 entries
    (``schema == 1``, PR #197 back-compat) emit the legacy 4-field form so
    round-trip identity holds across a compaction.
    """
    # Unknown op: verbatim round-trip via the captured raw line.
    if e._raw is not None:
        return e._raw
    if e.schema == 1:
        return json.dumps({"op": e.op, "src": e.src, "dst": e.dst, "state": e.state})
    # v2 envelope. Omit diagnostic fields when None so we don't bloat the
    # journal; the parser treats missing diagnostic fields as None on read.
    payload: dict[str, object] = {
        "schema": e.schema,
        "op": e.op,
        "op_id": e.op_id,
        "src": e.src,
        "dst": e.dst,
        "state": e.state,
    }
    if e.tmp_path is not None:
        payload["tmp_path"] = e.tmp_path
    if e.ts is not None:
        payload["ts"] = e.ts
    if e.host_pid is not None:
        payload["host_pid"] = e.host_pid
    return json.dumps(payload)


def _parse_journal_text(text: str) -> list[_JournalEntry]:
    """Parse JSONL journal text into typed entries.

    Applies the §4.1 rejection rules — every malformed line is logged +
    skipped; no input can cause the parser to raise. v1 records
    (``schema`` absent) and v2 records (``schema == 2``) both accepted.
    Unknown-op entries retain their full raw JSON line on ``_raw`` per
    §4.2 for verbatim forward-compat.
    """
    entries: list[_JournalEntry] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        entry = _parse_one_journal_line(line)
        if entry is not None:
            entries.append(entry)
    return entries


class _ParseReject(Exception):
    """Internal sentinel: a field validator rejected the line.

    Validators raise this with the line already logged so the top-level
    parser can ``except _ParseReject: return None`` without nested
    logging concerns. Not exposed outside the module.
    """


def _reject(msg: str, *args: object, line: str) -> _ParseReject:
    """Log a rejection reason + truncated line and return a sentinel exception.

    Keeps every rejection site one-liner-ish so
    :func:`_parse_one_journal_line` stays within the cyclomatic budget. The
    caller raises the returned value so control flow stays explicit.
    """
    logger.warning(msg + ": %r", *args, line[:200])
    return _ParseReject()


def _validate_core_fields(data: dict, line: str) -> tuple[str, str, str, str]:
    """§4.1 rules 3 + 4: op/src/dst/state present and string-typed."""
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
        raise _reject("sweep: dropping malformed journal entry", line=line)
    return op, src, dst, state


def _validate_schema(data: dict, line: str) -> int:
    """§4.1 rule 6: schema is absent (→ 1) or a positive int."""
    schema_raw = data.get("schema")
    if schema_raw is None:
        return 1
    if isinstance(schema_raw, bool) or not isinstance(schema_raw, int) or schema_raw < 1:
        raise _reject("sweep: dropping entry with invalid schema %r", schema_raw, line=line)
    return schema_raw


def _validate_op_id(data: dict, line: str) -> str | None:
    """Parse ``op_id``. None if absent, string if typed, rejection otherwise."""
    raw = data.get("op_id")
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    raise _reject("sweep: dropping entry with non-string op_id %r", raw, line=line)


def _validate_tmp_path(data: dict, line: str) -> str | None:
    """Parse ``tmp_path``. None if absent, string if typed, rejection otherwise."""
    raw = data.get("tmp_path")
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    raise _reject("sweep: dropping entry with non-string tmp_path %r", raw, line=line)


def _validate_ts(data: dict, line: str) -> float | None:
    """Parse diagnostic ``ts``. None/float; reject bool (bool is int subclass)."""
    raw = data.get("ts")
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise _reject("sweep: dropping entry with bool ts", line=line)
    if isinstance(raw, (int, float)):
        return float(raw)
    raise _reject("sweep: dropping entry with non-numeric ts %r", raw, line=line)


def _validate_host_pid(data: dict, line: str) -> int | None:
    """Parse diagnostic ``host_pid``. None/int; reject bool."""
    raw = data.get("host_pid")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _reject("sweep: dropping entry with non-int host_pid %r", raw, line=line)
    return raw


def _parse_one_journal_line(line: str) -> _JournalEntry | None:
    """Parse a single stripped journal line per §4.1 rejection rules.

    Returns ``None`` (and logs WARNING) on any rejection case. Returns a
    typed :class:`_JournalEntry` on success. Rejection sites delegate
    to per-field ``_validate_*`` helpers to keep this function within
    the project's cyclomatic complexity budget.
    """
    # §4.1 rule 7: oversized line cap (measured in bytes so UTF-8 payloads
    # can't slip past via multi-byte characters).
    encoded = line.encode("utf-8")
    if len(encoded) > _MAX_JOURNAL_LINE_BYTES:
        logger.warning(
            "sweep: dropping oversized journal line (%d bytes > %d cap): %r",
            len(encoded),
            _MAX_JOURNAL_LINE_BYTES,
            line[:200],
        )
        return None
    # §4.1 rule 1: JSON parse error.
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("sweep: dropping unparsable journal line: %r", line[:200])
        return None
    # §4.1 rule 2 (codex iy4w): valid JSON but not an object.
    if not isinstance(data, dict):
        logger.warning(
            "sweep: dropping non-object journal line (got %s): %r",
            type(data).__name__,
            line[:200],
        )
        return None
    # Per-field validation via helpers — each raises _ParseReject with the
    # line already logged.
    try:
        op, src, dst, state = _validate_core_fields(data, line)
        schema = _validate_schema(data, line)
        if schema == 1 and op not in _KNOWN_OPS:
            raise _reject(
                "sweep: dropping v1 entry with unknown op %r (v1 records must be in %s)",
                op,
                sorted(_KNOWN_OPS),
                line=line,
            )
        op_id = _validate_op_id(data, line)
        if schema == 2 and op in _KNOWN_OPS and op_id is None:
            raise _reject("sweep: dropping v2 known-op entry missing op_id", line=line)
        tmp_path = _validate_tmp_path(data, line)
        if schema == 2 and op == OP_MOVE and state == STATE_STARTED and tmp_path is None:
            raise _reject(
                "sweep: dropping v2 move-started entry missing tmp_path (§7.1 invariant)",
                line=line,
            )
        ts = _validate_ts(data, line)
        host_pid = _validate_host_pid(data, line)
    except _ParseReject:
        return None
    # §4.2: unknown ops retain the raw line verbatim for forward-compat.
    # Known ops drop extra fields silently.
    raw_for_unknown_op = line if op not in _KNOWN_OPS else None
    return _JournalEntry(
        op=op,
        src=src,
        dst=dst,
        state=state,
        schema=schema,
        op_id=op_id,
        tmp_path=tmp_path,
        ts=ts,
        host_pid=host_pid,
        _raw=raw_for_unknown_op,
    )


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

    Codex P2 PRRT_kwDOR_Rkws59ir1P: this read MUST acquire ``LOCK_SH``
    on the journal before parsing. Writers
    (:func:`_append_journal`, :func:`sweep`) hold ``LOCK_EX`` while
    appending or truncating; without a corresponding shared lock here
    the reader can observe a stale journal between a writer's
    ``open(..., "a")`` and the actual append, returning ``False`` for
    a path that's about to be marked in-flight. In the F8 trash-GC
    flow that race is the entire data-loss vector this function
    exists to prevent.

    Args:
        path: Absolute path to check.
        journal: Same journal used by the matching ``durable_move``
            calls. Missing/empty journal → ``False`` (nothing in
            flight).
    """
    journal = Path(journal)
    if not journal.exists():
        return False

    entries: list[_JournalEntry] = []
    if os.name != "nt":
        try:
            import fcntl
        except ImportError:  # pragma: no cover - POSIX ships fcntl
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            try:
                with open(journal, encoding="utf-8") as fh:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
                    try:
                        entries = _parse_journal_text(fh.read())
                    finally:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except FileNotFoundError:  # pragma: no cover - exists()→open() race
                # Journal disappeared between exists() and open() —
                # treat the same as missing-journal. No coordination
                # needed since no writer can touch a deleted file.
                return False
    if not entries and os.name == "nt":  # pragma: no cover - Windows-only
        # Windows: fall back to unlocked read; relies on the single-
        # CLI-invocation invariant per the module docstring.
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
    """Parse the JSONL journal into typed entries. Missing/empty → [].

    F7.1 consolidation: previously duplicated the parse body from
    :func:`_parse_journal_text`, which caused the round-8 ``isinstance(data,
    dict)`` fix to be applied to only one of the two parsers (codex iy4w
    flagged this in the #201 body — "the same pattern appears in
    ``_read_journal``"). Now a thin wrapper over the shared parser so the
    §4.1 rejection rules cover both call sites.
    """
    if not journal.exists():
        return []
    return _parse_journal_text(journal.read_text())
