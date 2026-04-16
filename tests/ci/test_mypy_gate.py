"""Guardrail tests asserting mypy gate configuration matches expectations.

These tests read ci.yml and .pre-commit-config.yaml as text and assert
structural properties. They fail when the gate drifts from what was
agreed in issue #93.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CI_YML = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text()
_PRECOMMIT = (PROJECT_ROOT / ".pre-commit-config.yaml").read_text()


def _mypy_step_body() -> str:
    """Extract the body of the 'Run mypy on gated modules' step."""
    m = re.search(
        r"Run mypy on gated modules.*?(?=\n      - name|\n  \w|\Z)",
        _CI_YML,
        re.DOTALL,
    )
    assert m, "Step 'Run mypy on gated modules' not found in ci.yml"
    return m.group()


def _mypy_hook_files_value() -> str:
    """Extract the files: value from the mypy-changed pre-commit hook."""
    hook_m = re.search(
        r"id: mypy-changed.*?(?=\n      - id:|\n  - repo:|\Z)",
        _PRECOMMIT,
        re.DOTALL,
    )
    assert hook_m, "mypy-changed hook not found in .pre-commit-config.yaml"
    files_m = re.search(r"files:\s*(\S+)", hook_m.group())
    assert files_m, "files: line not found in mypy-changed hook"
    return files_m.group(1)


class TestTier3MypyGate:
    """PR 1: core, cli, watcher must be in the gate."""

    def test_core_in_ci_mypy_step(self) -> None:
        assert "src/file_organizer/core/" in _mypy_step_body()

    def test_cli_in_ci_mypy_step(self) -> None:
        assert "src/file_organizer/cli/" in _mypy_step_body()

    def test_watcher_in_ci_mypy_step(self) -> None:
        assert "src/file_organizer/watcher/" in _mypy_step_body()

    def test_core_in_precommit_hook_regex(self) -> None:
        assert "core" in _mypy_hook_files_value()

    def test_cli_in_precommit_hook_regex(self) -> None:
        assert "cli" in _mypy_hook_files_value()

    def test_watcher_in_precommit_hook_regex(self) -> None:
        assert "watcher" in _mypy_hook_files_value()
