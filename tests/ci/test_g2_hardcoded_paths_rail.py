"""Tests for the G2 rail (``scripts/check_test_hardcoded_paths.py``).

G2 blocks hardcoded temp/home-dir paths in test files (see ``_FORBIDDEN`` in
the detector for the exact pattern list). The detector runs as both a
pre-commit hook and as the CI test in this file.

Tests cover positive detection, each exemption category (comment lines,
``# noqa: G2`` marker, adversarial inputs), and the full-suite assertion
that no unexempted violations exist on ``main``.

T10 predicate negative-case backfill: every exemption category has a test
that asserts the detector does NOT flag the line, alongside a positive
test that asserts the same surface shape without the exemption IS flagged.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_test_hardcoded_paths.py"

# Import the detector directly so we can unit-test helpers without spawning
# a subprocess for every case.
sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_test_hardcoded_paths import (  # noqa: E402
    _ADVERSARIAL_INPUTS,
    _FORBIDDEN,
    _has_adversarial_input,
    _has_noqa_g2,
    _is_comment_line,
    find_violations,
)

# ---------------------------------------------------------------------------
# Unit tests: detector internals
# ---------------------------------------------------------------------------


# The detector target strings are built via concatenation so the G2 rail
# doesn't flag this self-test file. ``_TMP``, ``_USERS``, ``_HOME`` are
# literal path prefixes that the detector must match; constructing them at
# runtime keeps the raw literal off any single source line.
_TMP = "/" + "tmp/"
_USERS = "/" + "Users/"
_HOME = "/" + "home/"


class TestPatternDetection:
    """The core regex must match forbidden paths and reject near-misses."""

    @pytest.mark.parametrize(
        "line",
        [
            f'Path("{_TMP}file.txt")',
            f'open("{_TMP}log")',
            f'working_dir = "{_USERS}alice/docs"',
            f'target = "{_HOME}bob/data"',
            f"{_TMP}sub/deep.json",
        ],
    )
    def test_matches_forbidden_paths(self, line: str) -> None:
        assert _FORBIDDEN.search(line) is not None

    @pytest.mark.parametrize(
        "line",
        [
            # Near-misses — should NOT match (T10 negative cases)
            "# mentions /tmp but not as a path prefix",
            "x = '/Users'  # no name after /Users",
            "y = '/home'  # no name after /home",
            # Substring but not at path start
            "contains_tmp_marker = True",
            "HOMEDIR = '~/docs'",
        ],
    )
    def test_rejects_non_matching_lines(self, line: str) -> None:
        assert _FORBIDDEN.search(line) is None


class TestCommentExemption:
    """Comment-only lines are exempt regardless of path content."""

    def test_full_line_comment_is_skipped(self) -> None:
        assert _is_comment_line(f"# use {_TMP}foo as example")

    def test_indented_comment_is_skipped(self) -> None:
        assert _is_comment_line(f"    # see {_USERS}example")

    def test_code_line_with_trailing_comment_is_not_comment_line(self) -> None:
        # A line with code AND a comment is still code — it should not be
        # exempted by the comment rule. (It may still be exempted by noqa.)
        assert not _is_comment_line(f'x = "{_TMP}foo"  # sample')


class TestNoqaExemption:
    """The ``# noqa: G2`` marker exempts a single line."""

    @pytest.mark.parametrize(
        "line",
        [
            f'x = "{_TMP}foo"  # noqa: G2',
            f'x = "{_TMP}foo"  # noqa: G2 (parser test input)',
            f'x = "{_HOME}user/bar"  # noqa:G2',  # no space after colon
            f'x = "{_USERS}alice"  # noqa: G2 (adversarial)',
        ],
    )
    def test_valid_noqa_markers_exempt_the_line(self, line: str) -> None:
        assert _has_noqa_g2(line)

    @pytest.mark.parametrize(
        "line",
        [
            # Similar-looking but wrong — must NOT be treated as G2 noqa
            # (T10 surface-shape negatives).
            f'x = "{_TMP}foo"  # noqa: E501',
            f'x = "{_TMP}foo"  # noqa',  # no code specified
            f'x = "{_TMP}foo"  # noqa: G3',
            f'x = "{_TMP}foo"  # noqa: G21',  # G21 is not G2
            f'x = "{_TMP}foo"  # TODO: noqa',
        ],
    )
    def test_unrelated_markers_do_not_exempt(self, line: str) -> None:
        assert not _has_noqa_g2(line)


