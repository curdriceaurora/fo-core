"""Path-validation and safe-walk helpers for CLI commands.

Epic A.foundation (hardening roadmap #154). Provides two primitives:

- `validate_within_roots(path, allowed_roots)` — assert `path` resolves inside
  one of the supplied roots and return its canonical absolute form. Raises
  `PathTraversalError` otherwise. Every CLI command that accepts a path
  argument routes through this helper (see Appendix A.4 of the roadmap spec
  for the full command surface).
- `safe_walk(root, *, follow_symlinks=False, include_hidden=False)` — yield
  files under `root`, filtering symlinks and hidden entries by default.
  Replaces raw `rglob("*")` calls in walker subsystems (analytics, pattern
  analysis, misplacement detection, dedup detection, copilot, JD/PARA scans)
  that risked indexing `/etc/passwd` or credential-bearing dotfiles.

Design invariants:

- Both helpers return/compare **resolved** paths. All paths are normalized
  via `Path.resolve()` before any comparison or traversal-check; callers get
  the canonical form back and don't need to re-resolve.
- `PathTraversalError` is a `ValueError` subclass so existing
  `except ValueError` handlers keep working.
- `safe_walk`'s hidden-file filter applies to the path **relative to `root`**
  — if the caller explicitly walks a hidden directory (e.g. scanning
  `.git/` intentionally), the root component doesn't veto every descendant.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path


class PathTraversalError(ValueError):
    """Raised when a path escapes the declared set of allowed roots.

    Subclasses `ValueError` so callers that want to catch this specifically
    can do so, while generic `except ValueError` paths continue to work.
    """


def validate_within_roots(path: Path, allowed_roots: Iterable[Path]) -> Path:
    """Resolve `path` and assert it lives inside one of `allowed_roots`.

    Returns the resolved absolute path. Raises `PathTraversalError` if:

    - `allowed_roots` is empty (no roots = nothing is allowed).
    - The resolved path is outside every resolved root.

    Each root is resolved before comparison, so symlinked roots are handled
    correctly. A resolved path exactly equal to a root is allowed (e.g.
    `fo analyze DIR` where the directory itself is the target).

    Args:
        path: The path to validate. May be relative, contain `..`, or be a
            symlink — all normalized by `Path.resolve()`.
        allowed_roots: The set of directories the path must be inside.
            Typically the CLI command's input and output directories plus
            any configured system locations (trash, cache) that the command
            legitimately touches.

    Returns:
        The resolved absolute form of `path`.

    Raises:
        PathTraversalError: If `allowed_roots` is empty, or if `path`
            resolves outside every root.
    """
    roots = [r.resolve() for r in allowed_roots]
    if not roots:
        raise PathTraversalError(f"No allowed roots declared; cannot validate {path!r}")
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    raise PathTraversalError(
        f"Path {path!r} (resolved to {resolved!r}) is outside allowed roots: "
        f"{[str(r) for r in roots]}"
    )


def safe_walk(
    root: Path,
    *,
    pattern: str = "*",
    recursive: bool = True,
    only_files: bool = True,
    follow_symlinks: bool = False,
    include_hidden: bool = False,
) -> Iterator[Path]:
    """Walk `root` with security filters.

    Drop-in replacement for raw `rglob("*")` / `glob("*")` in every
    user-supplied-root walker.

    Default filters (security-safe):

    - Symlinks (both file and directory symlinks) are skipped — their
      targets may live outside `root` (e.g. a malicious symlink from
      `indexed_dir/escape -> /etc/passwd`).
    - Hidden entries — any path with a component that starts with `.`,
      relative to `root` — are skipped. Catches `.git/`, `.env`,
      `.ssh/authorized_keys`, and similar credential-bearing paths.

    The hidden-file check is relative to `root`: if `root` itself is a
    hidden directory (`.config/fo/…`), descendants with non-hidden parts
    are still yielded. Only components inside the walked subtree are
    filtered.

    Args:
        root: Directory to walk. If it doesn't exist, yields nothing.
        pattern: Glob pattern to match. Default `"*"` (every entry).
        recursive: If True, walk recursively (`rglob`); if False, walk
            only the top level (`glob`). Default True.
        only_files: If True (default), yield files only — directories are
            filtered out. Pass False to yield directories and other
            non-file entries too (used by e.g. empty-directory cleanup).
        follow_symlinks: If True, include symlinked files and descend into
            symlinked directories. Default False (secure).
        include_hidden: If True, include dot-prefixed files and descendants
            of dot-prefixed directories. Default False (secure).

    Yields:
        `Path` objects for each entry under `root` that matches `pattern`
        and passes the filters.
    """
    if not root.exists():
        return
    resolved_root = root.resolve()
    glob_iter = root.rglob(pattern) if recursive else root.glob(pattern)
    for entry in glob_iter:
        # Per-entry OSError (PermissionError, stale NFS handle, etc.) skips
        # that entry instead of aborting the whole walk — walkers like the
        # doctor scan need to continue past inaccessible siblings.
        try:
            if not follow_symlinks and entry.is_symlink():
                continue
            # Hidden filter is relative to root so an explicit scan of a
            # hidden directory doesn't get vetoed by the root component.
            if not include_hidden:
                try:
                    rel = entry.resolve().relative_to(resolved_root)
                except ValueError:
                    # rglob yielded something outside root (can happen if a
                    # parent symlink was followed); skip it defensively.
                    continue
                if any(part.startswith(".") for part in rel.parts):
                    continue
            if only_files and not entry.is_file():
                continue
        except OSError:
            continue
        yield entry
