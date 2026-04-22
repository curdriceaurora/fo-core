"""Core file organization functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.types import OrganizationResult

if TYPE_CHECKING:
    from core.organizer import FileOrganizer


def __getattr__(name: str) -> Any:
    # Lazy re-export of FileOrganizer so importing `core.path_guard` from
    # sibling packages (e.g. services/*) doesn't transitively pull in
    # organizer → dispatcher → services, which would deadlock during
    # services/__init__.py initialization.
    if name == "FileOrganizer":
        from core.organizer import FileOrganizer

        return FileOrganizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FileOrganizer",
    "OrganizationResult",
]
