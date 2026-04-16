"""Guardrail tests asserting mypy gate configuration matches expectations.

These tests read ci.yml and .pre-commit-config.yaml as text and assert
structural properties. They fail when the gate drifts from the full-src
invocation agreed in issue #93 (tier-3 expansion).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Read at import time: intentional — these are required config files; missing files
# should cause immediate, loud failure rather than a confusing assertion error.
_CI_YML = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text()
_PRECOMMIT = (PROJECT_ROOT / ".pre-commit-config.yaml").read_text()


def _mypy_step_body() -> str:
    """Extract the body of the 'Run mypy on all source modules' step."""
    m = re.search(
        r"Run mypy on all source modules.*?(?=\n      - name|\n  \w|\Z)",
        _CI_YML,
        re.DOTALL,
    )
    assert m, (
        "Step 'Run mypy on all source modules' not found in ci.yml. "
        "If the step was renamed, update both the CI workflow and this test."
    )
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


@pytest.mark.ci
class TestFullSrcMypyGate:
    """Full-src gate: CI step and pre-commit hook must target src/ not per-package paths."""

    def test_ci_step_uses_full_src_path(self) -> None:
        """CI step must invoke mypy with bare src/ not a per-package subpath."""
        body = _mypy_step_body()
        # Require the standalone src/ token: must not be followed by file_organizer/
        assert re.search(r"\bsrc/\s", body) or body.rstrip().endswith("src/"), (
            "mypy step does not invoke bare 'src/'; found: " + body
        )
        assert "src/file_organizer/" not in body, (
            "mypy step uses a per-package subpath instead of bare src/"
        )

    def test_ci_step_not_per_package(self) -> None:
        """CI step must not list individual packages (regression guard)."""
        body = _mypy_step_body()
        assert "src/file_organizer/core/" not in body
        assert "src/file_organizer/cli/" not in body
        assert "src/file_organizer/watcher/" not in body

    def test_precommit_hook_covers_all_source(self) -> None:
        """Pre-commit hook files: regex must cover all src/file_organizer/ files."""
        regex = _mypy_hook_files_value()
        assert regex.startswith("^src/file_organizer/")

    def test_precommit_hook_not_per_package(self) -> None:
        """Pre-commit hook must not use per-package alternation (regression guard)."""
        regex = _mypy_hook_files_value()
        # The full-src regex (^src/file_organizer/.*\.py$) never needs alternation.
        # Any '|' in the value indicates a per-package list, which is a regression.
        assert "|" not in regex, (
            f"pre-commit hook files regex contains alternation (per-package list): {regex!r}"
        )

    def test_precommit_hook_name_reflects_full_src(self) -> None:
        """Pre-commit hook name must reflect full-src coverage."""
        hook_m = re.search(
            r"id: mypy-changed.*?(?=\n      - id:|\n  - repo:|\Z)",
            _PRECOMMIT,
            re.DOTALL,
        )
        assert hook_m, "mypy-changed hook not found"
        assert "all source" in hook_m.group()
