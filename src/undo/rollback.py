"""Rollback executor for file operations.

This module executes rollback operations for undo/redo,
handling all operation types and transaction management.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from history.models import Operation, OperationType

from ._journal import default_journal_path
from .durable_move import directory_move, durable_move
from .models import RollbackResult
from .validator import OperationValidator

logger = logging.getLogger(__name__)


class RollbackExecutor:
    """Executes rollback operations for undo/redo.

    This class handles the actual file system operations required
    to undo or redo file operations.
    """

    def __init__(
        self,
        validator: OperationValidator | None = None,
        journal_path: Path | None = None,
    ) -> None:
        """Initialize rollback executor.

        Args:
            validator: Operation validator.
            journal_path: F7 durable-move journal location. When
                omitted, inherits the validator's ``journal_path``
                so write (durable_move) and read
                (``is_trash_safe_to_delete``) coordinate on the same
                file; falls back to :func:`default_journal_path` only
                when no validator-configured value is available. Tests
                pass a per-test ``tmp_path`` to isolate from the real
                user journal.
        """
        self.validator = validator or OperationValidator()
        self.trash_dir = self.validator.trash_dir
        # Codex P2 PRRT_kwDOR_Rkws59hGWY: if the caller injects a
        # validator with a custom ``journal_path`` (typical in tests
        # and in any multi-tenant setup) but omits ``journal_path``
        # here, we MUST reuse the validator's path. Using the
        # default instead splits write/read: durable_move would write
        # entries to the default journal while
        # ``is_trash_safe_to_delete`` reads the validator's — a path
        # flagged in-flight in one would appear safe in the other,
        # reintroducing the F8 GC-vs-restore race.
        if journal_path is not None:
            self.journal_path = journal_path
        else:
            validator_journal = getattr(self.validator, "journal_path", None)
            self.journal_path = (
                validator_journal if validator_journal is not None else default_journal_path()
            )

    def _move(self, src: Path, dst: Path) -> None:
        """Move *src* to *dst* with a file-vs-directory dispatch.

        Files and symlinks go through :func:`durable_move`
        (atomic same-device, journalled+durable EXDEV);
        non-symlink directories go through :func:`directory_move`
        (non-atomic ``shutil.move`` wrapped with started/done
        journal entries so concurrent F8 trash GC sees them as
        in-flight).

        Codex PRRT_kwDOR_Rkws59hT9a (round-7) + coderabbit round-10:
        :func:`durable_move` rejects non-symlink directories up
        front with ``IsADirectoryError`` (it is file-only by design
        — atomic directory recovery is F7's intentional non-goal).
        Round-10's :func:`directory_move` adds the F8 coordination
        layer that was previously missing — the bare
        ``shutil.move`` call did not write to the journal, so
        :func:`is_path_in_flight` returned False during a directory
        restore and trash GC could delete the path mid-move.
        Symlinks still route through :func:`durable_move` because
        ``shutil.move`` would dereference them and copy target
        bytes instead of preserving the link.
        """
        if src.is_dir() and not src.is_symlink():
            directory_move(src, dst, journal=self.journal_path)
        else:
            durable_move(src, dst, journal=self.journal_path)

    def rollback_operation(self, operation: Operation) -> bool:
        """Rollback a single operation (undo).

        Args:
            operation: Operation to rollback

        Returns:
            True if successful, False otherwise
        """
        try:
            if operation.operation_type == OperationType.MOVE:
                return self.rollback_move(operation)
            elif operation.operation_type == OperationType.RENAME:
                return self.rollback_rename(operation)
            elif operation.operation_type == OperationType.DELETE:
                return self.rollback_delete(operation)
            elif operation.operation_type == OperationType.COPY:
                return self.rollback_copy(operation)
            elif operation.operation_type == OperationType.CREATE:
                return self.rollback_create(operation)
            else:
                logger.error(f"Unknown operation type: {operation.operation_type}")
                return False
        except Exception as e:
            logger.error(f"Failed to rollback operation {operation.id}: {e}", exc_info=True)
            return False

    def redo_operation(self, operation: Operation) -> bool:
        """Redo an operation (forward execution).

        Args:
            operation: Operation to redo

        Returns:
            True if successful, False otherwise
        """
        try:
            if operation.operation_type == OperationType.MOVE:
                return self.redo_move(operation)
            elif operation.operation_type == OperationType.RENAME:
                return self.redo_rename(operation)
            elif operation.operation_type == OperationType.DELETE:
                return self.redo_delete(operation)
            elif operation.operation_type == OperationType.COPY:
                return self.redo_copy(operation)
            elif operation.operation_type == OperationType.CREATE:
                return self.redo_create(operation)
            else:
                logger.error(f"Unknown operation type: {operation.operation_type}")
                return False
        except Exception as e:
            logger.error(f"Failed to redo operation {operation.id}: {e}", exc_info=True)
            return False

    def rollback_move(self, operation: Operation) -> bool:
        """Rollback a move operation (move file back to source).

        PR5c / #269: Before replaying the move, verify that the file
        currently at ``destination`` is the same inode that was there
        when the original move landed.  If the history row carries
        ``dest_dev`` / ``dest_ino`` (recorded by PR5b's
        ``durable_move`` return value) and the current lstat disagrees,
        the file was swapped — refuse and log a security event.

        Legacy rows (pre-PR5, ``dest_dev is None``) fall through to the
        existing behaviour without inode verification.

        Args:
            operation: Move operation to rollback

        Returns:
            True if successful, False otherwise
        """
        source = operation.source_path
        destination = operation.destination_path

        if destination is None:
            logger.error(f"Cannot rollback move operation {operation.id}: no destination path")
            return False

        logger.info(f"Rolling back move: {destination} -> {source}")

        # PR5c inode verification — POSIX only; requires both fields (partial
        # rows from broken writes or legacy DBs fall back to legacy path).
        if (
            sys.platform != "win32"
            and operation.dest_dev is not None
            and operation.dest_ino is not None
        ):
            if not self._verify_dst_inode(operation, destination):
                return False

        try:
            # F7: durable_move replaces ``shutil.move`` for files;
            # ``_move`` dispatches to ``shutil.move`` for directories
            # (codex PRRT_kwDOR_Rkws59hT9a). Atomic on same device;
            # EXDEV path journals each step so a crash mid-move leaves
            # recoverable state. The helper creates dst's parent.
            self._move(destination, source)

            logger.info(f"Successfully rolled back move operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to rollback move operation {operation.id}: {e}")
            return False

    # O_PATH (Linux) lets us open any file-system object without reading it and
    # without following symlinks; it is unavailable on macOS / Darwin so we fall
    # back to O_RDONLY | O_NOFOLLOW there.  The fd returned by os.open is used
    # only for os.fstat — it binds the inode check to the specific file that
    # existed at ``destination`` at the moment of the open, closing the TOCTOU
    # window that a plain ``os.lstat`` call leaves open.
    # O_NOFOLLOW is POSIX-only; Windows has neither it nor O_PATH.  Fallback
    # to 0 keeps the module importable on Windows — _verify_dst_inode is only
    # reachable when dest_dev/dest_ino are set, which the POSIX-only inode
    # capture path (sys.platform != "win32" guard in rollback_move) ensures.
    _O_VERIFY: int = getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0)

    def _verify_dst_inode(self, operation: Operation, destination: Path) -> bool:
        """Return True iff the file at *destination* matches the recorded inode.

        Called by :meth:`rollback_move` for new-style rows (``dest_dev``
        is not ``None``).  On mismatch — indicating a swap between the
        original move and the undo attempt — logs a ``security_event``
        and returns ``False`` so the caller refuses the replay.

        ``FileNotFoundError`` / ``ENOENT`` (destination gone) is treated as a
        mismatch: if the file is missing we cannot verify identity and
        must refuse rather than silently skip (which would look like
        success to the caller).

        Implementation note — fd-pinned inode check (issue #324 finding 3.1):
        We open *destination* with ``O_PATH | O_NOFOLLOW`` (Linux) or
        ``O_RDONLY | O_NOFOLLOW`` (macOS) and call ``os.fstat`` on the
        resulting fd.  Keeping the fd open until after the comparison binds
        the inode read to the specific on-disk object at *destination*, so an
        attacker cannot swap a symlink between our stat and our rename.
        ``O_NOFOLLOW`` ensures we never follow a symlink at *destination*
        even if the attacker races to place one there.

        macOS symlink fallback (issue #324 P2): On Linux ``O_PATH`` opens
        the symlink inode itself without following it, so symlink destinations
        work fine.  On macOS ``O_RDONLY | O_NOFOLLOW`` raises ``OSError``
        (ELOOP) for symlink leaf paths.  Since move operations explicitly
        support symlinks, we detect this case and fall back to ``os.lstat()``,
        which avoids the fd-binding guarantee but still compares the full
        ``(st_dev, st_ino)`` triple.
        """
        fd: int | None = None
        try:
            # macOS (no O_PATH) cannot open a symlink with O_NOFOLLOW — fall
            # back to lstat-based check.  On Linux O_PATH handles symlinks.
            if not hasattr(os, "O_PATH") and destination.is_symlink():
                st = os.lstat(str(destination))
            else:
                fd = os.open(str(destination), self._O_VERIFY)
                st = os.fstat(fd)
        except FileNotFoundError:
            logger.error(
                "security_event undo_dst_missing op_id=%s path=%s: "
                "destination absent at undo time; refusing replay",
                operation.id,
                destination,
                exc_info=True,
            )
            return False
        except OSError:
            logger.error(
                "security_event undo_dst_lstat_error op_id=%s path=%s: "
                "cannot stat destination; refusing replay",
                operation.id,
                destination,
                exc_info=True,
            )
            return False
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass  # best-effort close; inode check already done or failed

        if st.st_dev != operation.dest_dev or st.st_ino != operation.dest_ino:
            logger.error(
                "security_event undo_inode_mismatch op_id=%s path=%s: "
                "recorded (dev=%s ino=%s) != current (dev=%s ino=%s); "
                "file was replaced — refusing undo replay",
                operation.id,
                destination,
                operation.dest_dev,
                operation.dest_ino,
                st.st_dev,
                st.st_ino,
            )
            return False

        return True

    def rollback_rename(self, operation: Operation) -> bool:
        """Rollback a rename operation (rename back to original).

        Args:
            operation: Rename operation to rollback

        Returns:
            True if successful, False otherwise
        """
        old_name = operation.source_path
        new_name = operation.destination_path

        if new_name is None:
            logger.error(f"Cannot rollback rename operation {operation.id}: no destination path")
            return False

        logger.info(f"Rolling back rename: {new_name} -> {old_name}")

        try:
            # Rename back
            new_name.rename(old_name)

            logger.info(f"Successfully rolled back rename operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to rollback rename operation {operation.id}: {e}")
            return False

    def rollback_delete(self, operation: Operation) -> bool:
        """Rollback a delete operation (restore from trash).

        Args:
            operation: Delete operation to rollback

        Returns:
            True if successful, False otherwise
        """
        original_path = operation.source_path
        trash_path = self.validator._get_trash_path(operation)

        if not trash_path or not trash_path.exists():
            logger.error(f"File not found in trash for operation {operation.id}")
            return False

        logger.info(f"Rolling back delete: restoring {original_path} from trash")

        try:
            # F7: durable_move for trash→original restore, routed
            # through ``_move`` so directory entries in trash (created
            # by ``_move_to_trash``'s shutil.move fallback) restore
            # correctly — durable_move is file-only and would reject
            # directories with IsADirectoryError (codex
            # PRRT_kwDOR_Rkws59hT9a). Pairs with F8's trash GC race
            # protection: once the move lands in the journal the GC
            # validator detects the in-progress restore and avoids
            # deleting the trash path mid-move.
            self._move(trash_path, original_path)

            # Clean up trash directory for this operation
            if trash_path.parent.name == str(operation.id):
                try:
                    trash_path.parent.rmdir()
                except OSError:
                    pass  # Directory not empty or other issue

            logger.info(f"Successfully rolled back delete operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to rollback delete operation {operation.id}: {e}")
            return False

    def rollback_copy(self, operation: Operation) -> bool:
        """Rollback a copy operation (delete the copy).

        Args:
            operation: Copy operation to rollback

        Returns:
            True if successful, False otherwise
        """
        copy_path = operation.destination_path

        if copy_path is None:
            logger.error(f"Cannot rollback copy operation {operation.id}: no destination path")
            return False

        logger.info(f"Rolling back copy: deleting {copy_path}")

        try:
            # Move to trash instead of permanent delete
            self._move_to_trash(copy_path, operation.id)

            logger.info(f"Successfully rolled back copy operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to rollback copy operation {operation.id}: {e}")
            return False

    def rollback_create(self, operation: Operation) -> bool:
        """Rollback a create operation (delete the created file).

        Args:
            operation: Create operation to rollback

        Returns:
            True if successful, False otherwise
        """
        created_path = operation.source_path

        logger.info(f"Rolling back create: deleting {created_path}")

        try:
            # Move to trash instead of permanent delete
            self._move_to_trash(created_path, operation.id)

            logger.info(f"Successfully rolled back create operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to rollback create operation {operation.id}: {e}")
            return False

    def redo_move(self, operation: Operation) -> bool:
        """Redo a move operation (move file to destination again).

        Issue #324 finding 3.2 — EXDEV redo inode baseline refresh:
        After a successful cross-device (EXDEV) redo, ``destination`` has
        a brand-new inode (the original src inode no longer exists on the
        target device).  We stat *destination* after the move and update
        ``dest_dev`` / ``dest_ino`` on the operation so that any subsequent
        undo uses the refreshed pin rather than the now-stale pre-redo value.
        The update is best-effort: a stat failure is logged but does not cause
        the redo to report failure (the file was already moved successfully).

        Args:
            operation: Move operation to redo

        Returns:
            True if successful, False otherwise
        """
        source = operation.source_path
        destination = operation.destination_path

        if destination is None:
            logger.error(f"Cannot redo move operation {operation.id}: no destination path")
            return False

        logger.info(f"Redoing move: {source} -> {destination}")

        try:
            # F7: ``_move`` for redo. Same reasoning as
            # rollback_move — atomic same-device, journalled EXDEV
            # for files, shutil fallback for directories (codex
            # PRRT_kwDOR_Rkws59hT9a).
            self._move(source, destination)

            # Refresh the dest inode baseline so a subsequent undo uses the
            # correct pin.  On EXDEV moves the inode is always new; on
            # same-device moves the inode is preserved but a refresh is
            # harmless.  POSIX only — Windows inodes are unreliable.
            if sys.platform != "win32":
                try:
                    st = os.lstat(destination)
                    operation.set_dest_inode(st.st_dev, st.st_ino)
                except OSError as exc:
                    logger.debug(
                        "redo_move: could not refresh dest inode for op %s at %s: %s",
                        operation.id,
                        destination,
                        exc,
                    )

            logger.info(f"Successfully redid move operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to redo move operation {operation.id}: {e}")
            return False

    def redo_rename(self, operation: Operation) -> bool:
        """Redo a rename operation.

        Args:
            operation: Rename operation to redo

        Returns:
            True if successful, False otherwise
        """
        old_name = operation.source_path
        new_name = operation.destination_path

        if new_name is None:
            logger.error(f"Cannot redo rename operation {operation.id}: no destination path")
            return False

        logger.info(f"Redoing rename: {old_name} -> {new_name}")

        try:
            # Rename
            old_name.rename(new_name)

            logger.info(f"Successfully redid rename operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to redo rename operation {operation.id}: {e}")
            return False

    def redo_delete(self, operation: Operation) -> bool:
        """Redo a delete operation (delete file again).

        Args:
            operation: Delete operation to redo

        Returns:
            True if successful, False otherwise
        """
        file_path = operation.source_path

        logger.info(f"Redoing delete: {file_path}")

        try:
            # Move to trash
            self._move_to_trash(file_path, operation.id)

            logger.info(f"Successfully redid delete operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to redo delete operation {operation.id}: {e}")
            return False

    def redo_copy(self, operation: Operation) -> bool:
        """Redo a copy operation (create copy again).

        Args:
            operation: Copy operation to redo

        Returns:
            True if successful, False otherwise
        """
        source = operation.source_path
        destination = operation.destination_path

        if destination is None:
            logger.error(f"Cannot redo copy operation {operation.id}: no destination path")
            return False

        logger.info(f"Redoing copy: {source} -> {destination}")

        try:
            # Ensure parent directory exists
            destination.parent.mkdir(parents=True, exist_ok=True)

            # Copy file
            if source.is_file():
                shutil.copy2(str(source), str(destination))
            elif source.is_dir():
                shutil.copytree(str(source), str(destination))
            else:
                logger.error(f"Source path is neither file nor directory: {source}")
                return False

            logger.info(f"Successfully redid copy operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to redo copy operation {operation.id}: {e}")
            return False

    def redo_create(self, operation: Operation) -> bool:
        """Redo a create operation (create file again).

        Args:
            operation: Create operation to redo

        Returns:
            True if successful, False otherwise
        """
        file_path = operation.source_path

        logger.info(f"Redoing create: {file_path}")

        try:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Create empty file or directory
            if operation.metadata.get("is_dir"):
                file_path.mkdir(parents=True, exist_ok=True)
            else:
                file_path.touch()

            logger.info(f"Successfully redid create operation {operation.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to redo create operation {operation.id}: {e}")
            return False

    def rollback_transaction(
        self, transaction_id: str, operations: list[Operation]
    ) -> RollbackResult:
        """Rollback an entire transaction atomically.

        Args:
            transaction_id: Transaction ID
            operations: List of operations in transaction

        Returns:
            RollbackResult with details
        """
        logger.info(f"Rolling back transaction {transaction_id} with {len(operations)} operations")

        rolled_back = 0
        failed = 0
        errors: list[tuple[int, str]] = []
        warnings: list[str] = []

        # Rollback operations in reverse order
        for operation in reversed(operations):
            try:
                success = self.rollback_operation(operation)
                if success:
                    rolled_back += 1
                else:
                    failed += 1
                    errors.append((operation.id or 0, "Rollback operation returned False"))
            except Exception as e:
                failed += 1
                errors.append((operation.id or 0, str(e)))
                logger.error(
                    f"Failed to rollback operation {operation.id} in transaction {transaction_id}: {e}"
                )

                # For atomic rollback, stop on first failure
                warnings.append(
                    f"Transaction rollback stopped at operation {operation.id}. "
                    f"{rolled_back} operations rolled back, {len(operations) - rolled_back - 1} pending."
                )
                break

        success = failed == 0
        result = RollbackResult(
            success=success,
            operations_rolled_back=rolled_back,
            operations_failed=failed,
            errors=errors,
            warnings=warnings,
        )

        if success:
            logger.info(
                f"Successfully rolled back transaction {transaction_id}: {rolled_back} operations"
            )
        else:
            logger.error(
                f"Failed to rollback transaction {transaction_id}: "
                f"{rolled_back} succeeded, {failed} failed"
            )

        return result

    def _move_to_trash(self, file_path: Path, operation_id: int | None = None) -> Path:
        """Move a file to trash.

        Args:
            file_path: File to move to trash
            operation_id: Operation ID for organizing trash

        Returns:
            Path in trash
        """
        # Create trash directory
        if operation_id:
            trash_dir = self.trash_dir / str(operation_id)
        else:
            import uuid

            trash_dir = self.trash_dir / str(uuid.uuid4())

        trash_dir.mkdir(parents=True, exist_ok=True)

        # F7: durable_move for files, shutil.move fallback for
        # directories — delegated to ``_move`` so every mover in this
        # class shares the same file-vs-directory dispatch (codex
        # PRRT_kwDOR_Rkws59hT9a). Symlinks route as files.
        trash_path = trash_dir / file_path.name
        self._move(file_path, trash_path)

        logger.debug(f"Moved {file_path} to trash: {trash_path}")
        return trash_path
