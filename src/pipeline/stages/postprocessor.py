"""Postprocessor stage - destination path computation.

Combines the category and filename from analysis with the output
directory to produce the final destination path, handling duplicate
filenames with numeric suffixes.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from interfaces.pipeline import StageContext

if TYPE_CHECKING:
    from utils.safedir import SafeDir

logger = logging.getLogger(__name__)


class PostprocessorStage:
    """Compute the destination path for the file.

    Reads ``context.category`` and ``context.filename`` (set by the
    analyzer or preprocessor) and writes ``context.destination``.
    Appends numeric suffixes to avoid overwriting existing files.

    On POSIX, opens the output root as a ``SafeDir`` and creates/verifies
    category subdirectories via ``open_subdir(O_NOFOLLOW)`` so that a
    symlink swap of a category dir is rejected with ``SymlinkRejected``
    before the file is written (PR6 / #270).  On Windows, falls back to
    ``Path.mkdir`` (SafeDir is POSIX-only).

    Per-category ``SafeDir`` instances are cached for the run and released
    via ``close()``.
    """

    def __init__(self, output_directory: Path) -> None:
        """Initialize with output directory for destination paths."""
        self._output_directory = output_directory
        self._root_sd: SafeDir | None = None
        # Cache of open category-level SafeDirs: category name → SafeDir
        self._cat_subdirs: dict[str, SafeDir] = {}

        if sys.platform != "win32":  # pragma: no cover - Windows fallback path
            try:
                from utils.safedir import SafeDir

                output_directory.mkdir(parents=True, exist_ok=True)
                self._root_sd = SafeDir.open_root(output_directory)
            except (OSError, NotImplementedError) as exc:
                logger.warning(
                    "PostprocessorStage: cannot open SafeDir for %s: %s — "
                    "falling back to path-based mkdir",
                    output_directory,
                    exc,
                    exc_info=True,
                )

    @property
    def name(self) -> str:
        """Return stage name."""
        return "postprocessor"

    def close(self) -> None:
        """Release all cached SafeDir file descriptors."""
        import contextlib

        for sd in self._cat_subdirs.values():
            with contextlib.suppress(Exception):
                sd.__exit__(None, None, None)
        self._cat_subdirs.clear()
        root_sd = self._root_sd
        if root_sd is not None:
            with contextlib.suppress(Exception):
                root_sd.__exit__(None, None, None)
            self._root_sd = None

    def _get_category_safedir(self, category: str) -> SafeDir:
        """Return a cached ``SafeDir`` for *category*, creating it if needed.

        Returns ``None`` on any error (caller falls back to path-based mkdir).
        Raises ``SymlinkRejected`` if the category dir is a symlink.
        """
        if category in self._cat_subdirs:
            return self._cat_subdirs[category]

        from utils.safedir import SafeDir, SymlinkRejected  # noqa: F401

        sd = self._root_sd
        assert sd is not None
        try:
            sd.mkdir(category)
        except FileExistsError:
            pass  # already exists — open_subdir below will verify it's real

        # open_subdir uses O_NOFOLLOW | O_DIRECTORY — raises SymlinkRejected
        # if the entry is a symlink (lets the caller propagate the error).
        sub = sd.open_subdir(category)
        self._cat_subdirs[category] = sub
        return sub

    def process(self, context: StageContext) -> StageContext:
        """Build destination path, deduplicating if needed."""
        if context.failed:
            return context

        category = context.category or "uncategorized"
        filename = context.filename or context.file_path.stem
        suffix = context.file_path.suffix

        destination = self._output_directory / category / f"{filename}{suffix}"

        if self._root_sd is not None:
            try:
                from utils.safedir import SymlinkRejected

                cat_sd = self._get_category_safedir(category)
                context.dest_safedir = cat_sd
            except SymlinkRejected:
                logger.error(
                    "security_event destination_symlink_swap category=%s path=%s: "
                    "category dir is a symlink — refusing organize",
                    category,
                    destination.parent,
                    exc_info=True,
                )
                context.error = f"Destination symlink rejected: {destination.parent}"
                return context
            except Exception as exc:
                logger.warning(
                    "SafeDir category open failed for %s: %s — falling back",
                    category,
                    exc,
                    exc_info=True,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
        else:
            # Windows / SafeDir unavailable: plain mkdir
            destination.parent.mkdir(parents=True, exist_ok=True)

        # Deduplicate
        final = destination
        counter = 1
        while final.exists():
            final = destination.parent / f"{filename}_{counter}{suffix}"
            counter += 1

        context.destination = final
        logger.debug("Postprocessed %s -> %s", context.file_path.name, final)
        return context
