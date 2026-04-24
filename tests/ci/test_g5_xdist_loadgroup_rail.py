"""Tests for the G5 rail (``scripts/check_xdist_loadgroup.py``).

G5 blocks pytest invocations that use ``-n auto`` without also using
``--dist=loadgroup``. Without loadgroup, ``@pytest.mark.xdist_group``
markers are silently non-enforcing, and singleton-sharing tests race
under xdist.

See ``.claude/rules/xdist-safe-patterns.md`` Pattern 3.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_xdist_loadgroup.py"

sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_xdist_loadgroup import (  # noqa: E402
    _LOADGROUP_RE,
    _N_AUTO_RE,
    _NOQA_G5_RE,
    _is_reference_not_command,
    find_violations,
)


class TestPatternRecognition:
    """Core regexes must match documented forms and reject near-misses."""

    @pytest.mark.parametrize(
        "line",
        [
            "pytest -n auto tests/",
            "pytest -n=auto tests/",
            'pytest -n "auto" tests/',
            "pytest -n 'auto' tests/",
        ],
    )
    def test_n_auto_variants_match(self, line: str) -> None:
        assert _N_AUTO_RE.search(line)

    @pytest.mark.parametrize(
        "line",
        [
            "pytest -n 4 tests/",  # T10 negative: numeric count, not auto
            "pytest --numprocesses=auto tests/",  # long form not matched
            "pytest tests/",
            "pytest -n autox tests/",  # T10 surface shape: "auto" is not prefix-matched
        ],
    )
    def test_n_auto_negative_cases(self, line: str) -> None:
        assert not _N_AUTO_RE.search(line)

    @pytest.mark.parametrize(
        "line",
        [
            "--dist=loadgroup",
            "--dist loadgroup",
            '--dist="loadgroup"',
        ],
    )
    def test_loadgroup_variants_match(self, line: str) -> None:
        assert _LOADGROUP_RE.search(line)

    def test_loadgroup_does_not_match_loadfile(self) -> None:
        # T10 surface shape: --dist=loadfile is a REAL alternative that
        # looks similar but provides a different scheduler — must NOT
        # be treated as loadgroup.
        assert not _LOADGROUP_RE.search("--dist=loadfile")

    def test_noqa_g5_matches(self) -> None:
        assert _NOQA_G5_RE.search("pytest -n auto  # noqa: G5 (no singletons here)")

    def test_unrelated_noqa_does_not_match_g5(self) -> None:
        # T10 negative: other rule codes must NOT exempt G5.
        assert not _NOQA_G5_RE.search("pytest -n auto  # noqa: G2")
        assert not _NOQA_G5_RE.search("pytest -n auto  # noqa: E501")


class TestReferenceExemption:
    """Prose references (comments, backtick-wrapped flag mentions) are
    exempt; actual commands are not."""

    def test_shell_comment_is_reference(self, tmp_path: Path) -> None:
        line = "# run pytest with -n auto for parallel execution"
        assert _is_reference_not_command(line, tmp_path / "x.sh")

    def test_yaml_comment_is_reference(self, tmp_path: Path) -> None:
        assert _is_reference_not_command("# -n auto is used below", tmp_path / "x.yml")

    def test_bare_command_is_not_reference(self, tmp_path: Path) -> None:
        # T10 surface shape: looks like a command (leading `pytest`) but
        # without a `#` it IS a command — not exempt.
        assert not _is_reference_not_command("pytest -n auto tests/", tmp_path / "x.sh")


class TestFindViolations:
    """End-to-end ``find_violations`` against synthetic files."""

    def test_flags_missing_loadgroup(self, tmp_path: Path) -> None:
        f = tmp_path / "run.sh"
        f.write_text("#!/usr/bin/env bash\npytest tests/ -n auto --timeout=30\n")
        violations = find_violations(f)
        assert len(violations) == 1
        assert violations[0][0] == 2

    def test_accepts_same_line_loadgroup(self, tmp_path: Path) -> None:
        f = tmp_path / "run.sh"
        f.write_text("#!/usr/bin/env bash\npytest tests/ -n auto --dist=loadgroup --timeout=30\n")
        assert find_violations(f) == []

    def test_accepts_loadgroup_in_continuation(self, tmp_path: Path) -> None:
        f = tmp_path / "run.sh"
        f.write_text(
            "#!/usr/bin/env bash\n"
            "pytest tests/ \\\n"
            "  -n auto \\\n"
            "  --dist=loadgroup \\\n"
            "  --timeout=30\n"
        )
        assert find_violations(f) == []

    def test_accepts_noqa_marker(self, tmp_path: Path) -> None:
        f = tmp_path / "run.sh"
        f.write_text(
            "#!/usr/bin/env bash\n"
            "pytest tests/ -n auto  # noqa: G5 (no xdist_group markers in this subset)\n"
        )
        assert find_violations(f) == []

    def test_exempts_comment_references(self, tmp_path: Path) -> None:
        f = tmp_path / "ci.yml"
        f.write_text(
            "# We use -n auto for parallel test execution\n"
            "jobs:\n"
            "  run:\n"
            "    run: pytest tests/ -n auto --dist=loadgroup\n"
        )
        assert find_violations(f) == []

    def test_flags_second_occurrence_without_loadgroup(self, tmp_path: Path) -> None:
        f = tmp_path / "run.sh"
        f.write_text(
            "#!/usr/bin/env bash\n"
            "pytest tests/unit -n auto --dist=loadgroup\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "pytest tests/other -n auto\n"
        )
        violations = find_violations(f)
        # The 9th-line command is far enough from the first to exceed the
        # 5-line window.
        assert len(violations) == 1
        assert violations[0][0] == 9


class TestFullRepoEnforcement:
    """The script must exit 0 when run against the project tree."""

    def test_repo_has_no_unguarded_n_auto(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=_FO_ROOT,
            check=False,
        )
        assert result.returncode == 0, (
            f"G5 check reported `-n auto` without `--dist=loadgroup`:\n\n{result.stderr}"
        )
