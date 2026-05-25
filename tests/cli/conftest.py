"""Shared fixtures and helpers for tests/cli."""

from __future__ import annotations

import pytest

try:
    import rank_bm25  # noqa: F401
    import sklearn  # noqa: F401

    _HAS_SEMANTIC_DEPS = True
    _SEMANTIC_DEPS_ERROR = ""
except Exception as exc:
    _HAS_SEMANTIC_DEPS = False
    _SEMANTIC_DEPS_ERROR = str(exc)


def skip_without_semantic_deps() -> None:
    """Skip semantic-search tests unless optional search dependencies import cleanly.

    Catches broad Exception (not just ImportError) to handle ABI mismatches
    that surface as ValueError or RuntimeError at import time.
    """
    if not _HAS_SEMANTIC_DEPS:
        pytest.skip(f"semantic search dependencies unavailable: {_SEMANTIC_DEPS_ERROR}")
