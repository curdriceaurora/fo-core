"""Tests for the T3-narrow rail (``scripts/check_called_attribute_assertion.py``).

Bans the ``assert <mock>.called`` attribute lookup form. The canonical
mock-library equivalent ``<mock>.assert_called()`` is one extra
character, more discoverable in IDEs (it's a documented method, not a
flag attribute), and consistent with the rest of the test suite.

Detection forms:

  - ``assert <chain>.called``
  - ``assert <chain>.called is True``
  - ``assert <chain>.called == True``

T10 predicate negative-case backfill: every detection form has a
positive test (rail flags) AND a negative test (rail does NOT flag a
similar shape that is genuinely correct).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_called_attribute_assertion.py"

# Import the detector directly so we can unit-test helpers without spawning
# a subprocess for every case.
sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_called_attribute_assertion import (  # noqa: E402
    _PATTERN,
    _has_opt_out,
    _is_comment_line,
    find_violations,
)

# ---------------------------------------------------------------------------
# Unit tests: pattern detection
# ---------------------------------------------------------------------------


class TestPatternDetection:
    """The regex must match the three forbidden forms."""

    @pytest.mark.parametrize(
        "line",
        [
            "assert mock.called",
            "    assert mock.method.called",
            "        assert obj.attr.deep.chain.called",
            "assert mock.called is True",
            "assert mock.called == True",
            "assert mock.called  # trailing comment",
            "    assert mock.method.called is True",
            # Codex r218 — parenthesised forms must also match
            "assert (mock.method.called)",
            "    assert (mock.method.called)",
            "assert (mock.method.called) is True",
            "assert (mock.method.called) == True",
            "assert ( mock.method.called )",  # whitespace inside parens
        ],
    )
    def test_matches_forbidden(self, line: str) -> None:
        assert _PATTERN.match(line) is not None

    @pytest.mark.parametrize(
        "line",
        [
            # T10 negative cases — must NOT match
            "mock.assert_called()",  # canonical fix — the method form
            "mock.method.assert_called_once()",  # other Mock methods
            "assert mock.called == 3",  # count comparison (T3 has separate noqa for this)
            "assert mock.called is False",  # `is False` is rare but legitimate
            "assert obj.is_called",  # not the .called attribute
            "assert mock.call_count >= 1",  # not the bare-called form
            "called = mock.called",  # assignment, not assert
            "if mock.called:",  # conditional, not assert
            "assert called",  # local variable named 'called' — no chain
        ],
    )
    def test_does_not_match_legitimate(self, line: str) -> None:
        assert _PATTERN.match(line) is None


class TestOptOutMarker:
    """The opt-out marker must be detected only in its canonical form."""

    @pytest.mark.parametrize(
        "line",
        [
            "assert mock.called  # noqa: T3",
            "assert mock.called  # noqa: T3 reason: testing the mock library itself",
            "assert mock.called  #noqa: T3",  # tighter spacing
        ],
    )
    def test_recognises_opt_out(self, line: str) -> None:
        assert _has_opt_out(line)

    @pytest.mark.parametrize(
        "line",
        [
            "assert mock.called",  # no marker
            "assert mock.called  # noqa: T1",  # different rule
            "assert mock.called  # T3",  # missing noqa: prefix
        ],
    )
    def test_rejects_non_canonical(self, line: str) -> None:
        assert not _has_opt_out(line)


class TestCommentLines:
    """Comments must not trip the rail even if they contain the forbidden form."""

    @pytest.mark.parametrize(
        "line",
        [
            "# assert mock.called",
            "    # assert mock.called  (historical example)",
        ],
    )
    def test_comment_lines_are_skipped(self, line: str) -> None:
        assert _is_comment_line(line)


# ---------------------------------------------------------------------------
# Unit tests: find_violations on synthetic files
# ---------------------------------------------------------------------------


class TestFindViolationsSynthetic:
    """End-to-end checks against tmp_path test files."""

    def _write(self, tmp_path: Path, content: str) -> Path:
        target = tmp_path / "test_synth.py"
        target.write_text(content)
        return target

    def test_unmarked_assertion_is_flagged(self, tmp_path: Path) -> None:
        target = self._write(tmp_path, "assert mock.method.called\n")
        violations = find_violations(target)
        assert len(violations) == 1

    def test_marked_assertion_is_not_flagged(self, tmp_path: Path) -> None:
        target = self._write(
            tmp_path, "assert mock.method.called  # noqa: T3 reason: legacy test\n"
        )
        assert find_violations(target) == []

    def test_method_form_is_not_flagged(self, tmp_path: Path) -> None:
        # Canonical fix — the method call form. Not flagged.
        target = self._write(tmp_path, "mock.method.assert_called()\n")
        assert find_violations(target) == []

    def test_comment_with_forbidden_text_not_flagged(self, tmp_path: Path) -> None:
        target = self._write(tmp_path, "# example: assert mock.called\n")
        assert find_violations(target) == []

    def test_count_comparison_not_flagged(self, tmp_path: Path) -> None:
        # T10 negative case: ``call_count`` checks are out of scope for this
        # narrow rail (full T3 covers them under code review, not here).
        target = self._write(tmp_path, "assert mock.call_count >= 1\n")
        assert find_violations(target) == []

    def test_inside_triple_quoted_docstring_not_flagged(self, tmp_path: Path) -> None:
        # Fixtures embedded in docstrings (common in tests/ci/test_*.py
        # rails that demonstrate the forbidden pattern in dedent blocks)
        # must not false-flag.
        target = self._write(
            tmp_path,
            '"""Example fixture:\n\n    assert mock.method.called\n"""\n',
        )
        assert find_violations(target) == []


# ---------------------------------------------------------------------------
# Full-suite assertion: zero violations on the live tree
# ---------------------------------------------------------------------------


class TestFullSuite:
    """The rail must pass against the live ``tests/`` tree."""

    def test_no_violations_on_current_tree(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"T3 narrow rail flagged unmarked violation(s):\n{result.stderr}"
        )
