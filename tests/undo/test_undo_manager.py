"""
Unit tests for UndoManager.

Tests high-level undo/redo management functionality.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from collections.abc import Generator
from pathlib import Path

import pytest

from history.models import Operation, OperationStatus, OperationType
from history.tracker import OperationHistory
from undo.rollback import RollbackExecutor
from undo.undo_manager import UndoManager
from undo.validator import OperationValidator


@pytest.mark.ci
@pytest.mark.unit
@pytest.mark.integration
class TestUndoManager(unittest.TestCase):
    """Test cases for UndoManager.

    Marked with ``ci``/``unit``/``integration`` so the transaction
    wrap's new code paths (``undo_operation`` / ``redo_operation`` /
    batch undo/redo) show up in the per-module integration coverage
    that the PR CI gate tracks for ``src/undo/undo_manager.py``. The
    existing happy-path assertions here exercise the single-row
    ``with self.history.db.transaction()`` wrappers end-to-end.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_history.db"
        self.trash_dir = self.test_dir / "trash"

        # Create components
        self.history = OperationHistory(db_path=self.db_path)
        self.validator = OperationValidator(trash_dir=self.trash_dir)
        self.executor = RollbackExecutor(validator=self.validator)
        self.manager = UndoManager(
            history=self.history, validator=self.validator, executor=self.executor
        )

        # Create test files
        self.source_file = self.test_dir / "source.txt"
        self.dest_file = self.test_dir / "dest.txt"
        self.source_file.write_text("test content")

    def tearDown(self):
        """Clean up test fixtures."""
        self.manager.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_undo_last_operation(self):
        """Test undoing the last operation."""
        # Log a move operation
        shutil.move(str(self.source_file), str(self.dest_file))
        self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )

        # Undo
        success = self.manager.undo_last_operation()

        self.assertTrue(success)
        self.assertTrue(self.source_file.exists())
        self.assertFalse(self.dest_file.exists())

        # Check operation status updated
        operations = self.history.get_operations(limit=1)
        self.assertEqual(operations[0].status, OperationStatus.ROLLED_BACK)

    def test_undo_operation_by_id(self):
        """Test undoing a specific operation."""
        # Log operation
        shutil.move(str(self.source_file), str(self.dest_file))
        op_id = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )

        # Undo by ID
        success = self.manager.undo_operation(op_id)

        self.assertTrue(success)
        self.assertTrue(self.source_file.exists())
        self.assertFalse(self.dest_file.exists())

    def test_undo_transaction(self):
        """Test undoing an entire transaction."""
        # Start transaction
        txn_id = self.history.start_transaction(metadata={"test": "transaction"})

        # Log multiple operations
        file1 = self.test_dir / "file1.txt"
        dest1 = self.test_dir / "dest1.txt"
        file1.write_text("content1")
        shutil.move(str(file1), str(dest1))
        self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=file1,
            destination_path=dest1,
            transaction_id=txn_id,
        )

        file2 = self.test_dir / "file2.txt"
        dest2 = self.test_dir / "dest2.txt"
        file2.write_text("content2")
        shutil.move(str(file2), str(dest2))
        self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=file2,
            destination_path=dest2,
            transaction_id=txn_id,
        )

        # Commit transaction
        self.history.commit_transaction(txn_id)

        # Undo transaction
        success = self.manager.undo_transaction(txn_id)

        self.assertTrue(success)
        self.assertTrue(file1.exists())
        self.assertFalse(dest1.exists())
        self.assertTrue(file2.exists())
        self.assertFalse(dest2.exists())

    def test_redo_last_operation(self):
        """Test redoing the last rolled back operation."""
        # Log and undo operation
        shutil.move(str(self.source_file), str(self.dest_file))
        self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )
        self.manager.undo_last_operation()

        # Redo
        success = self.manager.redo_last_operation()

        self.assertTrue(success)
        self.assertFalse(self.source_file.exists())
        self.assertTrue(self.dest_file.exists())

        # Check status updated
        operations = self.history.get_operations(limit=1)
        self.assertEqual(operations[0].status, OperationStatus.COMPLETED)

    def test_redo_operation_by_id(self):
        """Test redoing a specific operation."""
        # Log and undo operation
        shutil.move(str(self.source_file), str(self.dest_file))
        op_id = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )
        self.manager.undo_operation(op_id)

        # Redo by ID
        success = self.manager.redo_operation(op_id)

        self.assertTrue(success)
        self.assertFalse(self.source_file.exists())
        self.assertTrue(self.dest_file.exists())

    def test_can_undo(self):
        """Test checking if operation can be undone."""
        # Log operation
        shutil.move(str(self.source_file), str(self.dest_file))
        op_id = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )

        can_undo, reason = self.manager.can_undo(op_id)

        self.assertTrue(can_undo)
        self.assertIn("can be undone", reason)

    def test_can_undo_already_rolled_back(self):
        """Test checking undo on already rolled back operation."""
        # Log and undo operation
        shutil.move(str(self.source_file), str(self.dest_file))
        op_id = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )
        self.manager.undo_operation(op_id)

        can_undo, reason = self.manager.can_undo(op_id)

        self.assertFalse(can_undo)
        self.assertIn("already been rolled back", reason)

    def test_can_redo(self):
        """Test checking if operation can be redone."""
        # Log and undo operation
        shutil.move(str(self.source_file), str(self.dest_file))
        op_id = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )
        self.manager.undo_operation(op_id)

        can_redo, reason = self.manager.can_redo(op_id)

        self.assertTrue(can_redo)
        self.assertIn("can be redone", reason)

    def test_can_redo_not_rolled_back(self):
        """Test checking redo on non-rolled-back operation."""
        # Log operation
        shutil.move(str(self.source_file), str(self.dest_file))
        op_id = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )

        can_redo, reason = self.manager.can_redo(op_id)

        self.assertFalse(can_redo)
        self.assertIn("rolled back", reason)

    def test_get_undo_stack(self):
        """Test getting undo stack."""
        # Log operations
        shutil.move(str(self.source_file), str(self.dest_file))
        self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )

        undo_stack = self.manager.get_undo_stack()

        self.assertEqual(len(undo_stack), 1)
        self.assertEqual(undo_stack[0].status, OperationStatus.COMPLETED)

    def test_get_redo_stack(self):
        """Test getting redo stack."""
        # Log and undo operation
        shutil.move(str(self.source_file), str(self.dest_file))
        op_id = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )
        self.manager.undo_operation(op_id)

        redo_stack = self.manager.get_redo_stack()

        self.assertEqual(len(redo_stack), 1)
        self.assertEqual(redo_stack[0].status, OperationStatus.ROLLED_BACK)

    def test_undo_no_operations(self):
        """Test undo when no operations exist."""
        success = self.manager.undo_last_operation()

        self.assertFalse(success)

    def test_redo_no_operations(self):
        """Test redo when no rolled back operations exist."""
        success = self.manager.redo_last_operation()

        self.assertFalse(success)

    def test_undo_invalid_operation_id(self):
        """Test undo with invalid operation ID."""
        success = self.manager.undo_operation(99999)

        self.assertFalse(success)

    def test_redo_invalid_operation_id(self):
        """Test redo with invalid operation ID."""
        success = self.manager.redo_operation(99999)

        self.assertFalse(success)


