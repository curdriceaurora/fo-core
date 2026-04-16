"""Guardrails for the hard-cut source layout and public identity."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DISALLOWED_TOKENS = [
    "file" + "_organizer",
    "src/" + "file" + "_organizer",
    "file" + "-" + "organizer",
    "local" + "-" + "file" + "-" + "organizer",
    "FILE" + "_" + "ORGANIZER",
]

_TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".iss",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


@pytest.mark.ci
def test_legacy_namespace_and_identity_tokens_are_absent() -> None:
    """Tracked text files must not reintroduce the pre-flattening names."""
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    violations: list[str] = []
    for rel_path in tracked:
        path = PROJECT_ROOT / rel_path
        if path.suffix not in _TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for token in _DISALLOWED_TOKENS:
            if token in content:
                violations.append(f"{rel_path}: contains {token!r}")

    assert not violations, "Legacy source layout or identity tokens remain:\n" + "\n".join(
        violations
    )
