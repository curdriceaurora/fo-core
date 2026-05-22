"""Writer stage - file copy/move operations.

Copies the file to its computed destination.  Skipped in dry-run
mode (``context.dry_run is True``).
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import TYPE_CHECKING

from interfaces.pipeline import StageContext

if TYPE_CHECKING:
    from utils.safedir import SafeDir

logger = logging.getLogger(__name__)


class WriterStage:
    """Copy or move the file to its destination.

    In dry-run mode the stage records what *would* happen but
    does not touch the filesystem.

    When ``context.dest_safedir`` is set (POSIX, PR6 / #270) the copy uses
    an ``O_NOFOLLOW`` open of the destination file inside the category dir so
    a symlink-swap of the destination file itself is caught.  Falls back to
    ``shutil.copy2`` on Windows or when the SafeDir is unavailable.
    """

    @property
    def name(self) -> str:
        """Return stage name."""
        return "writer"

    def process(self, context: StageContext) -> StageContext:
        """Copy the file to ``context.destination``."""
        if context.failed:
            return context

        if context.destination is None:
            context.error = "No destination set (postprocessor stage missing?)"
            return context

        if context.dry_run:
            logger.info(
                "[DRY RUN] Would copy %s -> %s",
                context.file_path,
                context.destination,
            )
            return context

        try:
            destination = context.destination
            sd = context.dest_safedir

            if sd is not None:
                # POSIX SafeDir path: copy bytes then set metadata.
                # open_child adds O_NOFOLLOW automatically, rejecting a
                # symlink-swap of the destination file with SymlinkRejected
                # (PR6 / #270).
                safedir: SafeDir = sd
                dst_name = destination.name
                dst_fd = safedir.open_child(
                    dst_name,
                    flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                )
                try:
                    # safedir: ok — source read; symlinks already filtered by safe_walk at collection
                    with open(context.file_path, "rb") as src_fh:
                        while True:
                            chunk = src_fh.read(65536)
                            if not chunk:
                                break
                            view = memoryview(chunk)
                            written = 0
                            while written < len(view):
                                written += os.write(dst_fd, view[written:])
                    # E1 fix (issue #354): preserve mode and timestamps via fd-based
                    # calls while the destination fd is still open — TOCTOU-free.
                    # os.fchmod / os.utime on an open fd operate on the inode
                    # directly, unlike shutil.copystat which re-opens the path and
                    # creates a window for a symlink-swap attack (#322 / 1.7).
                    try:
                        src_stat = os.stat(context.file_path)
                        os.fchmod(dst_fd, src_stat.st_mode & 0o777)
                        os.utime(dst_fd, ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
                    except OSError:
                        pass  # non-fatal: metadata loss is preferable to aborting the copy
                finally:
                    os.close(dst_fd)
            else:
                # Windows / SafeDir unavailable: legacy path-based copy.
                destination.parent.mkdir(parents=True, exist_ok=True)
                # safedir: ok — Windows / SafeDir unavailable fallback path
                shutil.copy2(context.file_path, destination)

            logger.info("Copied %s -> %s", context.file_path, context.destination)
        except OSError as exc:
            from utils.safedir import SymlinkRejected

            if isinstance(exc, SymlinkRejected):
                logger.error(
                    "security_event writer_symlink_rejected path=%s dst=%s: "
                    "destination file is a symlink — refusing write",
                    context.file_path,
                    context.destination,
                    exc_info=True,
                )
            else:
                logger.exception("Writer failed for %s", context.file_path)
            context.error = str(exc)

        return context