@pytest.mark.ci
@pytest.mark.unit
@pytest.mark.integration
class TestUndoManagerTransactionWrap(unittest.TestCase):
    """Tests for the B3 transaction-wrap invariants.

    The undo/redo status flips now go through
    ``DatabaseManager.transaction()`` (single atomic commit per logical
    operation, rollback on exception). These tests exercise the
    rollback path so a regression that reintroduces the old
    ``execute_query`` + ``get_connection().commit()`` pair — where the
    lock was released between write and commit, and a mid-commit crash
    could leave the DB ahead of the filesystem rollback — fails loudly.
    """

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_history.db"
        self.trash_dir = self.test_dir / "trash"

        self.history = OperationHistory(db_path=self.db_path)
        self.validator = OperationValidator(trash_dir=self.trash_dir)
        self.executor = RollbackExecutor(validator=self.validator)
        self.manager = UndoManager(
            history=self.history, validator=self.validator, executor=self.executor
        )

        self.source_file = self.test_dir / "source.txt"
        self.dest_file = self.test_dir / "dest.txt"
        self.source_file.write_text("test content")

    def tearDown(self) -> None:
        self.manager.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _logged_move(self) -> int:
        """Perform a MOVE + log it, return the operation id."""
        shutil.move(str(self.source_file), str(self.dest_file))
        op_id = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=self.source_file,
            destination_path=self.dest_file,
        )
        assert op_id is not None
        return op_id

    def test_undo_operation_db_failure_rolls_back_status(self) -> None:
        """B3 invariant: if the UPDATE inside the transaction raises
        (e.g. simulated disk failure mid-write), the in-flight
        transaction is rolled back before the exception propagates.
        Pre-B3 the pattern was ``execute_query`` + separate
        ``commit()`` with the db lock released in between, so a
        concurrent writer could slip a commit between the two calls
        and leave the undo half-landed. Now a single ``with
        transaction()`` block holds the lock across the whole write.
        """
        op_id = self._logged_move()

        # Wrap the db's ``transaction`` generator so the yielded
        # connection raises on the next ``execute`` call. sqlite3's
        # ``Connection`` is a C type with read-only attributes, so we
        # wrap it in a proxy that forwards everything except
        # ``execute``. End-to-end this still exercises the real
        # transaction context manager's rollback path.
        from contextlib import contextmanager

        class _FailingExecuteConn:
            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def execute(self, *_args: object, **_kwargs: object) -> sqlite3.Cursor:
                raise sqlite3.OperationalError("simulated disk-full on execute")

            def __getattr__(self, name: str) -> object:
                return getattr(self._real, name)

        real_transaction = self.history.db.transaction

        @contextmanager
        def _transaction_with_failing_execute() -> Generator[object, None, None]:
            with real_transaction() as conn:
                yield _FailingExecuteConn(conn)

        self.history.db.transaction = _transaction_with_failing_execute  # type: ignore[method-assign]
        try:
            with pytest.raises(sqlite3.OperationalError, match="simulated disk-full"):
                self.manager.undo_operation(op_id)
        finally:
            self.history.db.transaction = real_transaction  # type: ignore[method-assign]

        # Status must not have been persisted — the row stays in
        # whatever state it was in before the failed UPDATE.
        row = self.history.db.fetch_one("SELECT status FROM operations WHERE id = ?", (op_id,))
        assert row is not None
        assert row["status"] != OperationStatus.ROLLED_BACK.value, (
            "ROLLED_BACK status leaked through despite write failure"
        )

    def test_redo_transaction_aborts_and_rolls_back_on_executor_failure(self) -> None:
        """B3 invariant for the multi-row case: if any per-operation
        redo fails mid-transaction, EVERY status UPDATE already done
        in this batch must be rolled back — the DB must not show some
        operations as COMPLETED while others stay ROLLED_BACK just
        because they happened to land before the failing row.

        Also asserts ``DatabaseManager.transaction()`` was entered
        exactly once for the whole batch (copilot
        PRRT_kwDOR_Rkws59M7UW). Without this, the test would still
        pass against a pre-B3 implementation that did per-op
        ``execute_query`` + manual ``conn.rollback()`` on failure —
        the rollback path happens to produce the same end state in
        this particular scenario, but doesn't hold the db lock
        across the loop, which is the regression this PR is about.
        """
        # Build two MOVE operations in a transaction, undo both so
        # they're in ROLLED_BACK state ready for a redo.
        src_a = self.test_dir / "a_src.txt"
        src_a.write_text("a")
        dst_a = self.test_dir / "a_dst.txt"
        src_b = self.test_dir / "b_src.txt"
        src_b.write_text("b")
        dst_b = self.test_dir / "b_dst.txt"

        txn_id = self.history.start_transaction()
        shutil.move(str(src_a), str(dst_a))
        op_a = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=src_a,
            destination_path=dst_a,
            transaction_id=txn_id,
        )
        shutil.move(str(src_b), str(dst_b))
        op_b = self.history.log_operation(
            operation_type=OperationType.MOVE,
            source_path=src_b,
            destination_path=dst_b,
            transaction_id=txn_id,
        )
        self.history.commit_transaction(txn_id)
        assert self.manager.undo_transaction(txn_id) is True

        # Force the SECOND per-operation redo to fail.
        real_redo = self.executor.redo_operation
        redo_calls = {"n": 0}

        def _redo_second_fails(operation: Operation) -> bool:
            redo_calls["n"] += 1
            if redo_calls["n"] == 2:
                return False
            return real_redo(operation)

        self.executor.redo_operation = _redo_second_fails  # type: ignore[method-assign]

        # Wrap ``db.transaction`` to count entries — asserting it was
        # entered exactly once proves the batch went through a single
        # context-manager scope, not a per-op ``execute_query`` loop.
        from contextlib import contextmanager

        real_transaction = self.history.db.transaction
        transaction_entries = {"n": 0}

        @contextmanager
        def _counting_transaction() -> Generator[sqlite3.Connection, None, None]:
            transaction_entries["n"] += 1
            with real_transaction() as conn:
                yield conn

        self.history.db.transaction = _counting_transaction  # type: ignore[method-assign]

        try:
            success = self.manager.redo_transaction(txn_id)
        finally:
            self.executor.redo_operation = real_redo  # type: ignore[method-assign]
            self.history.db.transaction = real_transaction  # type: ignore[method-assign]

        assert success is False
        assert transaction_entries["n"] == 1, (
            f"expected redo_transaction to enter db.transaction() exactly once, "
            f"got {transaction_entries['n']}"
        )
        # Neither operation's status should have flipped — the
        # transaction was rolled back by the context manager when the
        # _RedoAborted sentinel propagated.
        for op_id in (op_a, op_b):
            row = self.history.db.fetch_one("SELECT status FROM operations WHERE id = ?", (op_id,))
            assert row is not None
            assert row["status"] == OperationStatus.ROLLED_BACK.value, (
                f"operation {op_id} status leaked as {row['status']} despite mid-batch redo failure"
            )


if __name__ == "__main__":
    unittest.main()
