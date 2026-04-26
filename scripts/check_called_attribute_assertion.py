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

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _ROOT / "tests"

# Match `assert <chain>.called` optionally followed by ` is True` or ` == True`.
# Anchored to end-of-line (modulo trailing comment / whitespace) so we
# don't catch substring uses like ``assert obj.called_once_with(x)``
# (that's a Mock method, not the attribute) or comparisons like
# ``assert mock.called == 3`` (that's a count check, T3 allows under
# noqa).  The precise allowed shapes are bare attribute, `is True`, and
# `== True`.
#
# The expression itself may be parenthesised — `assert (mock.called)` is
# common when the chain is long enough to wrap, and `(...) is True` is
# also common.  The regex therefore accepts an optional balanced pair
# of parens around the ``<chain>.called`` portion (codex r218).
_PATTERN = re.compile(
    r"""^\s*assert\s+               # leading 'assert'
        (?:                          # one of:
            [\w.]+\.called           #   <chain>.called  (no parens)
          | \(\s*[\w.]+\.called\s*\) #   ( <chain>.called )  (parens)
        )
        \s*                          # optional ws
        (?:is\s+True|==\s*True)?     # optional truth comparison
        \s*                          # trailing ws
        (?:\#.*)?                    # optional trailing comment
        $""",
    re.VERBOSE,
)

# Opt-out marker. Matched anywhere in the trailing comment.
_NOQA_RE = re.compile(r"#\s*noqa:\s*T3\b")


def _is_comment_line(line: str) -> bool:
    """True if the line (after whitespace) starts with ``#``."""
    return line.lstrip().startswith("#")


def _has_opt_out(line: str) -> bool:
    """True if the line contains a ``# noqa: T3`` marker."""
    return bool(_NOQA_RE.search(line))


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return [(line_number, line_text)] for each unexempted match.

    Tracks triple-quoted string blocks so that fixtures embedded in
    docstrings (notably ``tests/ci/test_*.py`` rails that demonstrate
    the forbidden pattern inside ``dedent('''...''')`` blocks) don't
    false-flag. The check is heuristic — counts ``\"\"\"`` / ``'''``
    toggles per line.
    """
    violations: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    in_triple_quote = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        triple_count = raw.count('"""') + raw.count("'''")
        if triple_count % 2 == 1:
            in_triple_quote = not in_triple_quote
            continue
        if in_triple_quote:
            continue
        if _is_comment_line(raw):
            continue
        if not _PATTERN.match(raw):
            continue
        if _has_opt_out(raw):
            continue
        violations.append((lineno, raw.rstrip()))
    return violations


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
