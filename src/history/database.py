"""SQLite database manager for operation history tracking.

This module provides database connection management, schema creation,
and migration support for the operation history system.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import cast

logger = logging.getLogger(__name__)


class DatabaseCorruptionError(RuntimeError):
    """F5 (hardening roadmap #159): raised when ``PRAGMA integrity_check``
    detects on-disk corruption during ``DatabaseManager.initialize``.

    Pre-F5 a corrupt ``history.db`` would silently propagate through
    every subsequent operation — indexes returning wrong rows, inserts
    blowing up with obscure errors. Post-F5 the database layer refuses
    to open a corrupt file and surfaces a specific exception carrying
    the corrupt file's path so CLI hooks can render a quarantine
    prompt.

    The message is deliberately actionable: it lists the corrupt path
    and the ``mv``-aside + reinit recovery step so operators can
    follow the instruction without reading the source.

    Attributes:
        db_path: Path of the corrupt database file (for quarantine UI).
        integrity_errors: Raw output of ``PRAGMA integrity_check`` —
            verbose diagnostic for logs / bug reports.
    """  # noqa: D205

    def __init__(self, db_path: Path, integrity_errors: list[str]) -> None:
        """Build the corruption error with the actionable recovery message."""
        self.db_path = db_path
        self.integrity_errors = integrity_errors
        details = "; ".join(integrity_errors[:5]) or "no detail"
        more = f" (and {len(integrity_errors) - 5} more)" if len(integrity_errors) > 5 else ""
        super().__init__(
            f"History database at {db_path} is corrupt: {details}{more}. "
            f"Quarantine the file (e.g. ``mv {db_path} {db_path}.corrupt``) "
            "and rerun the command — a fresh database will be created."
        )


class DatabaseManager:
    """Manages SQLite database connections and schema for operation history."""

    # Database schema version
    SCHEMA_VERSION = 1

    # SQL schema definitions
    SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        source_path TEXT NOT NULL,
        destination_path TEXT,
        file_hash TEXT,
        metadata TEXT,
        transaction_id TEXT,
        status TEXT NOT NULL DEFAULT 'completed',
        error_message TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS transactions (
        transaction_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        operation_count INTEGER DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'in_progress',
        metadata TEXT
    );

    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_operations_timestamp ON operations(timestamp);
    CREATE INDEX IF NOT EXISTS idx_operations_transaction ON operations(transaction_id);
    CREATE INDEX IF NOT EXISTS idx_operations_type ON operations(operation_type);
    CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status);
    CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
    """

    def __init__(self, db_path: Path | None = None):
        """Initialize database manager.

        Args:
            db_path: Path to SQLite database file.
                    Defaults to ``history/history.db`` in the XDG data
                    directory resolved by ``PathManager`` (with automatic
                    legacy path migration).
        """
        if db_path is None:
            from config.path_manager import get_data_dir
            from config.path_migration import resolve_legacy_path

            legacy_dir = Path.home() / ".fo"
            new_dir = get_data_dir() / "history"
            resolved = resolve_legacy_path(new_dir, legacy_dir)
            db_path = resolved / "history.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection: sqlite3.Connection | None = None
        self._lock = Lock()
        self._initialized = False

        logger.info(f"Database manager initialized with path: {self.db_path}")

    def initialize(self) -> None:
        """Initialize database schema and enable WAL mode."""
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            logger.info("Initializing database schema...")
            conn = self.get_connection()

            try:
                # F5 (hardening roadmap #159): integrity_check FIRST,
                # before any other pragma or schema work. Opening a
                # corrupt SQLite file via ``connect()`` often succeeds,
                # but the next pragma (``journal_mode=WAL``) can raise
                # ``DatabaseError: database disk image is malformed``
                # on truncated files — an opaque signal that hides
                # which file needs quarantine. Running integrity_check
                # first normalizes both the "returns error rows" and
                # "raises DatabaseError" failure modes into a single
                # typed :class:`DatabaseCorruptionError` with the path,
                # giving the CLI layer a clean hook for a quarantine
                # prompt.
                #
                # On a fresh file (no pages yet), integrity_check
                # returns ``[("ok",)]`` without touching WAL state, so
                # this is a no-op cost for the common path.
                self._check_integrity_locked(conn)

                # Enable WAL mode for better concurrent access
                conn.execute("PRAGMA journal_mode=WAL")

                # Enable foreign keys
                conn.execute("PRAGMA foreign_keys=ON")

                # Create schema
                conn.executescript(self.SCHEMA_SQL)

                # Check and update schema version
                cursor = conn.execute(
                    "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
                )
                result = cursor.fetchone()

                if result is None:
                    # First time setup
                    conn.execute(
                        "INSERT INTO schema_version (version) VALUES (?)", (self.SCHEMA_VERSION,)
                    )
                    logger.info(f"Database schema version {self.SCHEMA_VERSION} created")
                else:
                    current_version = result[0]
                    if current_version < self.SCHEMA_VERSION:
                        self._migrate(current_version, self.SCHEMA_VERSION, conn)
                    logger.info(f"Database schema version: {current_version}")

                conn.commit()
                self._initialized = True
                logger.info("Database initialization complete")

            except DatabaseCorruptionError:
                # F5: propagate unchanged. Don't attempt ``conn.rollback``
                # on a corrupt connection — it can raise a secondary
                # DatabaseError that masks the actionable corruption
                # message. Nothing to roll back: we raise before any
                # schema mutation runs.
                raise
            except Exception as e:
                # Rollback failure is swallowed (via
                # ``contextlib.suppress``) so it can't mask the
                # original initialization error. Rollback on certain
                # corrupt-state transitions raises a secondary
                # DatabaseError that's less informative than the
                # primary cause.
                with contextlib.suppress(Exception):
                    conn.rollback()
                logger.error("Database initialization failed: %s", e, exc_info=True)
                raise

    def check_integrity(self) -> None:
        """F5 (hardening roadmap #159): run ``PRAGMA integrity_check``.

        Raises :class:`DatabaseCorruptionError` if any page fails the
        check. On a clean database ``PRAGMA integrity_check`` returns
        exactly one row ``("ok",)``; anything else is a corruption
        indicator.

        This is idempotent and safe to call any time — used inside
        :meth:`initialize` once at startup and exposed publicly so
        diagnostic tools / CLI doctor commands can poll it.

        Raises:
            DatabaseCorruptionError: If integrity_check fails.
            sqlite3.OperationalError: If the file can't be opened at all
                (e.g. permission denied, missing file).
        """
        conn = self.get_connection()
        self._check_integrity_locked(conn)

    def _check_integrity_locked(self, conn: sqlite3.Connection) -> None:
        """Internal helper: run integrity_check on an existing connection.

        Takes an open connection so the caller can run the check
        inside their own lock/transaction without re-acquiring. The
        ``initialize`` path calls this while already holding
        ``self._lock``.

        Split into a raw-fetch step and a validation step so the
        "rows returned but not ok" branch is exercisable from tests
        without needing byte-level SQLite corruption (which Python's
        sqlite3 authorizer blocks — ``writable_schema=ON`` is
        refused for ``sqlite_master`` updates from the binding).
        """
        # PRAGMA integrity_check has two failure modes:
        # 1. Returns rows describing the damage (one row per error, up
        #    to the default cap of 100).
        # 2. Raises ``sqlite3.DatabaseError`` directly when the damage
        #    is severe enough that it can't even walk the pages
        #    (truncated header, unreadable root page, etc.).
        # Both are corruption signals — normalize to our typed
        # exception so callers don't have to catch raw DatabaseError
        # and guess.
        try:
            cursor = conn.execute("PRAGMA integrity_check")
            rows = cursor.fetchall()
        except sqlite3.DatabaseError as exc:
            logger.error(
                "PRAGMA integrity_check raised %s on %s",
                exc,
                self.db_path,
            )
            raise DatabaseCorruptionError(self.db_path, [str(exc)]) from exc
        self._validate_integrity_rows(rows)

    def _validate_integrity_rows(
        self,
        rows: list[tuple[object, ...]] | list[sqlite3.Row],
    ) -> None:
        """Validate the rows returned by ``PRAGMA integrity_check``.

        Clean result is exactly one row with value ``"ok"``. Anything
        else (multiple rows, or a single row whose first column isn't
        ``"ok"``) is a corruption report — raise
        :class:`DatabaseCorruptionError` carrying the diagnostic
        messages.

        Exposed as a separate helper (rather than inlined in
        :meth:`_check_integrity_locked`) so tests can cover the
        non-ok branch directly without needing to craft a corrupt
        file that sqlite3's Python binding will accept.
        """
        messages = [row[0] for row in rows if row and row[0] is not None]
        if messages == ["ok"]:
            return
        logger.error(
            "PRAGMA integrity_check failed on %s: %s",
            self.db_path,
            messages,
        )
        raise DatabaseCorruptionError(self.db_path, messages)

    def _migrate(self, from_version: int, to_version: int, conn: sqlite3.Connection) -> None:
        """Perform database migration from one version to another.

        Args:
            from_version: Current schema version
            to_version: Target schema version
            conn: Database connection
        """
        logger.info(f"Migrating database from version {from_version} to {to_version}")

        # Future migrations will be added here
        # For now, this is a placeholder for migration logic

        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (to_version,))
        logger.info(f"Migration to version {to_version} complete")

    def get_connection(self) -> sqlite3.Connection:
        """Get or create database connection.

        Returns:
            SQLite database connection
        """
        if self._connection is None:
            self._connection = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=30.0
            )
            # Enable row factory for easier data access
            self._connection.row_factory = sqlite3.Row

        return self._connection

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database transactions.

        Yields:
            Database connection

        Example:
            with db.transaction() as conn:
                conn.execute("INSERT INTO operations ...")
        """
        with self._lock:
            conn = self.get_connection()
            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"Transaction failed: {e}")
                raise

    def execute_query(self, query: str, params: tuple[object, ...] | None = None) -> sqlite3.Cursor:
        """Execute a SQL query with optional parameters.

        Args:
            query: SQL query string
            params: Query parameters tuple

        Returns:
            Query cursor
        """
        with self._lock:
            conn = self.get_connection()
            if params is None:
                return conn.execute(query)
            else:
                return conn.execute(query, params)

    def execute_many(self, query: str, params_list: list[tuple[object, ...]]) -> None:
        """Execute a SQL query with multiple parameter sets (batch insert).

        Args:
            query: SQL query string
            params_list: List of parameter tuples
        """
        with self.transaction() as conn:
            conn.executemany(query, params_list)

    def fetch_one(self, query: str, params: tuple[object, ...] | None = None) -> sqlite3.Row | None:
        """Execute query and fetch one result.

        Args:
            query: SQL query string
            params: Query parameters tuple

        Returns:
            Single row result or None
        """
        cursor = self.execute_query(query, params)
        return cast("sqlite3.Row | None", cursor.fetchone())

    def fetch_all(self, query: str, params: tuple[object, ...] | None = None) -> list[sqlite3.Row]:
        """Execute query and fetch all results.

        Args:
            query: SQL query string
            params: Query parameters tuple

        Returns:
            List of row results
        """
        cursor = self.execute_query(query, params)
        return cursor.fetchall()

    def get_database_size(self) -> int:
        """Get current database file size in bytes.

        Returns:
            Database size in bytes
        """
        if self.db_path.exists():
            return self.db_path.stat().st_size
        return 0

    def get_operation_count(self) -> int:
        """Get total number of operations in database.

        Returns:
            Total operation count
        """
        result = self.fetch_one("SELECT COUNT(*) as count FROM operations")
        return result["count"] if result else 0

    def vacuum(self) -> None:
        """Vacuum the database to reclaim space and optimize performance."""
        logger.info("Vacuuming database...")
        conn = self.get_connection()
        conn.execute("VACUUM")
        logger.info("Database vacuum complete")

    def close(self) -> None:
        """Close database connection."""
        if self._connection is not None:
            try:
                self._connection.close()
                self._connection = None
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database connection: {e}")

    def __enter__(self) -> DatabaseManager:
        """Context manager entry."""
        self.initialize()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Context manager exit."""
        self.close()
