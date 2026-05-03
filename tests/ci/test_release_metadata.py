"""Guard: version string and Development Status classifier must agree."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.mark.ci
def test_version_and_classifier_agree() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    version_match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert version_match, "Could not find version line in pyproject.toml"
    version = version_match.group(1)

    expected_classifier_substring = (
        "Development Status :: 4 - Beta"
        if "beta" in version
        else "Development Status :: 3 - Alpha"
        if "alpha" in version
        else "Development Status :: 5 - Production/Stable"
    )

    assert expected_classifier_substring in pyproject, (
        f"Version {version!r} implies classifier {expected_classifier_substring!r}, "
        f"but it was not found in pyproject.toml. Update the classifier."
    )


@pytest.mark.ci
def test_version_py_matches_pyproject() -> None:
    """src/version.py __version__ must match pyproject.toml version."""
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    version_py = (root / "src" / "version.py").read_text(encoding="utf-8")

    pyproject_match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    version_py_match = re.search(r'^__version__ = "([^"]+)"', version_py, re.MULTILINE)

    assert pyproject_match, "Could not find version in pyproject.toml"
    assert version_py_match, "Could not find __version__ in src/version.py"

    assert pyproject_match.group(1) == version_py_match.group(1), (
        f"Version mismatch: pyproject.toml={pyproject_match.group(1)!r} "
        f"vs src/version.py={version_py_match.group(1)!r}"
    )
