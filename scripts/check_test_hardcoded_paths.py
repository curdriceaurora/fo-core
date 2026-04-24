#!/usr/bin/env python3
"""G2 rail: block hardcoded absolute paths in test files.

Unlike the existing G1 pre-commit hook which only greps *added* lines of the
staged diff, G2 scans every ``.py`` file under ``tests/`` in full on every
invocation. This catches:

1. Stale violations in pre-existing files that were not touched on the
   current branch (G1 would miss them).
2. Lines copy-pasted from elsewhere without reformatting.

Exemptions (a line is skipped if any of the below is true):

- The line is a comment (leading ``#`` after whitespace).
- The line ends with ``# noqa: G2`` (optionally followed by a reason in
  parentheses, e.g. ``# noqa: G2 (parser test input)``).
- The match is to a well-known adversarial-input path commonly used as a
  test argument to path-validation code: ``/etc/passwd``, ``/etc/shadow``,
  ``/proc/self/mem``, ``/root/...``, ``/dev/null``, ``/dev/zero``.
  (These are test inputs, not output targets — T13 allows them.)

Blocked patterns (forbidden unless exempted): ``/tmp/``, ``/Users/<name>``,
``/home/<user>``. Pattern: a literal ``/tmp/``, ``/Users/``, or ``/home/``
followed by at least one path component character. The G2 rail mirrors G1's
pattern list so both rails block the same paths.

Exit 0 = no violations.
Exit 1 = violations found. Offending lines are printed to stderr.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Project root — the script lives at ``scripts/check_test_hardcoded_paths.py``.
_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _ROOT / "tests"

# Same pattern set as G1. A match is ``/tmp/<at-least-one-char>``,
# ``/Users/<lowercase-letter-or-underscore>``, or ``/home/<likewise>``.
# The trailing char class excludes bare ``/tmp`` or ``/tmp:`` mentions that
# aren't hardcoded paths (e.g. shell command snippets in docstrings).
_FORBIDDEN = re.compile(r"(/tmp/|/Users/[a-zA-Z_]|/home/[a-zA-Z_])")

# Adversarial inputs that are legitimately hardcoded as test inputs (T13
# allowance). Matching is substring — any line containing one of these is
# exempted for that particular path even if it also matches ``_FORBIDDEN``.
_ADVERSARIAL_INPUTS = (
    "/etc/passwd",
    "/etc/shadow",
    "/proc/self/mem",
    "/root/",
    "/dev/null",
    "/dev/zero",
)

# Allow an in-line ``# noqa: G2`` (optionally followed by a parenthesized
# justification). Anchored at EOL so a ``# noqa:`` embedded inside a string
# literal earlier on the line doesn't satisfy the rule.
_NOQA_RE = re.compile(r"#\s*noqa:\s*G2\b")


def _is_comment_line(line: str) -> bool:
    """True if the line (after whitespace) starts with ``#``."""
    return line.lstrip().startswith("#")


def _has_noqa_g2(line: str) -> bool:
    return bool(_NOQA_RE.search(line))


def _has_adversarial_input(line: str) -> bool:
    return any(marker in line for marker in _ADVERSARIAL_INPUTS)


def _iter_test_files() -> list[Path]:
    """Yield every ``.py`` file under ``tests/``."""
    return sorted(_TESTS_DIR.rglob("*.py"))


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return [(line_number, line_text)] for each unexempted match in ``path``."""
    violations: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not _FORBIDDEN.search(raw):
            continue
        if _is_comment_line(raw):
            continue
        if _has_noqa_g2(raw):
            continue
        if _has_adversarial_input(raw):
            continue
        violations.append((lineno, raw.rstrip()))
    return violations


def main() -> int:
    all_violations: list[tuple[Path, int, str]] = []
    for path in _iter_test_files():
        for lineno, line in find_violations(path):
            all_violations.append((path.relative_to(_ROOT), lineno, line))

    if not all_violations:
        return 0

    print(
        "ERROR (G2): hardcoded '/tmp/', '/Users/', or '/home/' path found in "
        "test file.\n"
        "Use the pytest `tmp_path` fixture instead, or add "
        "`# noqa: G2 (reason)` if the path is genuinely a test input "
        "(e.g. parser input, path-validation adversarial case).\n",
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
