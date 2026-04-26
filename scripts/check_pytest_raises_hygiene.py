#!/usr/bin/env python3
"""pytest.raises hygiene rail: no mock assertion after `raise` inside the block.

Background
----------
PR-A (#213) enabled ruff PT012, which flags any multi-statement
``pytest.raises`` block. That covers the common form of this anti-pattern.
However, 11 sites in the codebase carry ``# noqa: PT012`` because their
multi-statement bodies are genuinely required (transaction rollback tests,
context-manager exit semantics under exception, generator double-``next()``,
``importlib.reload`` under patched ``sys.modules``, ``try/except``
subclass-non-catching, ``typer.Exit`` re-raise).

Inside those ``# noqa: PT012`` blocks, PT012 is silent — which means a
mock assertion mistakenly placed AFTER the ``raise`` is not caught:

    with pytest.raises(Foo):  # noqa: PT012 — context-manager exit semantics
        setup()
        raise FooError("boom")
        mock.assert_called_once()   # <-- UNREACHABLE; PT012 suppressed

This rail closes that hole. It walks every ``with pytest.raises(...):``
block, finds an unconditional top-level ``raise``, and flags any mock
assertion (``mock.X.assert_called*(...)`` or ``assert mock.X.called``)
that follows it in the same block.

Scope
-----
- ``tests/**/*.py`` only — the pattern only appears in tests.
- Detects unconditional top-level ``raise`` (not raises inside nested
  ``if`` / ``try`` blocks, which may be reachable).
- Detects two mock-assertion forms:
  - ``Expr(Call(...assert_called*...))`` — ``mock.X.assert_called(...)`` etc.
  - ``Assert(test=Attribute(attr='called'))`` — ``assert mock.X.called``.

Exit 0 = no violations.
Exit 1 = violations found. Offending lines are printed to stderr.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _ROOT / "tests"

# Mock-assertion method names that imply a payload check belongs OUTSIDE
# the ``pytest.raises`` block (after the ``with`` exits). Anything from
# this set after a ``raise`` is unreachable.
_MOCK_ASSERTION_NAMES: frozenset[str] = frozenset(
    {
        "assert_called",
        "assert_called_once",
        "assert_called_with",
        "assert_called_once_with",
        "assert_any_call",
        "assert_has_calls",
        "assert_not_called",
    }
)


def _is_pytest_raises(item: ast.withitem) -> bool:
    """True if *item* is ``pytest.raises(...)`` (with or without ``as`` capture)."""
    expr = item.context_expr
    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    if not (isinstance(func, ast.Attribute) and func.attr == "raises"):
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "pytest"


def _is_mock_assertion(stmt: ast.stmt) -> bool:
    """True if *stmt* is one of the recognised mock-assertion forms."""
    # Form A: <expr>.assert_called*(...)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        if isinstance(func, ast.Attribute) and func.attr in _MOCK_ASSERTION_NAMES:
            return True
    # Form B: assert <expr>.called
    if isinstance(stmt, ast.Assert):
        test = stmt.test
        if isinstance(test, ast.Attribute) and test.attr == "called":
            return True
    return False


def _violations_in_block(body: list[ast.stmt]) -> list[ast.stmt]:
    """Return mock-assertion statements that appear after an unconditional ``raise``.

    Only top-level ``raise`` in the block body counts. A ``raise`` inside a
    nested ``if`` / ``try`` / ``for`` does not terminate the surrounding
    block unconditionally, so subsequent statements remain reachable.
    """
    found: list[ast.stmt] = []
    seen_raise = False
    for stmt in body:
        if isinstance(stmt, ast.Raise):
            seen_raise = True
            continue
        if seen_raise and _is_mock_assertion(stmt):
            found.append(stmt)
    return found


def _iter_statement_blocks(node: ast.AST) -> list[list[ast.stmt]]:
    """Yield every ``list[ast.stmt]`` directly attached to *node*.

    Excludes nested function / class / lambda scopes — a ``raise`` followed
    by a mock assertion inside a nested function definition is a different
    control-flow context (the function body is not executed at the
    pytest.raises call site, so no unreachable-code claim applies).
    """
    blocks: list[list[ast.stmt]] = []
    if isinstance(node, ast.With | ast.AsyncWith):
        blocks.append(node.body)
    elif isinstance(node, ast.For | ast.AsyncFor | ast.While):
        blocks.append(node.body)
        blocks.append(node.orelse)
    elif isinstance(node, ast.If):
        blocks.append(node.body)
        blocks.append(node.orelse)
    elif isinstance(node, ast.Try):
        blocks.append(node.body)
        blocks.append(node.orelse)
        blocks.append(node.finalbody)
        for handler in node.handlers:
            blocks.append(handler.body)
    return blocks


_NEW_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_unreachable_mock_assertions(
    body: list[ast.stmt], parent_unreachable: bool
) -> list[ast.stmt]:
    """Return mock-assertion statements that are unreachable in *body*.

    Reachability is propagated across nested blocks (codex r219 #4): once a
    top-level ``ast.Raise`` is seen in *body*, every subsequent statement —
    including its nested ``if`` / ``for`` / ``while`` / ``try`` / ``with``
    bodies — is unreachable. Mock assertions found in those statements are
    flagged regardless of how deeply nested they are.

    Function / class / lambda definitions are NOT descended: a ``raise``
    in the enclosing pytest.raises body doesn't make a *defined-but-not-yet-
    called* function body unreachable. Those open new control-flow scopes
    not exercised at the pytest.raises call site.

    *parent_unreachable* propagates from a caller block whose own
    top-level ``Raise`` already fired before the nested scope was entered.
    """
    found: list[ast.stmt] = []
    seen_raise = False
    for stmt in body:
        unreachable = parent_unreachable or seen_raise
        if unreachable and _is_mock_assertion(stmt):
            found.append(stmt)
        if not isinstance(stmt, _NEW_SCOPE_TYPES):
            for nested in _iter_statement_blocks(stmt):
                found.extend(
                    _walk_unreachable_mock_assertions(nested, parent_unreachable=unreachable)
                )
        if isinstance(stmt, ast.Raise):
            seen_raise = True
    return found


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return [(line_number, source_line_excerpt)] for each unreachable mock assertion.

    Walks every ``with pytest.raises(...):`` block. Reachability is
    propagated across all nested blocks via
    ``_walk_unreachable_mock_assertions`` — a top-level ``raise`` makes
    everything below it (including statements inside nested ``if`` /
    ``for`` / ``while`` / ``try`` / ``with`` bodies) unreachable, so a
    mock assertion buried under those nested constructs is still flagged
    (codex r219 #4).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        if not any(_is_pytest_raises(item) for item in node.items):
            continue
        for stmt in _walk_unreachable_mock_assertions(node.body, parent_unreachable=False):
            try:
                line = ast.unparse(stmt)
            except (AttributeError, ValueError):
                line = f"<line {stmt.lineno}>"
            violations.append((stmt.lineno, line))
    return sorted(violations)


def _iter_test_files() -> list[Path]:
    """Yield every ``.py`` file under ``tests/``."""
    return sorted(_TESTS_DIR.rglob("*.py"))


def main() -> int:
    """Scan ``tests/`` and print violations to stderr; exit 1 if any."""
    all_violations: list[tuple[Path, int, str]] = []
    for path in _iter_test_files():
        for lineno, line in find_violations(path):
            all_violations.append((path.relative_to(_ROOT), lineno, line))

    if not all_violations:
        return 0

    print(
        "ERROR (pytest.raises-hygiene): mock assertion after `raise` inside\n"
        "a `pytest.raises(...)` block is unreachable.\n"
        "Move the mock assertion AFTER the `with pytest.raises(...):` block "
        "exits — the `raise` terminates control flow, so subsequent statements\n"
        "in the block never execute.\n",
        file=sys.stderr,
    )
    for path, lineno, line in all_violations:
        print(f"  {path}:{lineno}: {line}", file=sys.stderr)
    print(
        f"\n{len(all_violations)} violation(s) across "
        f"{len({v[0] for v in all_violations})} file(s).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
