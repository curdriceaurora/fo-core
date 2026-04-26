"""Tests for the T3-narrow rail (``scripts/check_called_attribute_assertion.py``).

Bans the ``assert <mock>.called`` attribute lookup form. The canonical
mock-library equivalent ``<mock>.assert_called()`` is one extra
character, more discoverable in IDEs (it's a documented method, not a
flag attribute), and consistent with the rest of the test suite.

Detector is **AST-based**.  Earlier regex-on-raw-lines versions were
bypassed by:

- Multi-line parenthesised assertions (codex r218 / r219, issue #222):
  ``assert (\\n    mock.called\\n)``
- Assert-with-message form (codex r218):
  ``assert mock.called, "must be called"``
- String literals containing the marker text in a way that matched the
  regex but wasn't a real assertion.

The AST detector reads the structure of each ``ast.Assert`` node, so
all three classes of bypass are closed by construction.

T10 predicate negative-case backfill: every detection branch has a
positive test (the rail flags) AND a negative test (the rail does NOT
flag a similar surface shape that is genuinely correct).
"""

from __future__ import annotations

import ast
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
    _has_opt_out,
    _is_called_attribute_assertion,
    find_violations,
)


def _synth(tmp_path: Path, content: str) -> Path:
    """Write *content* to a synthetic Python file under *tmp_path*."""
    target = tmp_path / "test_synth.py"
    target.write_text(content)
    return target


def _parse_assert(source: str) -> ast.Assert:
    """Parse *source* and return its first ``ast.Assert`` node."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            return node
    raise AssertionError(f"no Assert node parsed from: {source!r}")


# ---------------------------------------------------------------------------
# Unit tests: _is_called_attribute_assertion
# ---------------------------------------------------------------------------


class TestIsCalledAttributeAssertion:
    """The AST predicate must match the three forbidden shapes only."""

    @pytest.mark.parametrize(
        "source",
        [
            "assert mock.called",
            "assert mock.method.called",
            "assert obj.attr.deep.chain.called",
            "assert mock.called is True",
            "assert mock.called == True",
            "assert mock.method.called is True",
            "assert mock.method.called == True",
        ],
    )
    def test_canonical_forms_flagged(self, source: str) -> None:
        """Each canonical bare-attribute or truth-comparison form is flagged."""
        assert _is_called_attribute_assertion(_parse_assert(source))

    @pytest.mark.parametrize(
        "source",
        [
            # Count comparison — out of scope for the narrow rail
            "assert mock.call_count >= 1",
            "assert mock.called == 3",
            # is False — rare but legitimate
            "assert mock.called is False",
            # Identifier collisions
            "assert obj.is_called",
            "assert called",
            # T10 negative — same-shape but wrong attribute
            "assert mock.method.return_value == 1",
        ],
    )
    def test_non_called_assertions_not_flagged(self, source: str) -> None:
        """Same-shape assertions that aren't the bare ``.called`` form pass."""
        assert not _is_called_attribute_assertion(_parse_assert(source))


# ---------------------------------------------------------------------------
# Unit tests: opt-out marker
# ---------------------------------------------------------------------------


class TestOptOutMarker:
    """``# noqa: T3`` opt-out is recognised on the assertion line."""

    @pytest.mark.parametrize(
        "line",
        [
            "assert mock.called  # noqa: T3",
            "assert mock.called  # noqa: T3 reason: testing the mock library itself",
            "assert mock.called  #noqa: T3",  # tighter spacing
        ],
    )
    def test_recognises_opt_out(self, line: str) -> None:
        """Canonical and tighter-spacing forms of the marker are recognised."""
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
        """Marker text without the exact ``noqa: T3`` form is rejected."""
        assert not _has_opt_out(line)


# ---------------------------------------------------------------------------
# Unit tests: find_violations on synthetic files
# ---------------------------------------------------------------------------


