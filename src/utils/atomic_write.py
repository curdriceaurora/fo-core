"""Crash-safe atomic state-file writers (Epic B.atomic / B1a + B1b).

Every persistent state file this project writes (config YAML, suggestion
feedback, rule manager, JD system, PARA migration manifest, embedder
pickle cache, event discovery state, audit log, VS Code command stream,
...) must be crash-safe: a mid-write crash or a concurrent writer must
never leave the file truncated or half-written.

The fix is the temp-file-plus-``os.replace()`` pattern, exposed here via
four helpers:

- :func:`atomic_write_text`  — UTF-8 (or caller-specified) text payload.
- :func:`atomic_write_bytes` — in-memory binary payload.
- :func:`atomic_write_with`  — streaming-callback form for writers that
  stream into the handle (e.g. :func:`pickle.dump`) and would otherwise
  require buffering the full payload in RAM.
- :func:`append_durable`     — single-record fsynced append, for log-
  style files where order matters and truncate-replace is wrong (audit
  log, VS Code JSONL).

All four write to a temp file *in the destination's parent directory*,
``fsync`` the contents, ``os.replace`` into the final name (atomic on
POSIX and Windows for same-filesystem renames), then fsync the parent
directory on POSIX so the rename itself is durable. Directory fsync is a
no-op on Windows — see :func:`utils.atomic_io.fsync_directory`.

Invariants upheld by every helper:

1. **Atomicity.** A reader opening the target at any point during the
   write either sees the old contents or the new contents, never a
   mixture or an empty file.
2. **No temp-file residue.** On success *or* failure, no ``*.tmp``
   artifact is left in the destination directory.
3. **Surface missing parents.** The helpers do NOT auto-create parent
   directories. Callers that need ``mkdir -p`` must do it explicitly
   (preserves the existing :meth:`pathlib.Path.mkdir` discipline at
   every site and prevents silent masking of config-path bugs).
"""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from utils.atomic_io import fsync_directory

# Writer callback signature for ``atomic_write_with``. ``Any`` here is
# an intentional boundary type: the caller passes the correct
# text/binary IO object based on the ``mode`` they chose, and mypy
# can't track the mode-dependent handle type through a generic
# callable. The ``mode`` validation in ``atomic_write_with`` keeps
# misuse observable.
_WriterCallback = Callable[[Any], None]

# Permitted ``mode`` values for ``atomic_write_with`` — writable text
# or binary only. ``"a"`` / ``"r*"`` are rejected because an atomic
# writer that preserved prior contents would defeat the temp-file
# pattern (the point is to replace, not append; append-durable uses a
# separate helper).
_ATOMIC_WRITE_MODES = ("w", "wb")


def _fsync_and_replace(tmp_path: Path, target: Path) -> None:
    """Flush + fsync a temp file, atomically rename to target, fsync parent.

    Shared tail of every helper. Deliberately minimal — the surface
    helpers own mode selection and payload marshalling; this owns only
    the durability pattern.
    """
    # Caller is responsible for closing the handle before calling
    # this (we need the content flushed to disk before replacing).
    # ``os.replace`` is atomic on same-filesystem POSIX and Windows;
    # if it raises (cross-device EXDEV, permission denied, etc.) we
    # unlink the temp file so operators don't find orphan ``*.tmp``
    # files next to real state.
    try:
        os.replace(str(tmp_path), str(target))
    except OSError:
        # Best-effort cleanup. ``missing_ok=True`` swallows the race
        # where another process already removed the temp file.
        tmp_path.unlink(missing_ok=True)
        raise
    fsync_directory(target)


