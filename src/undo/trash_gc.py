"""F8.1 race-safe trash deletion API (issue #202).

Closes the check-then-delete TOCTOU race in the F8 trash-GC surface. A
predicate alone (:func:`is_trash_safe_to_delete`) leaves the window between
the check and the delete unprotected — a concurrent rollback can journal a
``move started`` entry between the predicate read and the unlink, and the
caller would unlink a path the rollback was about to touch.

:class:`TrashGC` provides one blessed deletion entry point that performs
the in-flight check AND the deletion under a single ``LOCK_EX`` on
``<journal>.lock`` (per #201 §6.1). For directories, the lock is held
only for an atomic rename into a staging path (``.pending-delete-<uuid>``);
the slow ``rmtree`` runs unlocked, so concurrent writers are blocked for
microseconds regardless of trash subtree size.

Public surface (see :class:`TrashGC` for details):

- ``TrashGC(trash_dir, *, journal_path=None)`` — constructor; eagerly
  cleans ``.pending-delete-*`` orphans from prior crashed deletions.
- ``TrashGC.safe_delete(path) -> TrashDeleteOutcome`` — single race-safe
  deletion entry point covering files, symlinks (incl. dangling and
  symlinks to directories), and directories.
- ``TrashDeleteResult`` (StrEnum, six variants) and
  ``TrashDeleteOutcome`` (frozen dataclass) — public outcome types.

Spec + operator reference: ``docs/internal/F8-1-trash-gc-design.md`` and
``docs/internal/F8-1-trash-gc.md``.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from undo import _journal
from undo.durable_move import (
    _HAS_FCNTL,
    _locked,
    _path_in_flight_from_entries,
    _read_journal,
)

if _HAS_FCNTL:
    import fcntl
else:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Spec §3.3 / §3.4: GC-owned staging name prefix. The hyphen is part of
# the prefix so .pending-delete (no suffix) and .pending-deleted-x
# (different stem) are NOT recovered. Tests in §6.3a guard the boundary.
_STAGING_PREFIX = ".pending-delete-"


class TrashDeleteResult(StrEnum):
    """Six-variant outcome of a :meth:`TrashGC.safe_delete` call.

    Spec §2 / §5.1. ``StrEnum`` so callers can ``json.dumps`` an
    outcome directly and log lines render the lowercase string value.
    Stable string values for log telemetry across versions.
    """

    DELETED = "deleted"
    """Path removed cleanly. For files/symlinks: the unlink succeeded.
    For directories: both the atomic rename AND the unlocked rmtree
    completed."""

    DELETED_WITH_STAGING_FAILURE = "deleted_with_staging_failure"
    """Directory case only. The user's path is gone (atomic rename
    succeeded under lock), but the unlocked ``shutil.rmtree`` of the
    staging dir failed. The orphan ``<trash_dir>/.pending-delete-*``
    survives for the next :class:`TrashGC` construction's eager recovery
    sweep to remove. Surfaced as a distinct outcome so operators see
    the partial state — disk space hasn't been reclaimed yet even
    though the trash entry itself is gone."""

    SKIPPED_IN_FLIGHT = "skipped"
    """Journal shows an active ``move`` / ``dir_move`` entry whose src
    or dst matches the path; deletion would race the in-flight
    operation. Caller retries when the operation completes."""

    MISSING = "missing"
    """Path didn't exist when checked. Idempotent no-op outcome — a
    second :meth:`safe_delete` call after a successful one returns
    ``MISSING``, not an error."""

    PERMISSION_ERROR = "permission_error"
    """``OSError`` on the unlink (file/symlink path) or on the rename
    (directory path). The path is still at the original location.
    :attr:`TrashDeleteOutcome.error` is populated with the exception."""

    OUTSIDE_TRASH = "outside_trash"
    """The requested path resolves outside the configured trash root.
    Returned without ever acquiring the journal lock — a clearly
    out-of-bounds path doesn't deserve coordination overhead."""


@dataclass(frozen=True)
class TrashDeleteOutcome:
    """Result of a single :meth:`TrashGC.safe_delete` call.

    Frozen so outcomes can be hashed (e.g. for set-of-results in
    multi-path GC drivers). Carries enough context to drive both the
    structured log line (§5.3) and any operator-visible reporting
    upstream of the GC.

    Attributes:
        result: One of the six :class:`TrashDeleteResult` variants.
        path: The original path argument as supplied by the caller.
            Not the staging path; not the resolved-symlink target —
            so the caller's view of the operation is preserved.
        reason: Human-readable explanation. Always populated; safe for
            log lines.
        error: Underlying exception for ``PERMISSION_ERROR`` and
            ``DELETED_WITH_STAGING_FAILURE`` outcomes. ``None`` for
            success / skip / missing / outside-trash variants.
    """

    result: TrashDeleteResult
    path: Path
    reason: str
    error: BaseException | None = None


