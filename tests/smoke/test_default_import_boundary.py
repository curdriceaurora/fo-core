"""Verify that default-install imports do not require numpy."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.ci, pytest.mark.smoke]


def test_default_imports_without_numpy() -> None:
    """No module reachable from the default install should import numpy."""
    code = """
import sys
sys.modules["numpy"] = None  # block numpy

import core.organizer        # must not raise
import cli.dedupe_hash       # must not raise (hash-based dedup path)
from services.deduplication.detector import DuplicateDetector  # must not raise
import services.search        # must not raise (HybridRetriever guard required)
"""
    env = os.environ.copy()
    # Prepend src/ so imports work from a clean checkout before `pip install -e .`
    # On CI the editable install covers this, but local runs may not have it.
    # Use os.pathsep (';' on Windows, ':' on POSIX) for cross-platform portability.
    src_path = str(Path(__file__).resolve().parents[2] / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_path + (os.pathsep + existing if existing else "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"Default import triggered numpy dependency:\n{result.stderr}"
    )
