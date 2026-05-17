r"""POSIX-safe directory-fd filesystem operations.

Implementation of #266 in the security hardening series (#264). The
``SafeDir`` primitive holds an open directory file descriptor and routes
every operation through ``dir_fd=`` + ``O_NOFOLLOW``. PRs 3–6 thread it
through the read-side ingestion, dedupe, undo, and watcher paths.

Design invariants:

- **Every method takes a path *component*, never a path.** Names
  containing ``/``, ``\\``, ``..``, ``.``, NUL, or empty strings are
  rejected with ``ValueError`` before any syscall. This makes
  attacker-controlled segments — file content, AI output, watcher event
  payloads — unable to escape the held directory.
- **``O_NOFOLLOW`` is always set on opens.** On Linux this raises
  ``ELOOP`` when the named entry is a symlink; we translate that to a
  typed ``SymlinkRejected`` (subclass of ``OSError``) so callers can
  catch the security-relevant case specifically while generic
  ``except OSError`` paths continue to work.
- **No path-string reconstruction.** Every syscall uses
  ``dir_fd=self.fd`` with the bare component. There is no code path
  that builds ``str(self.path) + "/" + name`` and hands it to a syscall.
- **fd lifetime is via context manager only.** The ``__exit__`` releases
  the held fd; double-exit is harmless. The class deliberately does not
  expose a public ``close()`` to discourage leak-prone usage patterns.

Platform: POSIX only. Windows has no equivalent for ``dir_fd=`` /
``O_NOFOLLOW``; ``open_root`` raises ``NotImplementedError`` there. PR
CI is Linux; nightly Windows runs skip the dependent test modules. See
#264 for the deferral rationale.
"""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Self

__all__ = ["SafeDir", "SymlinkRejected"]


_INVALID_NAME_CHARS = frozenset({"/", "\\", "\x00"})
_RESERVED_NAMES = frozenset({"", ".", ".."})


class SymlinkRejected(OSError):
    """Raised when a SafeDir operation refuses to dereference a symlink.

    Subclasses ``OSError`` so existing ``except OSError`` paths continue
    to work while security-relevant callers can catch this specifically.
    The original ``OSError`` (``errno=ELOOP``) is wrapped, not replaced —
    ``__cause__`` / ``__context__`` carry the underlying error.
    """


def _validate_name(name: str) -> None:
    r"""Reject anything that isn't a single safe path component.

    Raises ``ValueError`` for any of:

    - Reserved (``""``, ``"."``, ``".."``).
    - Contains path-separator characters (``/`` on POSIX, plus ``\\``
      defensively — even on POSIX, ``\\`` in a filename is unusual and
      catching it here matches the Windows-portable invariant if/when
      ``SafeDir`` ships on Windows).
    - Contains NUL.

    This runs *before* any syscall so traversal payloads never reach the
    kernel.
    """
    if not isinstance(name, str):
        raise ValueError(f"name must be str, got {type(name).__name__}")
    if name in _RESERVED_NAMES:
        raise ValueError(f"reserved component name: {name!r}")
    for ch in name:
        if ch in _INVALID_NAME_CHARS:
            raise ValueError(f"name contains forbidden character {ch!r}: {name!r}")


def _wrap_open_errno(err: OSError, name: str) -> OSError:
    """Translate symlink-related open failures into ``SymlinkRejected``.

    On Linux, ``os.open(path, O_DIRECTORY | O_NOFOLLOW)`` against a
    symlink-to-directory returns ``ENOTDIR`` (because the symlink inode
    itself isn't a directory) rather than ``ELOOP``. On a regular-file
    target with ``O_NOFOLLOW`` you get ``ELOOP``. Callers can't tell
    these apart from the errno alone, so we map both through this
    helper. Non-symlink causes (``ENOTDIR`` against a regular file when
    ``O_DIRECTORY`` was set) are passed through unchanged.

    Disambiguation requires an extra ``lstat`` — see
    ``_check_symlink_under`` below.
    """
    if err.errno == errno.ELOOP:
        wrapped = SymlinkRejected(
            err.errno,
            f"refused to open symlinked entry: {name!r}",
            err.filename,
        )
        wrapped.__cause__ = err
        return wrapped
    return err


