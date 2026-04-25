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

Step 1 lands the public types and constructor surface only — the
``safe_delete`` body lands in steps 3-4, and init-time orphan recovery
in step 2. The tiered approach lets reviewers validate the API contract
before any locking or filesystem mutation lands.

Spec: ``docs/internal/F8-1-trash-gc-design.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from undo import _journal


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

    Step 1 (this commit): public types + constructor surface only.
    The ``safe_delete`` body and the eager init-time orphan recovery
    land in subsequent steps per the spec §7 implementation order.

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
        """Configure the GC and ensure the trash directory exists.

        Step 2 will add eager orphan-recovery here (scan ``trash_dir``
        for ``.pending-delete-*`` entries and rmtree them).
        """
        self.trash_dir: Path = trash_dir
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path: Path = (
            journal_path if journal_path is not None else _journal.default_journal_path()
        )
