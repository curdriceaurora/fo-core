#!/usr/bin/env python3
"""Atomic-write rail: persistent file writes in src/ must be crash-safe.

Background
----------
PRs #176 / #195 / #197 / #203 / #204 added the ``utils.atomic_write`` helper
suite (``atomic_write_text``, ``atomic_write_bytes``, ``atomic_write_with``,
``append_durable``) and converted 19+ call sites to crash-safe writes.

This rail blocks regressions: any ``Path.write_text``, ``Path.write_bytes``,
or ``open(p, "w"|"wb"|"a"|"ab")`` call in ``src/`` that is NOT marked with an
explicit ``# atomic-write: ok — <reason>`` opt-out comment is flagged.

Scope
-----
- ``src/**/*.py`` only — tests/scripts/docs are out of scope.
- Allowlisted files (the helper module itself + cross-platform fsync shim):
    ``src/utils/atomic_write.py``
    ``src/utils/atomic_io.py``

Opt-out marker
--------------
Add ``# atomic-write: ok — <one-line-reason>`` to the call line. Reason
categories that are accepted as legitimate non-atomic writes:

    user output      — user-supplied path, one-shot CLI export, retry-on-fail
    manual temp+replace — site already uses tempfile.NamedTemporaryFile + os.replace
    pid file         — created via O_EXCL elsewhere (atomicity via the lock file)
    journal append   — log/journal that uses append_durable semantics elsewhere

Example::

    cache_path.write_text(payload)  # atomic-write: ok — manual temp+replace pattern below

Exit 0 = no violations.
Exit 1 = violations found. Offending lines are printed to stderr.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _ROOT / "src"

# Files that own the atomic-write primitives themselves — exempt from the
# rail (they implement the pattern; they don't need to opt out of it).
_ALLOWLISTED_FILES = frozenset(
    {
        "src/utils/atomic_write.py",
        "src/utils/atomic_io.py",
    }
)

# Forbidden write patterns: Path.write_text(, Path.write_bytes(,
# open(<...>, "w"|"wb"|"a"|"ab").  Single-line forms are matched by
# _PATTERNS; multi-line ``open()`` calls (where the mode string is on a
# subsequent line) are detected by _OPEN_RE + _WRITE_MODE_RE in
# find_violations().
_PATTERNS = (
    re.compile(r"\.write_text\("),
    re.compile(r"\.write_bytes\("),
    re.compile(r"""\bopen\([^)]*['"]w[ba]?['"]"""),
    re.compile(r"""\bopen\([^)]*['"]a[b]?['"]"""),
)

# Used to detect the *start* of a multi-line open() call.
_OPEN_RE = re.compile(r"\bopen\(")
# Matches a write/append mode string used in the lookahead window for
# multi-line open() detection.
_WRITE_MODE_RE = re.compile(r"""['"](?:w[ba]?|a[b]?)['"]""")

# Opt-out marker.  Checked against the *comment portion* of the line only
# (see _comment_portion) so a marker embedded inside a string literal does
# not incorrectly satisfy the rule.
_OPT_OUT_RE = re.compile(r"#\s*atomic-write:\s*ok\b")


def _is_comment_line(line: str) -> bool:
    """True if the line (after whitespace) starts with ``#``."""
    return line.lstrip().startswith("#")


def _comment_portion(line: str) -> str:
    """Return everything from the first ``#`` that is outside a string literal.

    Walks *line* left-to-right tracking single- and double-quoted string state
    (including backslash escapes) and returns the substring starting at the
    first ``#`` that falls outside any quote. Returns ``""`` when no such
    ``#`` exists.
    """
    in_str: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\":
                i += 2  # skip escaped character
                continue
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch == "#":
            return line[i:]
        i += 1
    return ""


def _has_opt_out(line: str) -> bool:
    """True if the line's trailing comment contains the ``# atomic-write: ok`` marker."""
    return bool(_OPT_OUT_RE.search(_comment_portion(line)))


