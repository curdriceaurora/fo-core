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
import io
import re
import sys
import tokenize
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _ROOT / "tests"

# Opt-out marker. Searched against tokenised ``COMMENT`` tokens only —
# never against raw line text — so a marker text inside a string literal
# (``assert mock.called, "# noqa: T3"``) cannot bypass the rail (codex
# r219 #9).
_NOQA_RE = re.compile(r"#\s*noqa:\s*T3\b")


def _collect_noqa_comment_lines(source: str) -> set[int]:
    """Return the 1-based line numbers of every ``# noqa: T3`` *comment* token.

    Tokenises *source* and only inspects ``tokenize.COMMENT`` tokens —
    string literals containing the marker text (e.g.
    ``assert mock.called, "# noqa: T3"``) are NOT comments and therefore
    cannot exempt a real assertion (codex r219 #9 — bypass-via-string-literal).

    Falls back to an empty set on tokenise failure (e.g. an unterminated
    string literal in a syntactically broken file). The caller's AST
    parse will already have failed in that case, so no violations are
    reported either way.
    """
    marker_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and _NOQA_RE.search(tok.string):
                marker_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()
    return marker_lines


def _has_opt_out(line: str) -> bool:
    """True if the line contains a ``# noqa: T3`` marker (raw-text fallback for unit tests).

    The runtime detector uses ``_collect_noqa_comment_lines`` (token-based)
    to avoid bypass via string literals; this helper is preserved for
    direct unit tests of the marker regex.
    """
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
    # Token-aware marker collection — string literals containing the
    # marker text cannot exempt a real assertion (codex r219 #9).
    noqa_lines = _collect_noqa_comment_lines(text)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        if not _is_called_attribute_assertion(node):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", None) or start
        # CodeRabbit r219: a ``# noqa: T3`` placed on the closing-paren
        # line of a multi-line ``assert (\n    mock.called\n)`` form is
        # a natural, ruff-format-friendly placement.  Scan every line
        # the assert spans so the opt-out works for that placement too.
        if any(ln in noqa_lines for ln in range(start, end + 1)):
            continue
        if 0 < start <= len(lines):
            violations.append((start, lines[start - 1].rstrip()))

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
