"""API package exports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["app", "create_app", "get_app"]

# Module-level cache for lazy initialization
_app_cache: Optional["FastAPI"] = None
_create_app_cache = None
_get_app_cache = None


def create_app(settings=None):
    """Lazily import and call create_app from main module.

    This wrapper breaks the circular dependency and defers app creation.
    """
    global _create_app_cache
    if _create_app_cache is None:
        from file_organizer.api.main import create_app as _real_create_app

        _create_app_cache = _real_create_app
    return _create_app_cache(settings)


def get_app() -> "FastAPI":
    """Get or create the FastAPI application instance.

    This function implements lazy initialization to avoid import-time side effects
    (like creating .config directories) that would break isolated test environments.

    Returns:
        The initialized FastAPI application instance.
    """
    global _app_cache
    if _app_cache is None:
        _app_cache = create_app()
    return _app_cache


# Keep __getattr__ for backwards compatibility with attribute access
def __getattr__(name: str) -> object:
    """Fallback for attribute access patterns.

    Provided for backwards compatibility with code that uses attribute
    access instead of function calls.
    """
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