def _raise_symlink_rejected(name: str, cause: OSError) -> None:
    """Raise ``SymlinkRejected`` chained to *cause*."""
    wrapped = SymlinkRejected(
        cause.errno,
        f"refused to open symlinked entry: {name!r}",
        cause.filename,
    )
    wrapped.__cause__ = cause
    raise wrapped


def _is_symlink_at(name: str, dir_fd: int) -> bool:
    """True if *name* under *dir_fd* is a symlink, False otherwise.

    Used after an open fails to disambiguate ``ENOTDIR``-on-symlink from
    ``ENOTDIR``-on-regular-file. Returns False on any stat failure (the
    entry vanished, permissions changed, etc.) — the caller will then
    re-raise the original open error, which is the correct behavior:
    the open didn't follow a symlink, so we don't owe a
    ``SymlinkRejected``.
    """
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    import stat as _stat

    return _stat.S_ISLNK(st.st_mode)


class SafeDir:
    """Holds an open directory fd; every operation uses ``dir_fd=`` + ``O_NOFOLLOW``.

    Construct via ``SafeDir.open_root(path)`` — direct construction is
    not part of the public API. Always use as a context manager so the
    fd is released:

        with SafeDir.open_root(organize_root) as sd:
            with sd.open_subdir("documents") as sub:
                for entry in sub.scandir():
                    ...
    """

    __slots__ = ("_fd", "_closed")

    def __init__(self, fd: int) -> None:
        """Wrap an already-open directory fd.

        Internal — public construction goes through ``open_root``;
        ``open_subdir`` uses this directly to wrap the fd it has just
        opened. The constructor does not validate that *fd* is a
        directory fd; callers must.
        """
        self._fd = fd
        self._closed = False

    # ------------------------------------------------------------------
    # Construction & lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def open_root(cls, path: Path) -> Self:
        """Open *path* as the root of a SafeDir.

        *path* is the only place a multi-component path enters this API —
        every subsequent operation must use single component names.

        Raises:
            NotImplementedError: On Windows. ``dir_fd=`` and
                ``O_NOFOLLOW`` are POSIX-only; the Windows port is
                deferred (see #264).
            SymlinkRejected: If *path* itself is a symlink. The caller
                must hand a real directory; we don't follow whatever it
                might point at.
            OSError: If *path* doesn't exist, isn't a directory, or
                can't be opened.
        """
        if sys.platform == "win32":  # pragma: no cover - platform skip
            raise NotImplementedError(
                "SafeDir requires POSIX dir_fd / O_NOFOLLOW support; Windows port deferred (#264)"
            )
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(str(path), flags)
        except OSError as exc:
            # ``O_DIRECTORY | O_NOFOLLOW`` against a symlink-to-dir returns
            # ENOTDIR on Linux (the symlink inode isn't a directory).
            # Disambiguate via lstat — symlink → SymlinkRejected, anything
            # else → propagate.
            if exc.errno == errno.ELOOP or (exc.errno == errno.ENOTDIR and path.is_symlink()):
                _raise_symlink_rejected(str(path), exc)
            raise _wrap_open_errno(exc, str(path)) from exc
        return cls(fd)

    @property
    def fd(self) -> int:
        """Underlying directory fd (read-only, intended for diagnostics).

        Callers should *not* perform syscalls on this fd directly — use
        the methods on this class instead, which keep the
        ``dir_fd=`` + ``O_NOFOLLOW`` invariant.
        """
        return self._fd

    def __enter__(self) -> Self:
        """Enter the context manager; return *self* unchanged."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the held directory fd. Idempotent on double-exit.

        Closing an already-closed fd would raise ``EBADF``, which is
        noise in failure-path teardown; suppress it.
        """
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._fd)
        except OSError:  # pragma: no cover - defensive
            pass

    # ------------------------------------------------------------------
    # Open: file / subdirectory / reader-fd
    # ------------------------------------------------------------------

    def open_child(self, name: str, *, flags: int = os.O_RDONLY) -> int:
        """Open the entry *name* under this directory.

        Always sets ``O_NOFOLLOW``; if the entry is a symlink, raises
        ``SymlinkRejected``. *flags* is OR-ed in on top so callers can
        request e.g. ``os.O_WRONLY | os.O_CREAT`` for non-content
        operations (the SafeDir invariant still holds: the open is
        anchored to ``self.fd`` and never follows a symlink).

        Returns the opened fd. Caller is responsible for closing it
        (use ``os.fdopen`` / ``os.close`` / ``contextlib.closing``).
        """
        _validate_name(name)
        try:
            return os.open(name, flags | os.O_NOFOLLOW, dir_fd=self._fd)
        except OSError as exc:
            raise _wrap_open_errno(exc, name) from exc

    def open_for_reader(self, name: str) -> int:
        """Open *name* read-only with ``O_NOFOLLOW`` and return its fd.

        The canonical 'pass to a content reader' helper. Wrap with
        ``os.fdopen(fd, "rb")`` and hand to ``fitz.open(stream=...)`` /
        ``Image.open(...)`` / ``pypdf.PdfReader(...)`` / etc.

        Raises:
            ValueError: If *name* isn't a safe component.
            SymlinkRejected: If *name* is a symlink.
            FileNotFoundError: If *name* doesn't exist.
            OSError: For other open failures (permission, etc.).
        """
        return self.open_child(name, flags=os.O_RDONLY)

    def open_subdir(self, name: str) -> SafeDir:
        """Open subdirectory *name* and return a new SafeDir wrapping its fd.

        Use as a context manager so the new fd is released:

            with sd.open_subdir("category") as sub:
                ...
        """
        _validate_name(name)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(name, flags, dir_fd=self._fd)
        except OSError as exc:
            # ENOTDIR can mean "the entry is a symlink (whose own inode
            # isn't a directory)" or "the entry is a regular file".
            # ``_is_symlink_at`` resolves the ambiguity.
            if exc.errno == errno.ELOOP or (
                exc.errno == errno.ENOTDIR and _is_symlink_at(name, self._fd)
            ):
                _raise_symlink_rejected(name, exc)
            raise _wrap_open_errno(exc, name) from exc
        return SafeDir(fd)

    # ------------------------------------------------------------------
    # Inspect / list / stat
    # ------------------------------------------------------------------

    def scandir(self) -> Iterator[os.DirEntry[str]]:
        """Iterate over entries in this directory, yielding ``os.DirEntry``s.

        The underlying ``os.scandir(self.fd)`` reads the directory by
        file descriptor — there is no path-string lookup that could be
        intercepted between this call and a follow-up operation on the
        same name.
        """
        return iter(os.scandir(self._fd))

    def lstat(self, name: str) -> os.stat_result:
        """Stat *name* without following symlinks (``lstat`` semantics).

        Useful for inode-pinning workflows (PR4 / PR5): capture
        ``(st_dev, st_ino, st_size)`` here, then re-call before any
        destructive operation and refuse on mismatch.
        """
        _validate_name(name)
        return os.stat(name, dir_fd=self._fd, follow_symlinks=False)

    # ------------------------------------------------------------------
    # Mutate: create / remove / rename
    # ------------------------------------------------------------------

    def mkdir(self, name: str, mode: int = 0o755) -> None:
        """Create a subdirectory *name* relative to this dir's fd.

        The created directory is *not* opened or returned; call
        ``open_subdir(name)`` afterwards if you need to operate on it.
        Separating create from open lets the caller decide whether to
        re-stat or use ``O_DIRECTORY | O_NOFOLLOW`` semantics on the
        follow-up open.
        """
        _validate_name(name)
        os.mkdir(name, mode=mode, dir_fd=self._fd)

    def unlink(self, name: str) -> None:
        """Remove a file (not a directory) *name* relative to this dir's fd.

        Uses ``os.unlink`` with ``dir_fd=`` so the unlink target is
        resolved through the held directory — not via a path string
        that could be intercepted.
        """
        _validate_name(name)
        os.unlink(name, dir_fd=self._fd)

    def rename_into(self, name: str, other: SafeDir, other_name: str) -> None:
        """Rename ``self/name`` to ``other/other_name`` atomically.

        Uses ``os.rename(name, other_name, src_dir_fd=self.fd,
        dst_dir_fd=other.fd)`` — atomic on POSIX within the same
        filesystem. Both component names are validated.

        Caller is responsible for ensuring ``other`` is on the same
        filesystem; cross-filesystem renames will raise ``OSError``
        (``EXDEV``) and the caller must fall back to copy + unlink (see
        ``undo/durable_move.py`` for the existing pattern).
        """
        _validate_name(name)
        _validate_name(other_name)
        os.rename(name, other_name, src_dir_fd=self._fd, dst_dir_fd=other._fd)
