"""Link-based actions for the copilot rule system.

Implements ``hardlink`` and ``symlink`` actions that let users create a
second-view of files under a new path without duplicating storage.

Conflict-resolution strategies mirror those of move/copy actions:

* ``skip``             — leave the destination untouched, record a skip.
* ``overwrite``        — replace the destination link/file.
* ``rename_new``       — keep the destination; add a counter suffix to the
                         *incoming* link (e.g. ``photo_1.jpg``).
* ``rename_existing``  — rename the *existing* destination file; place the
                         new link at the originally requested path.
"""

from __future__ import annotations

import errno
import os
import warnings
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from loguru import logger


class ConflictStrategy(Enum):
    """How to handle a destination path that already exists."""

    SKIP = "skip"
    OVERWRITE = "overwrite"
    RENAME_NEW = "rename_new"
    RENAME_EXISTING = "rename_existing"


@dataclass
class LinkResult:
    """Result of a single hardlink or symlink operation.

    Attributes:
        success: Whether the link was created (or would be created in dry-run).
        source: The original source path.
        destination: The final destination path (may differ from the requested
            destination when a rename strategy was applied).
        dry_run: True when no filesystem changes were made.
        skipped: True when the operation was skipped due to conflict strategy.
        message: Human-readable explanation of what happened (or would happen).
    """

    success: bool
    source: Path
    destination: Path
    dry_run: bool
    skipped: bool = False
    message: str = ""


def _counter_path(path: Path, counter: int) -> Path:
    """Return *path* with *counter* inserted before the suffix."""
    return path.with_name(f"{path.stem}_{counter}{path.suffix}")


def _find_free_name(path: Path, *, max_attempts: int = 9999) -> Path:
    """Return the first non-existing path derived from *path* by appending a counter."""
    for i in range(1, max_attempts + 1):
        candidate = _counter_path(path, i)
        if not candidate.exists():
            return candidate
    raise OSError(
        errno.EEXIST,
        f"Could not find a free name for {path} after {max_attempts} attempts",
        str(path),
    )


def _resolve_dest(src: Path, dest: str) -> Path:
    """Expand *dest* to a concrete ``Path``, appending the source filename when
    *dest* is (or resolves to) an existing directory.

    Args:
        src: The source file path.
        dest: Destination string (may contain ``~`` or template variables
            ``{name}``, ``{stem}``, ``{ext}``).

    Returns:
        Resolved destination ``Path``.
    """
    resolved = dest.format(
        name=src.name,
        stem=src.stem,
        ext=src.suffix.lstrip("."),
    )
    p = Path(resolved).expanduser()
    if p.is_dir():
        p = p / src.name
    return p


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_hardlink(
    src: Path,
    dest: str,
    *,
    conflict: ConflictStrategy | str = ConflictStrategy.RENAME_NEW,
    dry_run: bool = False,
) -> LinkResult:
    """Create a hard link from *src* to the resolved *dest* path.

    Hard links require both paths to be on the **same filesystem**.  If the
    source and destination are on different devices an ``OSError`` with
    ``errno.EXDEV`` is raised by the OS; this function catches it and returns
    a failed ``LinkResult`` with an informative message instead of propagating
    the exception.

    FAT32 and similar filesystems that do not support hard links return
    ``errno.EPERM``; the same graceful fallback applies.

    Args:
        src: Source file.  Must be a regular file (not a directory).
        dest: Destination path string.  Supports ``~`` expansion and
            ``{name}``, ``{stem}``, ``{ext}`` template variables.  When
            *dest* resolves to an existing directory the source filename is
            appended automatically.
        conflict: Strategy for handling an existing destination path.
        dry_run: When ``True``, print what *would* happen without touching
            the filesystem.

    Returns:
        A :class:`LinkResult` describing the outcome.
    """
    if isinstance(conflict, str):
        conflict = ConflictStrategy(conflict)

    dest_path = _resolve_dest(src, dest)

    if dry_run:
        msg = f"[dry-run] Would hardlink {src} → {dest_path}"
        logger.info(msg)
        return LinkResult(
            success=True,
            source=src,
            destination=dest_path,
            dry_run=True,
            message=msg,
        )

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    final_dest, skipped = _apply_conflict_strategy(dest_path, conflict, "hardlink")
    if skipped:
        return LinkResult(
            success=True,
            source=src,
            destination=dest_path,
            dry_run=False,
            skipped=True,
            message=f"Skipped: {dest_path} already exists",
        )

    try:
        os.link(src, final_dest)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            msg = (
                f"Cannot create hardlink from {src} to {final_dest}: "
                "source and destination are on different filesystems. "
                "Use 'symlink' action for cross-volume links."
            )
        elif exc.errno == errno.EPERM:
            msg = (
                f"Cannot create hardlink at {final_dest}: "
                "filesystem does not support hard links (e.g. FAT32). "
                "Use 'symlink' action or a supported filesystem."
            )
        else:
            msg = f"Failed to create hardlink {src} → {final_dest}: {exc}"
        logger.error(msg)
        return LinkResult(
            success=False,
            source=src,
            destination=final_dest,
            dry_run=False,
            message=msg,
        )

    msg = f"Hardlinked {src} → {final_dest}"
    logger.info(msg)
    return LinkResult(
        success=True,
        source=src,
        destination=final_dest,
        dry_run=False,
        message=msg,
    )


