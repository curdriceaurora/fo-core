"""Tests for the pytest.raises hygiene rail (``scripts/check_pytest_raises_hygiene.py``).

Closes the gap PR-A's PT012 enforcement leaves: PT012 is silenced inside the
11 ``# noqa: PT012`` blocks that legitimately need multi-statement bodies
(transaction rollback, context-manager exit semantics, generator
double-``next()``, etc.). The rail in this PR catches mock assertions
mistakenly placed AFTER the ``raise`` inside any of those blocks — those
assertions are unreachable because ``raise`` terminates control flow.

T10 predicate negative-case backfill: every detection branch has a positive
test (the rail flags the surface shape) AND a negative test (the rail does
NOT flag a similar shape that is genuinely reachable).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_pytest_raises_hygiene.py"

# Import the detector directly so we can unit-test helpers without spawning
# a subprocess for every case.
sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_pytest_raises_hygiene import (  # noqa: E402
    _MOCK_ASSERTION_NAMES,
    _is_mock_assertion,
    _is_pytest_raises,
    _violations_in_block,
    find_violations,
)


def _parse_with(source: str) -> ast.With:
    """Return the first ``ast.With`` node in *source*."""
    tree = ast.parse(dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            return node
    raise AssertionError("no With node parsed")


# ---------------------------------------------------------------------------
# Unit tests: _is_pytest_raises
# ---------------------------------------------------------------------------


class TestIsPytestRaises:
    """The detector must recognise canonical pytest.raises shapes."""

    @pytest.mark.parametrize(
        "source",
        [
            "with pytest.raises(ValueError):\n    pass",
            "with pytest.raises(ValueError, match='boom'):\n    pass",
            "with pytest.raises(ValueError) as excinfo:\n    pass",
            "with pytest.raises((ValueError, TypeError)):\n    pass",
        ],
    )
    def test_recognises_pytest_raises(self, source: str) -> None:
        with_node = _parse_with(source)
        assert any(_is_pytest_raises(item) for item in with_node.items)

    @pytest.mark.parametrize(
        "source",
        [
            # T10 negative cases — must NOT match
            "with open('x') as f:\n    pass",
            "with self.raises(ValueError):\n    pass",  # not pytest.raises
            "with patch('foo') as p:\n    pass",
            "with pytest.warns(DeprecationWarning):\n    pass",
        ],
    )
    def test_rejects_non_pytest_raises(self, source: str) -> None:
        with_node = _parse_with(source)
        assert not any(_is_pytest_raises(item) for item in with_node.items)


# ---------------------------------------------------------------------------
# Unit tests: _is_mock_assertion
# ---------------------------------------------------------------------------


class TestIsMockAssertion:
    """The detector must recognise the two canonical mock-assertion forms."""

    @pytest.mark.parametrize(
        "name",
        sorted(_MOCK_ASSERTION_NAMES),
    )
    def test_recognises_mock_call(self, name: str) -> None:
        # Form A: mock.X.assert_called*(...)
        source = f"mock.method.{name}()"
        stmt = ast.parse(source).body[0]
        assert _is_mock_assertion(stmt)

    def test_recognises_assert_called_attribute(self) -> None:
        # Form B: assert mock.X.called
        stmt = ast.parse("assert mock.method.called").body[0]
        assert _is_mock_assertion(stmt)

    @pytest.mark.parametrize(
        "source",
        [
            # T10 negative cases — same surface shapes that must NOT match
            "mock.method.return_value = 1",  # attribute set, not assertion
            "mock.method.call_count == 1",  # comparison, not assertion call
            "result = mock.method.assert_called()",  # assignment, not Expr
            "assert result is True",  # plain assert, not on .called
            "assert mock.method.return_value == 1",  # not .called attr
            "x.assert_something_else()",  # not in assertion-name set
        ],
    )
    def test_rejects_non_mock_assertion(self, source: str) -> None:
        stmt = ast.parse(source).body[0]
        assert not _is_mock_assertion(stmt)


# ---------------------------------------------------------------------------
# Unit tests: _violations_in_block
# ---------------------------------------------------------------------------


class TestViolationsInBlock:
    """Body-level scan must flag unreachable mock assertions only."""

    def test_flags_assertion_after_top_level_raise(self) -> None:
        with_node = _parse_with(
            """
            with pytest.raises(ValueError):
                raise ValueError("boom")
                mock.method.assert_called_once()
            """
        )
        violations = _violations_in_block(with_node.body)
        assert len(violations) == 1

    def test_flags_assert_called_attribute_after_raise(self) -> None:
        with_node = _parse_with(
            """
            with pytest.raises(ValueError):
                raise ValueError()
                assert mock.method.called
            """
        )
        violations = _violations_in_block(with_node.body)
        assert len(violations) == 1

    def test_no_flag_when_assertion_precedes_raise(self) -> None:
        # Statement appears BEFORE raise — reachable; not flagged.
        with_node = _parse_with(
            """
            with pytest.raises(ValueError):
                mock.method.assert_called_once()
                raise ValueError()
            """
        )
        assert _violations_in_block(with_node.body) == []

    def test_no_flag_when_no_top_level_raise(self) -> None:
        # No raise at top level; mock assertion is reachable.
        with_node = _parse_with(
            """
            with pytest.raises(ValueError):
                func_that_might_raise()
                mock.method.assert_called_once()
            """
        )
        assert _violations_in_block(with_node.body) == []

    def test_no_flag_when_raise_is_nested_in_if(self) -> None:
        # T10 negative case: conditional raise; trailing assertion is
        # reachable when the condition is False.
        with_node = _parse_with(
            """
            with pytest.raises(ValueError):
                if condition:
                    raise ValueError()
                mock.method.assert_called_once()
            """
        )
        assert _violations_in_block(with_node.body) == []

    def test_no_flag_when_raise_is_nested_in_try(self) -> None:
        # T10 negative case: raise inside try doesn't terminate the
        # outer block (the except may catch and the body continues).
        with_node = _parse_with(
            """
            with pytest.raises(ValueError):
                try:
                    raise RuntimeError()
                except RuntimeError:
                    raise ValueError()
                mock.method.assert_called_once()
            """
        )
        # The outer raise IS still inside an except branch — the rail
        # treats only top-level raises as terminating, so this stays
        # un-flagged. Conservative is the right default for a rail.
        assert _violations_in_block(with_node.body) == []


# ---------------------------------------------------------------------------
# Unit tests: find_violations on synthetic files
# ---------------------------------------------------------------------------


class TestFindViolationsSynthetic:
    """End-to-end checks against tmp_path test files."""

    def _write(self, tmp_path: Path, content: str) -> Path:
        target = tmp_path / "test_synth.py"
        target.write_text(dedent(content))
        return target

    def test_unreachable_mock_assertion_is_flagged(self, tmp_path: Path) -> None:
        target = self._write(
            tmp_path,
            """
            import pytest

            def test_x():
                with pytest.raises(ValueError):
                    raise ValueError()
                    mock.method.assert_called_once()
            """,
        )
        violations = find_violations(target)
        assert len(violations) == 1
        assert "assert_called_once" in violations[0][1]

    def test_correctly_placed_assertion_is_not_flagged(self, tmp_path: Path) -> None:
        # T10 negative: assertion AFTER the with-block exits is correct.
        target = self._write(
            tmp_path,
            """
            import pytest

            def test_x():
                with pytest.raises(ValueError):
                    raise ValueError()
                mock.method.assert_called_once()
            """,
        )
        assert find_violations(target) == []

    def test_assert_called_attribute_after_raise_is_flagged(self, tmp_path: Path) -> None:
        target = self._write(
            tmp_path,
            """
            import pytest

            def test_x():
                with pytest.raises(ValueError):
                    raise ValueError()
                    assert mock.method.called
            """,
        )
        violations = find_violations(target)
        assert len(violations) == 1
        assert ".called" in violations[0][1]

    def test_pytest_warns_does_not_trigger(self, tmp_path: Path) -> None:
        # T10 negative case: same with-statement shape but pytest.warns,
        # not pytest.raises. Must not flag.
        target = self._write(
            tmp_path,
            """
            import pytest

            def test_x():
                with pytest.warns(DeprecationWarning):
                    raise DeprecationWarning()
                    mock.method.assert_called_once()
            """,
        )
        assert find_violations(target) == []

    def test_nested_with_block_with_raise_then_mock_assertion_is_flagged(
        self, tmp_path: Path
    ) -> None:
        # Codex r217 false-negative case: the raise + mock assertion are
        # both inside an inner ``with manager() as m:`` block nested under
        # the outer ``with pytest.raises(...):``. The pre-fix detector
        # only walked the outer body and missed this. The fixed detector
        # walks every nested statement-list within the pytest.raises
        # subtree.
        target = self._write(
            tmp_path,
            """
            import pytest

            def test_x():
                with pytest.raises(ValueError):  # noqa: PT012
                    with manager() as m:
                        do_setup()
                        raise ValueError()
                        m.cleanup.assert_called_once()
            """,
        )
        violations = find_violations(target)
        assert len(violations) == 1
        assert "assert_called_once" in violations[0][1]

    def test_nested_if_with_raise_then_assertion_is_flagged(self, tmp_path: Path) -> None:
        # The raise is at top level of the if-body (not the outer
        # pytest.raises body), so the immediate-body-only scan would
        # miss it. After the fix, the rail walks into the if-body too.
        target = self._write(
            tmp_path,
            """
            import pytest

            def test_x():
                with pytest.raises(ValueError):  # noqa: PT012
                    if True:
                        raise ValueError()
                        mock.method.assert_called_once()
            """,
        )
        violations = find_violations(target)
        assert len(violations) == 1

    def test_assertion_inside_nested_function_def_is_not_flagged(self, tmp_path: Path) -> None:
        # T10 negative case: the nested def starts a new scope; its body
        # is not executed at the pytest.raises call site. The detector
        # must NOT descend into function definitions.
        target = self._write(
            tmp_path,
            """
            import pytest

            def test_x():
                with pytest.raises(ValueError):
                    raise ValueError()

                    def helper():
                        mock.method.assert_called_once()
            """,
        )
        # The raise + def are at top level of the pytest.raises body. The
        # def itself is not a mock assertion, and we don't descend into
        # its body. So no violations.
        assert find_violations(target) == []

    def test_mocked_pytest_raises_via_alias_is_not_flagged(self, tmp_path: Path) -> None:
        # T10 negative case: an aliased import doesn't match the
        # ``pytest.raises`` attribute pattern. Conservative: detector
        # only matches the canonical form. False negatives are accepted
        # for false-positive immunity (the canonical form is what the
        # codebase uses everywhere — see existing 11 noqa-PT012 sites).
        target = self._write(
            tmp_path,
            """
            from pytest import raises

            def test_x():
                with raises(ValueError):
                    raise ValueError()
                    mock.method.assert_called_once()
            """,
        )
        assert find_violations(target) == []


# ---------------------------------------------------------------------------
# Full-suite assertion: zero unreachable mock assertions on the live tree
# ---------------------------------------------------------------------------


class TestFullSuite:
    """The rail must pass against the live ``tests/`` tree (preventive)."""

    def test_no_violations_on_current_tree(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"pytest.raises hygiene rail flagged unreachable mock assertion(s):\n{result.stderr}"
        )
