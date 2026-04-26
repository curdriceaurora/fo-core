#!/usr/bin/env python3
"""Atomic-write rail: persistent file writes in src/ must be crash-safe.

Background
----------
PRs #176 / #195 / #197 / #203 / #204 added the ``utils.atomic_write`` helper
suite (``atomic_write_text``, ``atomic_write_bytes``, ``atomic_write_with``,
``append_durable``) and converted 19+ call sites to crash-safe writes.

This rail blocks regressions: any ``Path.write_text``, ``Path.write_bytes``,
or ``open(p, mode)`` call in ``src/`` whose mode is one of
``w``, ``wb``, ``a``, ``ab``, ``w+``, ``wb+``, ``a+``, ``ab+`` and which is
NOT marked with an explicit ``# atomic-write: ok — <reason>`` opt-out comment
is flagged.

Detection
---------
AST-based. Walks every Python source file in ``src/`` looking for:

- ``<expr>.write_text(...)`` / ``<expr>.write_bytes(...)`` calls.
- ``open(...)`` calls where the mode argument is a string literal in the
  ``_FORBIDDEN_MODES`` set. The mode may be the second positional argument
  or a ``mode=`` keyword. The first argument can be any expression
  (including nested calls like ``open(Path(name).with_suffix('.json'), 'w')``
  — codex r219 #1).

String-literal forbidden patterns are NOT matched (e.g.
``logger.debug("open(path, 'w')")`` is a string, not a call — codex r219 #2).
The opt-out marker is matched against tokenised comments only, so a
string-literal containing the marker text (``msg = "# atomic-write: ok"``)
cannot bypass the rail (CodeRabbit r219).

Scope
-----
- ``src/**/*.py`` only — tests/scripts/docs are out of scope.
- Allowlisted files (the helper module itself + cross-platform fsync shim):
    ``src/utils/atomic_write.py``
    ``src/utils/atomic_io.py``

Opt-out marker
--------------
Add ``# atomic-write: ok — <one-line-reason>`` somewhere in the
``-2 / +6`` line window around the call line (2 lines above through 6 lines
below — see ``_MARKER_WINDOW_ABOVE`` / ``_MARKER_WINDOW_BELOW``). Three
placements are accepted:

- Trailing comment on the call line itself.
- Trailing comment on the closing-paren line of a multi-line call (ruff
  format frequently splits long calls — covered by the +6 lookahead).
- Standalone comment line on the line(s) immediately above the call (covered
  by the -2 lookback). This placement avoids modifying the call line itself,
  which keeps the diff-coverage gate from flagging the (already-uncovered)
  call line as a newly-changed-but-uncovered line.

Reason categories that are accepted as legitimate non-atomic writes:

    user output      — user-supplied path, one-shot CLI export, retry-on-fail
    manual temp+replace — site already uses tempfile.NamedTemporaryFile + os.replace
    pid file         — created via O_EXCL elsewhere (atomicity via the lock file)
    journal append   — log/journal that uses append_durable semantics elsewhere

Example::

    # atomic-write: ok — manual temp+replace pattern below
    with open(temp_path, "w", encoding="utf-8") as f:
        ...

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
_SRC_DIR = _ROOT / "src"

# Files that own the atomic-write primitives themselves — exempt from the
# rail (they implement the pattern; they don't need to opt out of it).
_ALLOWLISTED_FILES = frozenset(
    {
        "src/utils/atomic_write.py",
        "src/utils/atomic_io.py",
    }
)

# Characters that signal a write/append/exclusive-create mode in
# ``open(path, mode)``.  Python ``open`` modes are character flags whose
# order doesn't matter (``"wb"`` == ``"bw"``, ``"w+b"`` == ``"wb+"``,
# etc.) and which can include ``b``/``t``/``+`` flags. Any mode string
# containing ``w``, ``a``, or ``x`` opens the file for writing in a way
# that requires atomicity:
#
# - ``w``/``w+`` truncate the file before writing.
# - ``a``/``a+`` append, but a torn-write at the end is still observable.
# - ``x``/``x+`` create-or-fail; a torn write leaves a partial file the
#   next ``x`` open will refuse.
#
# Pure read modes (``r``, ``rb``, ``rt``, ``r+`` — read+write WITHOUT
# truncation, file must exist) are not in scope.  ``r+`` does write, but
# a crash leaves the original file modified-in-place rather than torn,
# which is a different concern from the rail's truncate/append target.
#
# Codex r219 #3: the previous literal set ``{"w", "wb", "a", "ab", ...}``
# missed valid aliases like ``"wt"``, ``"at"``, ``"w+b"``, ``"a+t"``.
# Character-based detection covers every order/flag combination.
_FORBIDDEN_MODE_CHARS: frozenset[str] = frozenset("wax")


def _mode_is_forbidden(mode: str) -> bool:
    """True if *mode* opens for writing/appending/exclusive-create.

    Order-and-flag-independent: ``"wb"``, ``"bw"``, ``"w+b"``, ``"wt+"``
    all map to forbidden because each contains ``w``. Pure-read modes
    (``"r"``, ``"rb"``, ``"r+"``) contain no character in
    ``_FORBIDDEN_MODE_CHARS`` and are allowed.
    """
    return any(c in _FORBIDDEN_MODE_CHARS for c in mode)


# Backwards-compat: tests historically asserted membership in this set.
# Keep it as the explicit canonical-string enumeration; runtime detection
# uses _mode_is_forbidden, which is strictly more permissive.
_FORBIDDEN_MODES: frozenset[str] = frozenset(
    {
        "w",
        "wb",
        "wt",
        "a",
        "ab",
        "at",
        "x",
        "xb",
        "xt",
        "w+",
        "wb+",
        "w+b",
        "wt+",
        "w+t",
        "a+",
        "ab+",
        "a+b",
        "at+",
        "a+t",
        "x+",
        "xb+",
        "x+b",
    }
)

# Opt-out marker.  Searched for in trailing comments AND on standalone
# comment lines within the configured window (see ``_MARKER_WINDOW``).
_OPT_OUT_RE = re.compile(r"#\s*atomic-write:\s*ok\b")

# How far above / below the call line to scan for the opt-out marker.
# ABOVE accommodates the standalone-comment placement; BELOW accommodates
# ruff-format splitting a long call across multiple lines.
_MARKER_WINDOW_ABOVE = 2
_MARKER_WINDOW_BELOW = 6


def _is_write_text_or_bytes(node: ast.Call) -> bool:
    """True if *node* is ``<expr>.write_text(...)`` or ``<expr>.write_bytes(...)``."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    return func.attr in ("write_text", "write_bytes")