def apply_symlink(
    src: Path,
    dest: str,
    *,
    conflict: ConflictStrategy | str = ConflictStrategy.RENAME_NEW,
    dry_run: bool = False,
) -> LinkResult:
    """Create a symbolic link at *dest* pointing to *src*.

    Symlinks work across filesystem boundaries, making them suitable as the
    cross-volume counterpart to ``hardlink``.

    If *src* is itself a symlink a ``UserWarning`` is issued to alert the
    caller about potential symlink chains.

    Args:
        src: Source file.  The symlink will point to this path.
        dest: Destination path string.  Supports ``~`` expansion and
            ``{name}``, ``{stem}``, ``{ext}`` template variables.  When
            *dest* resolves to an existing directory the source filename is
            appended automatically.
        conflict: Strategy for handling an existing destination path.
        dry_run: When ``True``, print what *would* happen without touching
            the filesystem.

    Returns:
        A :class:`LinkResult` describing the outcome.
    """
    if isinstance(conflict, str):
        conflict = ConflictStrategy(conflict)

    dest_path = _resolve_dest(src, dest)

    if src.is_symlink():
        warnings.warn(
            f"Source path {src} is itself a symlink; creating a symlink to it "
            "may produce a symlink chain.",
            stacklevel=2,
        )

    if dry_run:
        msg = f"[dry-run] Would symlink {dest_path} → {src}"
        logger.info(msg)
        return LinkResult(
            success=True,
            source=src,
            destination=dest_path,
            dry_run=True,
            message=msg,
        )

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    final_dest, skipped = _apply_conflict_strategy(dest_path, conflict, "symlink")
    if skipped:
        return LinkResult(
            success=True,
            source=src,
            destination=dest_path,
            dry_run=False,
            skipped=True,
            message=f"Skipped: {dest_path} already exists",
        )

    try:
        os.symlink(src, final_dest)
    except OSError as exc:
        msg = f"Failed to create symlink {final_dest} → {src}: {exc}"
        logger.error(msg)
        return LinkResult(
            success=False,
            source=src,
            destination=final_dest,
            dry_run=False,
            message=msg,
        )

    msg = f"Symlinked {final_dest} → {src}"
    logger.info(msg)
    return LinkResult(
        success=True,
        source=src,
        destination=final_dest,
        dry_run=False,
        message=msg,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_conflict_strategy(
    dest: Path,
    strategy: ConflictStrategy,
    link_type: str,
) -> tuple[Path, bool]:
    """Apply *strategy* and return ``(final_dest, skipped)``.

    Args:
        dest: The originally requested destination.
        strategy: How to handle a pre-existing file at *dest*.
        link_type: ``"hardlink"`` or ``"symlink"`` (used only in log messages).

    Returns:
        A ``(final_dest, skipped)`` tuple where ``skipped`` is ``True`` when
        the caller should bail out without creating a link.
    """
    if not dest.exists() and not dest.is_symlink():
        return dest, False

    if strategy == ConflictStrategy.SKIP:
        logger.debug("{}: {} already exists — skipping", link_type, dest)
        return dest, True

    if strategy == ConflictStrategy.OVERWRITE:
        logger.debug("{}: overwriting {}", link_type, dest)
        dest.unlink()
        return dest, False

    if strategy == ConflictStrategy.RENAME_NEW:
        new_dest = _find_free_name(dest)
        logger.debug("{}: {} exists — renaming new link to {}", link_type, dest, new_dest)
        return new_dest, False

    if strategy == ConflictStrategy.RENAME_EXISTING:
        renamed = _find_free_name(dest)
        logger.debug(
            "{}: {} exists — renaming existing file to {}", link_type, dest, renamed
        )
        dest.rename(renamed)
        return dest, False

    return dest, False  # pragma: no cover — all enum members handled above