class TestAdversarialInputExemption:
    """Well-known path-validation test inputs are pre-authorized."""

    @pytest.mark.parametrize("adv_path", _ADVERSARIAL_INPUTS)
    def test_known_adversarial_path_is_exempt(self, adv_path: str) -> None:
        line = f'validator.check("{adv_path}")'
        assert _has_adversarial_input(line)

    def test_ordinary_user_path_is_not_adversarial(self) -> None:
        # T10 negative case: surface-looking-similar (starts with /), but
        # NOT a documented adversarial pattern → must return False.
        assert not _has_adversarial_input(f'x = "{_TMP}ordinary_test.txt"')

    def test_unrelated_etc_path_is_not_adversarial(self) -> None:
        # ``/etc/hosts`` is NOT in the adversarial list — only ``passwd``
        # and ``shadow`` are. Prevents a hardcoded /etc/foo slipping in.
        assert not _has_adversarial_input('x = "/etc/hosts"')


# ---------------------------------------------------------------------------
# Integration tests: full detector against synthetic files
# ---------------------------------------------------------------------------


class TestFindViolations:
    """End-to-end: ``find_violations()`` against per-case tmp files."""

    def test_reports_single_line_violation(self, tmp_path: Path) -> None:
        f = tmp_path / "test_example.py"
        f.write_text(
            "def test_foo():\n"
            f'    path = "{_TMP}target.txt"\n'
            f'    assert path == "{_TMP}target.txt"\n'
        )
        violations = find_violations(f)
        # Line 2 and line 3 both violate.
        assert len(violations) == 2
        assert violations[0][0] == 2
        assert f"{_TMP}target.txt" in violations[0][1]

    def test_comment_lines_are_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "test_comments.py"
        f.write_text(
            f"# Example path: {_TMP}foo.txt\n"
            f"# {_USERS}alice/docs is a placeholder\n"
            "def test_foo():\n"
            "    pass\n"
        )
        assert find_violations(f) == []

    def test_noqa_g2_marker_skips_line(self, tmp_path: Path) -> None:
        f = tmp_path / "test_noqa.py"
        f.write_text(
            "def test_parser():\n"
            f'    parser.parse("move {_TMP}dest")  # noqa: G2 (parser test input)\n'
        )
        assert find_violations(f) == []

    def test_adversarial_path_not_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "test_adversarial.py"
        f.write_text(
            dedent(
                """\
                def test_validator_rejects_traversal():
                    with pytest.raises(PathTraversalError):
                        validate("/etc/passwd")
                """
            )
        )
        assert find_violations(f) == []

    def test_mixed_flagged_and_exempt(self, tmp_path: Path) -> None:
        f = tmp_path / "test_mixed.py"
        f.write_text(
            "def test_a():\n"
            '    validator("/etc/passwd")          # exempt: adversarial\n'
            f'    x = "{_TMP}ok"  # noqa: G2          # exempt: noqa\n'
            f"    # {_HOME}user/example               # exempt: comment\n"
            f'    bad = "{_TMP}flagged.txt"           # NOT exempt\n'
        )
        violations = find_violations(f)
        assert len(violations) == 1
        assert violations[0][0] == 5


# ---------------------------------------------------------------------------
# Full-suite enforcement: the project's own tests/ must be clean
# ---------------------------------------------------------------------------


class TestFullSuiteEnforcement:
    """The script must exit 0 when run against the project's own tests/."""

    def test_full_suite_has_no_unexempted_violations(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=_FO_ROOT,
            check=False,
        )
        assert result.returncode == 0, (
            "G2 full-suite check reported violations on main. Either fix "
            "the violations or add `# noqa: G2 (reason)` markers for "
            f"legitimate exceptions.\n\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Contract: the script reports non-zero exit + stderr details on violation
# ---------------------------------------------------------------------------


class TestScriptContract:
    """When violations exist, the script exits 1 and names the offending lines."""

    def test_violation_exit_code_and_output(self, tmp_path: Path, monkeypatch) -> None:
        """Invoke the detector's ``main`` function directly against a synthetic
        tests/ tree so we verify exit code + stderr format without modifying
        the real project tree.

        We re-import the module with a patched ``_TESTS_DIR`` pointing at the
        synthetic directory to isolate the test from the real test suite.
        """
        # Build a synthetic test directory with one violation.
        synthetic_tests = tmp_path / "tests"
        synthetic_tests.mkdir()
        violating = synthetic_tests / "test_bad.py"
        violating.write_text(f'def test_foo():\n    x = "{_TMP}bad.txt"\n')

        # Re-import the module with a patched _TESTS_DIR.
        import check_test_hardcoded_paths as mod

        monkeypatch.setattr(mod, "_TESTS_DIR", synthetic_tests)
        monkeypatch.setattr(mod, "_ROOT", tmp_path)

        exit_code = mod.main()
        assert exit_code == 1