def _is_open_call(node: ast.Call) -> bool:
    """True if *node* is ``open(...)`` or ``<obj>.open(...)``."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "open":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "open":
        return True
    return False


_MODE_CHARS = frozenset("rwaxbt+")


def _looks_like_mode_literal(value: object) -> bool:
    """True if *value* is a string that plausibly is a Python ``open()`` mode.

    A Python mode string is short (1-4 chars) and consists of characters
    drawn from ``rwaxbt+`` with exactly one of the primary action chars
    ``rwax``. This rejects things that happen to be string Constants but
    are clearly file paths (``"write.log"`` has ``.`` and ``l``, ``o``,
    ``g``) — important because attribute-style calls like
    ``tarfile.open("write.log", "wb")`` have the path at args[0] and the
    mode at args[1], while ``Path("x").open("w")`` has the mode at
    args[0]. The strict pattern lets us check BOTH positions safely.
    """
    if not isinstance(value, str):
        return False
    if not 1 <= len(value) <= 4:
        return False
    if not all(c in _MODE_CHARS for c in value):
        return False
    primary_chars = sum(1 for c in value if c in "rwax")
    return primary_chars == 1


def _extract_mode_string(node: ast.Call) -> str | None:
    """Return the mode string passed to ``open()`` if statically known.

    Three call shapes the rail must handle:

    - Builtin ``open(path, mode, ...)``: mode at ``args[1]``.
    - Module-level ``<module>.open(path, mode, ...)`` such as
      ``tarfile.open(path, "w")`` or ``gzip.open(path, "wb")``:
      mode at ``args[1]``.
    - Instance-method ``<obj>.open(mode, ...)`` such as
      ``Path("x").open("w")``: mode at ``args[0]``.

    Disambiguation strategy (codex r219 #7):

    1. If the call has 2+ positional args, prefer ``args[1]`` — that's
       the mode position for the builtin / module-level shapes (the
       majority case). This avoids the false-negative
       ``open("r", "w")`` (where the previous "first-matching-wins"
       returned ``"r"``) and the false-positive ``open("a", "r")``.
    2. Fall back to ``args[0]`` if it's a literal mode — this handles
       the instance-method shape ``Path("x").open("w")`` where there's
       only one positional arg, or where ``args[1]`` is a non-mode
       value like a buffering int.
    3. Finally check the ``mode=`` keyword.

    The strict ``_looks_like_mode_literal`` pattern (length ≤ 4,
    characters from ``rwaxbt+``, exactly one primary action char) keeps
    file-path strings like ``"write.log"`` from being mistaken for modes.

    Returns ``None`` if no statically-known mode is present — a dynamic
    mode (variable, computed, etc.) could be a write or read; we skip
    rather than false-flag.
    """
    if len(node.args) >= 2:
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and _looks_like_mode_literal(arg.value):
            return arg.value
    if len(node.args) >= 1:
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and _looks_like_mode_literal(arg.value):
            return arg.value
    for kw in node.keywords:
        if kw.arg == "mode":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
    return None


def _is_forbidden_open(node: ast.Call) -> bool:
    """True if *node* is ``open(<path>, "<write-mode>")``."""
    if not _is_open_call(node):
        return False
    mode = _extract_mode_string(node)
    return mode is not None and _mode_is_forbidden(mode)


def _collect_marker_comment_lines(source: str) -> set[int]:
    """Return the 1-based line numbers of every Python comment carrying the marker.

    Tokenises *source* and only inspects ``tokenize.COMMENT`` tokens — string
    literals containing ``# atomic-write: ok`` (e.g. ``msg = "# atomic-write: ok"``)
    are NOT comments and therefore cannot exempt a real write call (CodeRabbit
    r219 #2 — bypass-via-string-literal).

    Falls back to an empty set on tokenise failure (e.g. an unterminated
    string literal in a syntactically broken file). The caller's AST parse
    will already have failed in that case, so no violations are reported
    either way.
    """
    marker_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and _OPT_OUT_RE.search(tok.string):
                marker_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()
    return marker_lines


def _has_opt_out_in_window(marker_lines: set[int], call_line: int, total_lines: int) -> bool:
    """True if any *marker_lines* entry falls inside the call's marker window.

    *call_line* is 1-based. The window covers ``call_line - _MARKER_WINDOW_ABOVE``
    through ``call_line + _MARKER_WINDOW_BELOW`` (inclusive), clamped to the
    file bounds. *marker_lines* is the set returned by
    ``_collect_marker_comment_lines`` — only real comment tokens.
    """
    start = max(1, call_line - _MARKER_WINDOW_ABOVE)
    end = min(total_lines, call_line + _MARKER_WINDOW_BELOW)
    for lineno in range(start, end + 1):
        if lineno in marker_lines:
            return True
    return False


def _line_excerpt(lines: list[str], lineno: int) -> str:
    """Return the source line at *lineno* (1-based), trimmed of trailing whitespace."""
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].rstrip()
    return f"<line {lineno}>"


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return [(line_number, line_text)] for each unexempted write call.

    Uses the AST to locate ``Path.write_text``, ``Path.write_bytes``, and
    ``open(p, "w"|"wb"|"a"|"ab")`` calls. Files outside ``_ROOT`` (e.g.
    ``tmp_path`` synthetic files in unit tests) bypass the allowlist check —
    they're never the real ``src/utils/atomic_write.py`` so there's nothing
    to exempt at the file level.

    Files that fail to parse (invalid Python) yield zero violations — a
    syntax error is a separate problem and shouldn't be conflated with
    rail enforcement.
    """
    if path.is_relative_to(_ROOT):
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _ALLOWLISTED_FILES:
            return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    marker_lines = _collect_marker_comment_lines(source)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (_is_write_text_or_bytes(node) or _is_forbidden_open(node)):
            continue
        if _has_opt_out_in_window(marker_lines, node.lineno, len(lines)):
            continue
        violations.append((node.lineno, _line_excerpt(lines, node.lineno)))
    return sorted(violations)


def _iter_src_files() -> list[Path]:
    """Yield every ``.py`` file under ``src/``."""
    return sorted(_SRC_DIR.rglob("*.py"))


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
        "Or, for legitimate non-atomic writes, place a comment in the -2 / +6\n"
        "line window around the call:\n"
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