class TestFindViolationsSynthetic:
    """End-to-end checks against ``tmp_path`` source files."""

    # ----- Canonical forms (positive) -----

    def test_bare_called_is_flagged(self, tmp_path: Path) -> None:
        """``assert mock.method.called`` is flagged."""
        target = _synth(tmp_path, "assert mock.method.called\n")
        assert len(find_violations(target)) == 1

    def test_called_is_true_is_flagged(self, tmp_path: Path) -> None:
        """``assert mock.method.called is True`` is flagged."""
        target = _synth(tmp_path, "assert mock.method.called is True\n")
        assert len(find_violations(target)) == 1

    def test_called_eq_true_is_flagged(self, tmp_path: Path) -> None:
        """``assert mock.method.called == True`` is flagged."""
        target = _synth(tmp_path, "assert mock.method.called == True\n")
        assert len(find_violations(target)) == 1

    # ----- Codex r218 / #222 cases the previous regex missed -----

    def test_parenthesised_form_is_flagged(self, tmp_path: Path) -> None:
        """``assert (mock.method.called)`` (codex r218 #1, issue #222)."""
        target = _synth(tmp_path, "assert (mock.method.called)\n")
        assert len(find_violations(target)) == 1

    def test_multiline_parenthesised_form_is_flagged(self, tmp_path: Path) -> None:
        """Multi-line parenthesised — codex r219 multi-line bypass case (#222)."""
        target = _synth(tmp_path, "assert (\n    mock.method.called\n)\n")
        assert len(find_violations(target)) == 1

    def test_assert_with_message_is_flagged(self, tmp_path: Path) -> None:
        """``assert mock.method.called, 'must be called'`` (codex r218 #2, #222)."""
        target = _synth(tmp_path, 'assert mock.method.called, "must be called"\n')
        assert len(find_violations(target)) == 1

    def test_assert_with_message_and_truth_compare_is_flagged(self, tmp_path: Path) -> None:
        """Combined: ``assert mock.method.called is True, "msg"``."""
        target = _synth(tmp_path, 'assert mock.method.called is True, "msg"\n')
        assert len(find_violations(target)) == 1

    # ----- Negative / opt-out cases -----

    def test_method_form_not_flagged(self, tmp_path: Path) -> None:
        """The canonical ``mock.assert_called()`` form is NOT flagged."""
        target = _synth(tmp_path, "mock.method.assert_called()\n")
        assert find_violations(target) == []

    def test_count_comparison_not_flagged(self, tmp_path: Path) -> None:
        """``assert mock.call_count >= 1`` is out of scope for the narrow rail."""
        target = _synth(tmp_path, "assert mock.call_count >= 1\n")
        assert find_violations(target) == []

    def test_is_false_not_flagged(self, tmp_path: Path) -> None:
        """``assert mock.called is False`` is rare but legitimate."""
        target = _synth(tmp_path, "assert mock.called is False\n")
        assert find_violations(target) == []

    def test_string_literal_containing_pattern_not_flagged(self, tmp_path: Path) -> None:
        """A string literal containing the pattern text is not an assertion."""
        target = _synth(tmp_path, 'msg = "assert mock.called"\n')
        assert find_violations(target) == []

    def test_docstring_containing_pattern_not_flagged(self, tmp_path: Path) -> None:
        """A docstring containing the pattern text is not an assertion."""
        target = _synth(
            tmp_path,
            '"""Example fixture:\n\n    assert mock.method.called\n"""\n',
        )
        assert find_violations(target) == []

    def test_comment_with_pattern_not_flagged(self, tmp_path: Path) -> None:
        """A comment with the pattern text is not an assertion."""
        target = _synth(tmp_path, "# example: assert mock.called\n")
        assert find_violations(target) == []

    def test_marked_assertion_is_not_flagged(self, tmp_path: Path) -> None:
        """``# noqa: T3`` opt-out exempts the line."""
        target = _synth(
            tmp_path,
            "assert mock.method.called  # noqa: T3 reason: legacy\n",
        )
        assert find_violations(target) == []

    def test_syntax_error_yields_no_violations(self, tmp_path: Path) -> None:
        """A file that fails to parse yields zero violations."""
        target = _synth(tmp_path, "def broken(\n")
        assert find_violations(target) == []


# ---------------------------------------------------------------------------
# Full-suite assertion: zero violations on the live tree
# ---------------------------------------------------------------------------


class TestFullSuite:
    """The rail must pass against the live ``tests/`` tree."""

    def test_no_violations_on_current_tree(self) -> None:
        """The live ``tests/`` tree has no unmarked ``assert <mock>.called``."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"T3 narrow rail flagged unmarked violation(s):\n{result.stderr}"
        )
