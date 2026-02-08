"""
Shared test fixtures and configuration for the file_organizer test suite.

Provides version-aware fixtures and skip markers for multi-version testing.
"""

from __future__ import annotations

import sys

import pytest

# ---------------------------------------------------------------------------
# Version-aware fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def python_version() -> tuple[int, int]:
    """Return the current Python (major, minor) version tuple.

    Useful for tests that need to branch logic based on runtime version.
    """
    return sys.version_info[:2]


@pytest.fixture
def python_version_string() -> str:
    """Return a human-readable Python version string like '3.12.1'."""
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


@pytest.fixture
def is_py39() -> bool:
    """True when running on Python 3.9."""
    return sys.version_info[:2] == (3, 9)


@pytest.fixture
def is_py310_plus() -> bool:
    """True when running on Python 3.10 or later."""
    return sys.version_info >= (3, 10)


@pytest.fixture
def is_py311_plus() -> bool:
    """True when running on Python 3.11 or later."""
    return sys.version_info >= (3, 11)


# ---------------------------------------------------------------------------
# Skip markers
# ---------------------------------------------------------------------------

skip_below_py310 = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="Requires Python 3.10+",
)

skip_below_py311 = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="Requires Python 3.11+",
)

skip_below_py312 = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="Requires Python 3.12+",
)

skip_on_py39 = pytest.mark.skipif(
    sys.version_info[:2] == (3, 9),
    reason="Not applicable on Python 3.9",
)

requires_py39 = pytest.mark.skipif(
    sys.version_info[:2] != (3, 9),
    reason="Only runs on Python 3.9",
)
