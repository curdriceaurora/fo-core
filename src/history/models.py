"""Data models for operation history tracking.

This module defines the data structures for operations and transactions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from enum import StrEnum


class OperationType(StrEnum):
    """Types of file operations that can be tracked."""

    MOVE = "move"
    RENAME = "rename"
    DELETE = "delete"
    COPY = "copy"
    CREATE = "create"


class OperationStatus(StrEnum):
    """Status of an operation."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class TransactionStatus(StrEnum):
    """Status of a transaction."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIALLY_ROLLED_BACK = "partially_rolled_back"


@dataclass
class Operation:
    """Represents a single file operation.

    Attributes:
        id: Unique operation ID (database primary key)
        operation_type: Type of operation (move, rename, delete, copy)
        timestamp: When the operation occurred
        source_path: Source file path
        destination_path: Destination file path (for move/rename/copy)
        file_hash: SHA256 hash of the file
        metadata: Additional metadata (size, type, permissions, etc.)
            Inode identity for TOCTOU prevention is stored here under the
            keys ``dest_dev`` / ``dest_ino`` (destination) and
            ``source_dev`` / ``source_ino`` (source).  Rows written before
            PR5 have ``None`` for these keys; ``rollback.py`` falls back to
            size + hash verification for those legacy rows.
        transaction_id: ID of the transaction this operation belongs to
        status: Current status of the operation
        error_message: Error message if operation failed
        created_at: When this record was created in the database
    """

    operation_type: OperationType
    timestamp: datetime
    source_path: Path
    id: int | None = None
    destination_path: Path | None = None
    file_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    transaction_id: str | None = None
    status: OperationStatus = OperationStatus.COMPLETED
    error_message: str | None = None
    created_at: datetime | None = None

    # ------------------------------------------------------------------
    # Inode-pin accessors (PR5 / #269)
    #
    # Stored in metadata rather than new SQL columns so the DB schema
    # stays at version 1.  Pre-PR5 rows have no keys → return None.
    # ------------------------------------------------------------------

    @property
    def dest_dev(self) -> int | None:
        """Device number of the destination file at move time, or None for legacy rows."""
        v = self.metadata.get("dest_dev")
        return int(v) if v is not None else None

    @property
    def dest_ino(self) -> int | None:
        """Inode number of the destination file at move time, or None for legacy rows."""
        v = self.metadata.get("dest_ino")
        return int(v) if v is not None else None

    @property
    def source_dev(self) -> int | None:
        """Device number of the source file at move time, or None for legacy rows."""
        v = self.metadata.get("source_dev")
        return int(v) if v is not None else None

    @property
    def source_ino(self) -> int | None:
        """Inode number of the source file at move time, or None for legacy rows."""
        v = self.metadata.get("source_ino")
        return int(v) if v is not None else None

    def set_dest_inode(self, dev: int, ino: int) -> None:
        """Record destination inode identity captured at move time."""
        self.metadata["dest_dev"] = dev
        self.metadata["dest_ino"] = ino

    def set_source_inode(self, dev: int, ino: int) -> None:
        """Record source inode identity captured at move time."""
        self.metadata["source_dev"] = dev
        self.metadata["source_ino"] = ino

    def to_dict(self) -> dict[str, Any]:
        """Convert operation to dictionary.

        Returns:
            Dictionary representation of the operation
        """
        return {
            "id": self.id,
            "operation_type": self.operation_type.value
            if isinstance(self.operation_type, OperationType)
            else self.operation_type,
            "timestamp": self.timestamp.isoformat()
            if isinstance(self.timestamp, datetime)
            else self.timestamp,
            "source_path": str(self.source_path),
            "destination_path": str(self.destination_path) if self.destination_path else None,
            "file_hash": self.file_hash,
            "metadata": self.metadata,
            "transaction_id": self.transaction_id,
            "status": self.status.value
            if isinstance(self.status, OperationStatus)
            else self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat()
            if self.created_at and isinstance(self.created_at, datetime)
            else self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Operation:
        """Create operation from dictionary.

        Args:
            data: Dictionary containing operation data

        Returns:
            Operation instance
        """
        # Parse operation type
        op_type = data["operation_type"]
        if isinstance(op_type, str):
            op_type = OperationType(op_type)

        # Parse timestamp
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

        # Parse paths
        source_path = Path(data["source_path"])
        dest_path = Path(data["destination_path"]) if data.get("destination_path") else None

        # Parse status
        status = data.get("status", "completed")
        if isinstance(status, str):
            status = OperationStatus(status)

        # Parse metadata
        metadata = data.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        # Parse created_at
        created_at = data.get("created_at")
        if created_at and isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))

        return cls(
            id=data.get("id"),
            operation_type=op_type,
            timestamp=timestamp,
            source_path=source_path,
            destination_path=dest_path,
            file_hash=data.get("file_hash"),
            metadata=metadata,
            transaction_id=data.get("transaction_id"),
            status=status,
            error_message=data.get("error_message"),
            created_at=created_at,
        )

    @classmethod
    def from_row(cls, row: Any) -> Operation:
        """Create operation from database row.

        Args:
            row: sqlite3.Row object

        Returns:
            Operation instance
        """
        data = dict(row)
        return cls.from_dict(data)


@dataclass
class Transaction:
    """Represents a batch of related operations.

    Attributes:
        transaction_id: Unique transaction ID (UUID)
        started_at: When the transaction started
        completed_at: When the transaction completed
        operation_count: Number of operations in this transaction
        status: Current status of the transaction
        metadata: Additional metadata about the transaction
    """

    transaction_id: str
    started_at: datetime
    status: TransactionStatus = TransactionStatus.IN_PROGRESS
    completed_at: datetime | None = None
    operation_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert transaction to dictionary.

        Returns:
            Dictionary representation of the transaction
        """
        return {
            "transaction_id": self.transaction_id,
            "started_at": self.started_at.isoformat()
            if isinstance(self.started_at, datetime)
            else self.started_at,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at and isinstance(self.completed_at, datetime)
            else self.completed_at,
            "operation_count": self.operation_count,
            "status": self.status.value
            if isinstance(self.status, TransactionStatus)
            else self.status,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        """Create transaction from dictionary.

        Args:
            data: Dictionary containing transaction data

        Returns:
            Transaction instance
        """
        # Parse timestamps
        started_at = data["started_at"]
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))

        completed_at = data.get("completed_at")
        if completed_at and isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))

        # Parse status
        status = data.get("status", "in_progress")
        if isinstance(status, str):
            status = TransactionStatus(status)

        # Parse metadata
        metadata = data.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        return cls(
            transaction_id=data["transaction_id"],
            started_at=started_at,
            completed_at=completed_at,
            operation_count=data.get("operation_count", 0),
            status=status,
            metadata=metadata,
        )

    @classmethod
    def from_row(cls, row: Any) -> Transaction:
        """Create transaction from database row.

        Args:
            row: sqlite3.Row object

        Returns:
            Transaction instance
        """
        data = dict(row)
        return cls.from_dict(data)
