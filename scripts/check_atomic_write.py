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

Detection
---------
AST-based. Walks every Python source file in ``src/`` looking for:

- ``<expr>.write_text(...)`` / ``<expr>.write_bytes(...)`` calls.
- ``open(...)`` calls where the mode argument is a string literal matching
  ``w``, ``wb``, ``a``, or ``ab``. The mode may be the second positional
  argument or a ``mode=`` keyword. The first argument can be any expression
  (including nested calls like ``open(Path(name).with_suffix('.json'), 'w')``
  — codex r219).

String-literal forbidden patterns are NOT matched (e.g.
``logger.debug("open(path, 'w')")`` is a string, not a call — codex r219).

Scope
-----
- ``src/**/*.py`` only — tests/scripts/docs are out of scope.
- Allowlisted files (the helper module itself + cross-platform fsync shim):
    ``src/utils/atomic_write.py``
    ``src/utils/atomic_io.py``

Opt-out marker
--------------
Add ``# atomic-write: ok — <one-line-reason>`` somewhere within ±6 lines of
the call.  Three placements are accepted:

- Trailing comment on the call line itself.
- Trailing comment on the closing-paren line of a multi-line call (ruff
  format frequently splits long calls).
- Standalone comment line on the line(s) immediately above the call. This
  placement avoids modifying the call line itself, which keeps the
  diff-coverage gate from flagging the (already-uncovered) call line as a
  newly-changed-but-uncovered line.

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

# Modes that constitute a forbidden write/append.  Read modes (``r``,
# ``rb``, etc.) are not forbidden.
_FORBIDDEN_MODES: frozenset[str] = frozenset({"w", "wb", "a", "ab", "w+", "wb+", "a+", "ab+"})

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
    """True if *node* is ``open(...)`` (the bare builtin, not ``<obj>.open(...)``)."""
    func = node.func
    return isinstance(func, ast.Name) and func.id == "open"


def _extract_mode_string(node: ast.Call) -> str | None:
    """Return the mode string passed to ``open(path, mode)`` if statically known.

    Handles both the positional second argument and the ``mode=`` keyword.
    Returns ``None`` if the mode is dynamic (variable, computed, etc.) — a
    dynamic mode could be a write or read; we conservatively skip rather
    than false-flag.
    """
    if len(node.args) >= 2:
        mode_arg = node.args[1]
        if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
            return mode_arg.value
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
    return mode is not None and mode in _FORBIDDEN_MODES


def _has_opt_out_in_window(lines: list[str], call_line: int) -> bool:
    """True if any line in the marker window contains ``# atomic-write: ok``.

    *call_line* is 1-based. The window covers ``call_line - _MARKER_WINDOW_ABOVE``
    through ``call_line + _MARKER_WINDOW_BELOW`` (inclusive), clamped to the
    file bounds.
    """
    start = max(1, call_line - _MARKER_WINDOW_ABOVE)
    end = min(len(lines), call_line + _MARKER_WINDOW_BELOW)
    for lineno in range(start, end + 1):
        if _OPT_OUT_RE.search(lines[lineno - 1]):
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
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (_is_write_text_or_bytes(node) or _is_forbidden_open(node)):
            continue
        if _has_opt_out_in_window(lines, node.lineno):
            continue
        violations.append((node.lineno, _line_excerpt(lines, node.lineno)))
    return violations


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
        "Or, for legitimate non-atomic writes, place a comment within ±6 lines:\n"
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
