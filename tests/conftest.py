"""
Shared test fixtures and configuration for the fo test suite.

Provides version-aware fixtures and skip markers for multi-version testing.
Requires Python 3.11+.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

collect_ignore_glob = [
    # Playwright browser tests require `playwright install chromium` and
    # --override-ini='addopts=' to suppress --cov interference.  Exclude from
    # default collection so `pytest tests/` does not attempt to import
    # playwright (which raises ImportError on machines without the package).
    # Run explicitly: pytest tests/playwright/ --browser chromium --override-ini='addopts='
    "playwright/**",
]

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
def is_py311_plus() -> bool:
    """True when running on Python 3.11 or later (always True for this project)."""
    return sys.version_info >= (3, 11)


# ---------------------------------------------------------------------------
# Skip markers
# ---------------------------------------------------------------------------

skip_below_py311 = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="Requires Python 3.11+",
)

skip_below_py312 = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="Requires Python 3.12+",
)


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def ensure_default_event_loop(request: pytest.FixtureRequest) -> None:
    """Ensure sync tests can instantiate asyncio-bound widgets across Python versions.

    Some Textual widgets allocate ``asyncio.Lock`` during ``__init__``. On Python 3.9,
    after async tests run, the default loop policy can be left with no current loop
    in the main thread, which raises ``RuntimeError`` for later sync widget tests.
    This fixture gives each sync test an explicit default loop; async tests use
    ``pytest-asyncio``/``anyio`` loop management and are skipped here.
    """
    if request.node.get_closest_marker("asyncio") or request.node.get_closest_marker("anyio"):
        yield
        return

    # On Windows, asyncio.new_event_loop() returns a ProactorEventLoop backed by
    # IOCP.  Its cleanup thread races with pytest's session teardown and generates
    # CTRL_C_EVENT → KeyboardInterrupt at _pytest/stash.py during finalization,
    # causing exit code 1 even when all tests pass.  SelectorEventLoop avoids
    # IOCP entirely and is safe for the only thing sync tests need: asyncio.Lock.
    if sys.platform == "win32":
        created_loop: asyncio.AbstractEventLoop = asyncio.SelectorEventLoop()
    else:
        created_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(created_loop)

    yield

    created_loop.close()
    asyncio.set_event_loop(None)


@pytest.fixture
def mock_text_model() -> MagicMock:
    """Create a mock TextModel that returns deterministic responses.

    Returns:
        A MagicMock mimicking the TextModel interface.
    """
    model = MagicMock()
    model.generate.return_value = "Mock AI response for testing."
    model.generate_streaming.return_value = iter(["Mock ", "response."])
    model.is_initialized = True
    model._initialized = True
    model.config = MagicMock()
    model.config.name = "mock-model:test"
    return model


@pytest.fixture
def mock_ollama() -> MagicMock:
    """Create a mock Ollama client.

    Returns:
        A MagicMock mimicking the ollama.Client interface.
    """
    client = MagicMock()
    client.list.return_value = {
        "models": [
            {"name": "qwen2.5:3b-instruct-q4_K_M", "size": "1.9 GB"},
            {"name": "qwen2.5vl:7b-q4_K_M", "size": "6.0 GB"},
        ]
    }
    client.show.return_value = {"name": "test-model"}
    client.generate.return_value = {"response": "Test output", "total_duration": 1_000_000_000}
    return client


@pytest.fixture
def sample_config_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for configuration files.

    Returns:
        Path to the temp config directory.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return config_dir


@pytest.fixture
def sample_files_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with sample files for testing.

    Returns:
        Path to the temp directory containing sample files.
    """
    d = tmp_path / "samples"
    d.mkdir()
    (d / "document.txt").write_text("Sample document content for testing.")
    (d / "notes.md").write_text("# Notes\n\n- Item 1\n- Item 2\n")
    (d / "data.csv").write_text("col1,col2\nval1,val2\n")
    return d