def _is_in_docstring_or_string(line: str) -> bool:
    """Heuristic: line is dominated by a docstring/string literal.

    Conservative — a line that contains a forbidden pattern *only* inside a
    string literal (e.g. an example in a docstring) should not trip the rail.
    Detection: line, after stripping leading whitespace, starts with a quote
    char or with the line being part of a triple-quoted block (``\"\"\"`` or
    ``'''``). Misses some cases but cheap and false-positive-safe.
    """
    stripped = line.lstrip()
    if stripped.startswith(('"""', "'''", '"', "'")):
        return True
    return False


def _matches_forbidden(line: str) -> bool:
    """True if the line contains any forbidden write pattern."""
    return any(pat.search(line) for pat in _PATTERNS)


def _iter_src_files() -> list[Path]:
    """Yield every ``.py`` file under ``src/``."""
    return sorted(_SRC_DIR.rglob("*.py"))


_MARKER_LOOKAHEAD = 6
"""Number of lines past the matched call to scan for the opt-out marker.

``ruff format`` may split long calls across lines, leaving the
``# atomic-write: ok`` comment on a later line (typically on the closing
``)``). A small lookahead window catches these without false positives.
"""


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return [(line_number, line_text)] for each unexempted write site.

    Files outside ``_ROOT`` (e.g. ``tmp_path`` synthetic files in unit tests)
    bypass the allowlist check — they're never the real ``src/utils/atomic_write.py``
    so there's nothing to exempt at the file level.
    """
    if path.is_relative_to(_ROOT):
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _ALLOWLISTED_FILES:
            return []

    violations: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    lines = text.splitlines()
    in_triple_quote = False
    for idx, raw in enumerate(lines):
        lineno = idx + 1
        # Track triple-quoted string blocks so docstring examples don't trip
        # the rail. The check is heuristic — counts ``\"\"\"`` toggles.
        triple_count = raw.count('"""') + raw.count("'''")
        if triple_count % 2 == 1:
            in_triple_quote = not in_triple_quote
            continue
        if in_triple_quote:
            continue
        if not _matches_forbidden(raw):
            # Also detect multi-line open() calls: ``open(`` on one line,
            # mode string on a subsequent line (ruff format splits long
            # argument lists). Scan the next _MARKER_LOOKAHEAD lines for a
            # write/append mode string.
            if not _OPEN_RE.search(raw):
                continue
            rest = lines[idx + 1 : idx + _MARKER_LOOKAHEAD]
            if not any(_WRITE_MODE_RE.search(ln) for ln in rest):
                continue
        if _is_comment_line(raw):
            continue
        if _is_in_docstring_or_string(raw):
            continue
        # Marker may be on the matched line OR on one of the next few
        # lines (ruff format often moves a long-line trailing comment to
        # the closing-paren line). Scan a bounded window forward.
        window = lines[idx : idx + _MARKER_LOOKAHEAD]
        if any(_has_opt_out(w) for w in window):
            continue
        violations.append((lineno, raw.rstrip()))
    return violations


def main() -> int:
    """Scan ``src/`` and print violations to stderr; exit 1 if any."""
    all_violations: list[tuple[Path, int, str]] = []
    for path in _iter_src_files():
        for lineno, line in find_violations(path):
            all_violations.append((path.relative_to(_ROOT), lineno, line))

    if not all_violations:
        return 0

    print(
        "ERROR (atomic-write): persistent write call in src/ without atomic\n"
        "helper or opt-out marker. Use one of:\n"
        "  - utils.atomic_write.atomic_write_text / atomic_write_bytes / atomic_write_with\n"
        "  - utils.atomic_write.append_durable (for journal/log appends)\n"
        "Or, for legitimate non-atomic writes, append:\n"
        "  # atomic-write: ok — <reason>\n"
        "Accepted reasons: user output, manual temp+replace, pid file, journal append.\n",
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