class TrashGC:
    """Race-safe trash deletion coordinator.

    Single-path API: :meth:`safe_delete` performs the in-flight check
    and the deletion under one ``LOCK_EX`` on ``<journal>.lock``,
    closing the check-then-delete TOCTOU window.

    For directories, the lock is held only for an atomic ``os.rename``
    into a ``.pending-delete-<uuid>`` staging path; the slow
    ``shutil.rmtree`` runs unlocked. The construction also eagerly
    cleans any orphan staging directories left by prior crashed
    deletions (§3.4 of the design spec).

    Args:
        trash_dir: Configured trash directory. ALL paths passed to
            :meth:`safe_delete` MUST resolve inside this root; paths
            that escape return ``OUTSIDE_TRASH``. Created on
            construction if absent (mirrors ``OperationValidator``).
        journal_path: Override the durable_move journal location. When
            omitted, falls through to
            :func:`undo._journal.default_journal_path` so :class:`TrashGC`,
            :class:`OperationValidator`, and :class:`RollbackExecutor`
            all coordinate on the same on-disk journal.
            Keyword-only (rejecting positional pass-through prevents
            the ``trash_dir`` / ``journal`` argument-order confusion
            that any path-typed pair invites).
    """

    def __init__(
        self,
        trash_dir: Path,
        *,
        journal_path: Path | None = None,
    ) -> None:
        """Configure the GC, ensure trash_dir exists, and recover orphan staging dirs.

        Recovery is intentionally lockless: ``.pending-delete-*`` names
        are GC-owned and isolated from the user's path namespace by
        construction, so no journal coordination is required to clean
        them. Per-orphan failures are logged at WARNING and the loop
        continues so one stuck entry doesn't block recovery of the rest.
        """
        self.trash_dir: Path = trash_dir
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path: Path = (
            journal_path if journal_path is not None else _journal.default_journal_path()
        )
        self._recover_orphans()

    def _recover_orphans(self) -> None:
        """Scan ``self.trash_dir`` for ``.pending-delete-*`` orphans and rmtree them.

        Spec §3.4 / §6.3a contract. Boundary rule: the entry name must
        START WITH ``.pending-delete-`` AND have at least one character
        after the prefix — that excludes ``.pending-delete`` (no
        suffix) and unrelated names like ``.pending-deleted-x``.

        Failures are logged at WARNING with ``exc_info=True`` and
        counted; the aggregate INFO line at the end summarizes
        ``cleaned`` vs ``failed`` so operators can spot a stuck-orphan
        pattern without reading every DEBUG line.
        """
        cleaned = 0
        failed = 0
        for entry in self.trash_dir.iterdir():
            name = entry.name
            if not name.startswith(_STAGING_PREFIX):
                continue
            if len(name) == len(_STAGING_PREFIX):
                # Exact match for ".pending-delete" — no UUID suffix,
                # not a GC-emitted orphan.
                continue
            try:
                shutil.rmtree(entry)
            except OSError as exc:
                logger.warning(
                    "trash GC init recovery: failed to clean orphan %s: %s",
                    entry,
                    exc,
                    exc_info=True,
                )
                failed += 1
                continue
            logger.debug("trash GC init recovery: cleaned orphan %s", entry)
            cleaned += 1
        if cleaned or failed:
            logger.info(
                "trash GC init recovery: %d orphans cleaned, %d failed",
                cleaned,
                failed,
            )

    def safe_delete(self, path: Path) -> TrashDeleteOutcome:
        """Delete *path* from trash with race-safe coordination (§3.2 / §3.3).

        Sequence: validate path is inside ``trash_dir`` → acquire
        ``LOCK_EX`` on ``<journal>.lock`` → check ``is_path_in_flight``
        → ``lexists`` → ``unlink`` (file / symlink) OR atomic ``rename``
        to ``.pending-delete-<uuid>`` (directory) → release ``LOCK_EX``
        → unlocked ``rmtree`` of the staging dir for the directory case.

        Symlinks (including dangling and symlinks to directories) are
        always unlinked, NEVER walked into — see
        ``test_does_not_follow_symlink_to_directory`` for the load-
        bearing safety guarantee.

        Args:
            path: Path to delete. Must resolve inside ``self.trash_dir``;
                paths that escape return ``OUTSIDE_TRASH``.

        Returns:
            :class:`TrashDeleteOutcome` per the §5.1 decision table.
        """
        # §4.1: out-of-bounds rejection BEFORE acquiring the lock.
        if not self._is_inside_trash(path):
            outcome = TrashDeleteOutcome(
                result=TrashDeleteResult.OUTSIDE_TRASH,
                path=path,
                reason=(f"path {path} resolves outside the configured trash root {self.trash_dir}"),
            )
            logger.warning("trash GC: %s", outcome.reason)
            return outcome

        # Phase 1 (under LOCK_EX): in-flight check + unlink-or-rename.
        # ``_decide_under_lock`` returns either a final outcome OR a
        # staging path that phase 2 must rmtree without holding the lock.
        # Lock-hold scope is bounded by one rename syscall in the
        # directory case — §3.3 atomic-rename pivot.
        if _HAS_FCNTL:
            with _locked(self.journal_path, fcntl.LOCK_EX):
                outcome, staging = self._decide_under_lock(path)
        else:  # pragma: no cover - Windows
            outcome, staging = self._decide_under_lock(path)

        # Phase 2 (UNLOCKED): rmtree the staging dir if phase 1 renamed
        # one. Concurrent writers can proceed during the rmtree.
        if staging is not None:
            return self._rmtree_unlocked(path, staging)
        # Phase 1 produced a final outcome.
        assert outcome is not None
        return outcome

    def _is_inside_trash(self, path: Path) -> bool:
        """Return True iff *path*'s LEXICAL absolute form is inside ``self.trash_dir``.

        Uses ``os.path.abspath`` + ``relative_to`` — explicitly NOT
        ``Path.resolve()``. ``resolve`` follows symlinks; for trash
        deletion we want to delete the LINK itself, not its target,
        so the containment check must look at the lexical path inside
        ``trash_dir`` (the link IS in trash) rather than the symlink's
        target (which can be anywhere). ``abspath`` still collapses
        ``..`` so a ``trash/../neighbour`` traversal attempt is caught.
        """
        try:
            Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(self.trash_dir)))
        except ValueError:
            return False
        return True

    def _decide_under_lock(self, path: Path) -> tuple[TrashDeleteOutcome | None, Path | None]:
        """Phase 1 of ``safe_delete``; runs while holding ``LOCK_EX``.

        Returns ``(outcome, None)`` if the deletion can be fully
        decided under the lock (file/symlink path, or any error/skip
        case), or ``(None, staging_path)`` if the directory was
        successfully renamed to a staging path that must be rmtree'd
        WITHOUT the lock held (§3.3 atomic-rename pivot).

        Returning a tuple is uglier than two separate paths but keeps
        the lock acquisition site (in :meth:`safe_delete`) the single
        source of truth for "what runs under the lock."
        """
        # Unlocked predicate: we ALREADY hold LOCK_EX on the lock file.
        # Calling is_path_in_flight() here would re-acquire LOCK_SH on
        # a different fd of the same inode and deadlock against our own
        # LOCK_EX. The shared helper accepts pre-loaded entries; we
        # read them inline (also no lock needed under our held LOCK_EX).
        entries = _read_journal(self.journal_path)
        if _path_in_flight_from_entries(path, entries):
            outcome = TrashDeleteOutcome(
                result=TrashDeleteResult.SKIPPED_IN_FLIGHT,
                path=path,
                reason=(
                    f"path {path} is the src or dst of an in-flight move/"
                    "dir_move; deletion would race the rollback"
                ),
            )
            logger.info("trash GC: %s", outcome.reason)
            return outcome, None

        # ``lexists`` so dangling symlinks count as present.
        if not os.path.lexists(path):
            outcome = TrashDeleteOutcome(
                result=TrashDeleteResult.MISSING,
                path=path,
                reason=f"path {path} did not exist at deletion time",
            )
            logger.debug("trash GC: %s", outcome.reason)
            return outcome, None

        # Dispatch on path type:
        #   - Symlink (incl. links to directories) → unlink (lock-held).
        #   - Regular file → unlink (lock-held).
        #   - Real directory → atomic rename to staging (lock-held);
        #     rmtree happens UNLOCKED in phase 2.
        # ``Path.is_dir()`` returns True for symlinks-to-directories,
        # so we explicitly check ``is_symlink()`` first to keep symlinks
        # on the unlink path (load-bearing — see
        # ``test_does_not_follow_symlink_to_directory``).
        if path.is_symlink() or not path.is_dir():
            return self._unlink_under_lock(path), None
        return self._rename_dir_under_lock(path)

    def _unlink_under_lock(self, path: Path) -> TrashDeleteOutcome:
        """Unlink a file/symlink while holding ``LOCK_EX``.

        Maps OSError variants per §5.1: FileNotFoundError →
        idempotent MISSING; other OSError → PERMISSION_ERROR.
        """
        try:
            path.unlink()
        except FileNotFoundError:
            # §5.1 idempotency: a concurrent deleter won the race
            # between our lexists and our unlink. Treat as MISSING,
            # NOT PERMISSION_ERROR — we got the same end state.
            outcome = TrashDeleteOutcome(
                result=TrashDeleteResult.MISSING,
                path=path,
                reason=f"path {path} vanished between lexists and unlink",
            )
            logger.debug("trash GC: %s", outcome.reason)
            return outcome
        except OSError as exc:
            outcome = TrashDeleteOutcome(
                result=TrashDeleteResult.PERMISSION_ERROR,
                path=path,
                reason=f"unlink of {path} raised: {exc}",
                error=exc,
            )
            logger.warning("trash GC: %s", outcome.reason, exc_info=True)
            return outcome
        outcome = TrashDeleteOutcome(
            result=TrashDeleteResult.DELETED,
            path=path,
            reason=f"unlinked {path}",
        )
        logger.debug("trash GC: %s", outcome.reason)
        return outcome

    def _rename_dir_under_lock(self, path: Path) -> tuple[TrashDeleteOutcome | None, Path | None]:
        """Atomically rename a directory to a staging path (§3.3 step 6).

        Returns:
            ``(None, staging)`` on success — phase 2 ``rmtree`` is the
            caller's responsibility (and runs UNLOCKED).
            ``(outcome, None)`` on rename failure: PERMISSION_ERROR
            with the OSError populated; the original path is still in
            place because the rename never landed.

        The staging path lives inside ``self.trash_dir`` so the rename
        is single-filesystem (no EXDEV failure mode). The
        ``.pending-delete-<uuid>`` namespace is GC-owned: no other
        writer touches paths with that prefix, so the unlocked rmtree
        in phase 2 cannot race anyone.
        """
        staging = self.trash_dir / f".pending-delete-{uuid.uuid4().hex}"
        try:
            os.rename(path, staging)
        except OSError as exc:
            outcome = TrashDeleteOutcome(
                result=TrashDeleteResult.PERMISSION_ERROR,
                path=path,
                reason=f"rename of {path} to staging {staging.name} raised: {exc}",
                error=exc,
            )
            logger.warning("trash GC: %s", outcome.reason, exc_info=True)
            return outcome, None
        # Lock will release in safe_delete; phase 2 owns the rmtree.
        return None, staging

    def _rmtree_unlocked(self, original_path: Path, staging: Path) -> TrashDeleteOutcome:
        """Phase 2: ``rmtree`` the staging dir WITHOUT holding the lock (§3.3).

        Spec §5.1 outcomes for the directory case after a successful
        rename:

        - ``rmtree`` succeeds → ``DELETED`` (clean).
        - ``rmtree`` raises → ``DELETED_WITH_STAGING_FAILURE``: the
          user's ``original_path`` is gone (rename succeeded under
          lock), but the orphan staging dir survives. Next-init eager
          recovery (§3.4) cleans it on the next :class:`TrashGC`
          construction. The error is surfaced so operators correlate
          partial-state outcomes with the underlying failure.
        """
        try:
            shutil.rmtree(staging)
        except OSError as exc:
            outcome = TrashDeleteOutcome(
                result=TrashDeleteResult.DELETED_WITH_STAGING_FAILURE,
                path=original_path,
                reason=(
                    f"renamed {original_path} to staging {staging.name} "
                    f"under lock, but unlocked rmtree raised: {exc}. "
                    "Orphan staging dir will be cleaned by next TrashGC "
                    "construction's init recovery."
                ),
                error=exc,
            )
            logger.warning("trash GC: %s", outcome.reason, exc_info=True)
            return outcome
        outcome = TrashDeleteOutcome(
            result=TrashDeleteResult.DELETED,
            path=original_path,
            reason=f"renamed {original_path} to staging and rmtree'd",
        )
        logger.debug("trash GC: %s", outcome.reason)
        return outcome
