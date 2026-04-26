#!/usr/bin/env python3
"""T3 narrow rail: no `assert <mock>.called` attribute-style assertion.

Background
----------
T3 (`.claude/rules/test-generation-patterns.md`) catches mock assertions
that check call count without verifying payload. The full T3 has a wide
surface (`call_count >= N`, `assert_called()` without args, etc.) and
many legitimate uses, so a strict rail is too noisy.

This rail enforces the *narrow* form that has zero legitimate uses: the
`assert <obj>.<attr>.called` attribute lookup. The mock library's
canonical equivalent — `<obj>.<attr>.assert_called()` — is one extra
character, more discoverable in IDEs (it's a documented method, not a
flag attribute), and consistent with the rest of the test suite's
assertion style.

Recognised forms (all flagged):

    assert <chain>.called
    assert <chain>.called is True
    assert <chain>.called == True

The fix is mechanical: replace ``assert mock.X.called`` with
``mock.X.assert_called()``.

Scope
-----
- ``tests/**/*.py`` only.
- Honors per-site ``# noqa: T3`` opt-out (rare — the canonical fix is
  cheap, but the marker exists for cases where the attribute access is
  intentional, e.g. testing the mock library itself).

Exit 0 = no violations.
Exit 1 = violations found. Offending lines are printed to stderr.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _ROOT / "tests"

# Opt-out marker. Matched anywhere in the trailing comment.
_NOQA_RE = re.compile(r"#\s*noqa:\s*T3\b")


def _has_opt_out(line: str) -> bool:
    """True if the line contains a ``# noqa: T3`` marker."""
    return bool(_NOQA_RE.search(line))


def _is_called_attribute_assertion(node: ast.Assert) -> bool:
    """True if *node* is an `assert <expr>.called` or similar."""
    test = node.test
    # Case 1: assert mock.called
    if isinstance(test, ast.Attribute) and test.attr == "called":
        return True

    # Case 2: assert mock.called is True  OR  assert mock.called == True
    if isinstance(test, ast.Compare):
        if len(test.ops) == 1 and len(test.comparators) == 1:
            left = test.left
            op = test.ops[0]
            right = test.comparators[0]

            if isinstance(left, ast.Attribute) and left.attr == "called":
                if isinstance(op, (ast.Is, ast.Eq)):
                    if isinstance(right, ast.Constant) and right.value is True:
                        return True

    return False


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return [(line_number, line_text)] for each unexempted match.

    Uses AST parsing to accurately find assertions and avoid false positives
    inside strings, docstrings, or multi-statement lines.
    """
    violations: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return violations

    lines = text.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        if _is_called_attribute_assertion(node):
            lineno = node.lineno
            if 0 < lineno <= len(lines):
                line_text = lines[lineno - 1]
                if not _has_opt_out(line_text):
                    violations.append((lineno, line_text.rstrip()))

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
        "ERROR (T3): `assert <mock>.called` attribute-style assertion found.\n"
        "Replace with the canonical mock-library equivalent:\n"
        "  - `assert mock.X.called`  →  `mock.X.assert_called()`\n"
        "  - `assert mock.X.called is True`  →  `mock.X.assert_called()`\n"
        "Or, for an intentional attribute-style check (rare), add\n"
        "`# noqa: T3 reason: <one-line justification>`.\n",
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
