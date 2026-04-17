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

# Legacy namespace token — split to avoid triggering the identity guardrail
# (tests/ci/test_flattened_identity_guardrail.py) on this file itself.
_LEGACY_NS = "file" + "_organizer"


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
        # Require the standalone src/ token: must end with src/ or have src/ followed by space
        assert re.search(r"\bsrc/\s", body) or body.rstrip().endswith("src/"), (
            "mypy step does not invoke bare 'src/'; found: " + body
        )
        assert "src/" + _LEGACY_NS + "/" not in body, (
            "mypy step uses the old namespace subpath instead of bare src/"
        )

    def test_ci_step_not_per_package(self) -> None:
        """CI step must not list individual packages (regression guard)."""
        body = _mypy_step_body()
        # Ensure no old-namespace per-package paths are used
        for pkg in ("core", "cli", "watcher"):
            assert "src/" + _LEGACY_NS + "/" + pkg + "/" not in body

    def test_precommit_hook_covers_all_source(self) -> None:
        """Pre-commit hook files: regex must cover all src/ files (flat layout)."""
        regex = _mypy_hook_files_value()
        assert regex.startswith("^src/"), (
            f"pre-commit hook files regex does not cover src/: {regex!r}"
        )
        assert _LEGACY_NS not in regex, f"pre-commit hook still references old namespace: {regex!r}"

    def test_precommit_hook_not_per_package(self) -> None:
        """Pre-commit hook must not use per-package alternation (regression guard)."""
        regex = _mypy_hook_files_value()
        # The full-src regex (^src/.*\\.py$) never needs alternation.
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