def _write_via_temp(
    target: Path,
    mode: str,
    write_fn: Callable[[Any], None],
    *,
    encoding: str | None = None,
) -> None:
    """Create a temp file in ``target.parent``, run ``write_fn(handle)``,
    fsync, atomically replace ``target``. Clean up temp on any exception.
    """
    # ``NamedTemporaryFile(delete=False)`` leaves the temp around so we
    # can ``os.replace`` it into place. ``dir=target.parent`` is
    # essential — cross-device renames raise ``EXDEV``.
    tmp_file = tempfile.NamedTemporaryFile(
        mode=mode,
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
        encoding=encoding,
    )
    tmp_path = Path(tmp_file.name)
    try:
        try:
            write_fn(tmp_file)
            # Flush Python-level buffer, then fsync the OS-level
            # file descriptor. Without the fsync, the content may
            # still live only in the kernel's page cache when the
            # subsequent ``os.replace`` runs — a power loss right
            # after the rename can then leave the destination
            # pointing at an inode with zero-length contents.
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        finally:
            tmp_file.close()
    except BaseException:
        # Any exception from the writer (including KeyboardInterrupt)
        # means we must not replace the target — unlink the partial
        # temp file and re-raise. ``missing_ok=True`` covers the
        # race where the writer itself already cleaned up.
        tmp_path.unlink(missing_ok=True)
        raise
    _fsync_and_replace(tmp_path, target)


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Atomically write ``content`` to ``path`` as text.

    Replaces the classic ``path.write_text(content, encoding=...)``
    pattern used across config / rule-manager / JD / PARA / suggestion-
    feedback state files. A mid-write crash leaves ``path`` pointing
    at the *prior* contents, never a truncated file.

    Args:
        path: Destination. Parent directory must already exist.
        content: UTF-8 (or ``encoding``-specified) text to write.
        encoding: Text encoding; ``"utf-8"`` by default. Passed through
            to the temp-file handle and used verbatim when writing.

    Raises:
        FileNotFoundError: Parent directory does not exist.
        OSError: Underlying filesystem error (permission, no space,
            cross-device rename, etc.). The target is never left in a
            partial state; no temp file remains.
    """

    def _writer(fh: io.TextIOBase) -> None:
        fh.write(content)

    _write_via_temp(path, "w", _writer, encoding=encoding)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Atomically write ``content`` to ``path`` as bytes.

    For binary state files whose payload already lives in memory
    (small caches, serialized headers, etc.). For streaming writers
    (``pickle.dump`` into a multi-MB cache), use
    :func:`atomic_write_with` instead so the full payload isn't
    duplicated in RAM.

    Args:
        path: Destination. Parent directory must already exist.
        content: Byte payload. Empty ``b""`` is valid and produces a
            zero-byte file.

    Raises:
        FileNotFoundError: Parent directory does not exist.
        OSError: Underlying filesystem error.
    """

    def _writer(fh: io.BufferedWriter) -> None:
        fh.write(content)

    _write_via_temp(path, "wb", _writer)


def atomic_write_with(
    path: Path,
    writer: _WriterCallback,
    *,
    mode: Literal["w", "wb"] = "wb",
) -> None:
    """Atomically write via a caller-supplied streaming ``writer`` callback.

    Use when the payload must stream directly into a file handle — most
    importantly :func:`pickle.dump` — rather than being fully
    materialised in memory first. The helper owns the temp-file /
    fsync / ``os.replace`` ceremony; the callback only sees the open
    handle.

    Mirrors the real usage site in
    ``src/services/deduplication/embedder.py`` where a multi-MB
    vectorizer or embedding cache is pickled out per save.

    Args:
        path: Destination. Parent directory must already exist.
        writer: Called with an open, writable file handle. The handle
            is closed by the helper after the callback returns; the
            callback MUST NOT close it.
        mode: ``"wb"`` (default, binary) or ``"w"`` (text). Any other
            value raises :class:`ValueError` — append / read modes do
            not fit the atomic-replace contract.

    Raises:
        ValueError: ``mode`` is not ``"w"`` or ``"wb"``.
        FileNotFoundError: Parent directory does not exist.
        OSError: Underlying filesystem error. Target is unchanged on
            failure; temp file is cleaned up.
        Exception: Any exception raised by ``writer`` propagates
            unchanged *after* the temp file is unlinked. The target
            is never replaced on writer failure.
    """
    if mode not in _ATOMIC_WRITE_MODES:
        raise ValueError(
            f"atomic_write_with mode must be 'w' or 'wb', got {mode!r}. "
            "Append / read modes are incompatible with the atomic-replace contract."
        )
    _write_via_temp(path, mode, writer)


def append_durable(path: Path, line: str, *, encoding: str = "utf-8") -> None:
    """Append one record to ``path`` with flush + fsync durability.

    For log-style files where order matters and truncate-replace is
    wrong (audit log, VS Code JSONL command stream). The helper:

    1. Opens ``path`` in append mode, creating it if absent.
    2. Writes ``line``, terminating with ``\\n`` if not already.
    3. Flushes the Python-level buffer and fsyncs the OS descriptor.

    Same-line atomicity for small (< PIPE_BUF) writes is a Linux
    guarantee when using O_APPEND; on other kernels a single
    ``write()`` call for the full record is still durable via the
    fsync. Records larger than PIPE_BUF (~4 KiB) across concurrent
    writers may interleave — callers that need strict ordering across
    processes should wrap this helper with a file lock.

    Args:
        path: Destination. Parent directory must already exist.
        line: Record text; ``\\n`` is auto-appended if absent.
        encoding: Text encoding; ``"utf-8"`` by default.

    Raises:
        FileNotFoundError: Parent directory does not exist.
        OSError: Underlying filesystem error.
    """
    terminated = line if line.endswith("\n") else line + "\n"
    # ``O_APPEND`` on POSIX makes every ``write()`` call atomic with
    # respect to other appenders — the kernel atomically advances the
    # write offset to EOF before each write. Pair with the fsync for
    # crash durability (flush alone leaves the record in the kernel
    # page cache).
    with path.open("a", encoding=encoding) as fh:
        fh.write(terminated)
        fh.flush()
        os.fsync(fh.fileno())
    fsync_directory(path)
