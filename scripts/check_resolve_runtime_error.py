#!/usr/bin/env python3
"""F11-resolve rail: ``Path.resolve()`` calls inside ``try`` blocks must handle ``RuntimeError``.

Background
----------
``Path.resolve()`` raises:

- ``RuntimeError`` on Python < 3.13 for symlink loops.
- ``OSError`` on Python >= 3.13 (or for other filesystem errors).

PRs #168, #173, #195 hardened the codebase by wrapping every CLI-boundary
``resolve()`` call to catch both exceptions. The canonical wrapper is
``src/cli/path_validation.py``. This rail prevents regression: any
``.resolve()`` call found inside a ``try`` block in ``src/`` whose
``except`` clauses do not cover ``RuntimeError`` (directly or via a broad
base class) is flagged.

Calls that are **not** inside any ``try`` block at all are **not** flagged
by this rail — they are a separate concern. The rail targets the pattern
where a developer adds a ``try``/``except`` guard but omits ``RuntimeError``.

Detection
---------
AST-based. For each Python source file in ``src/``:

1. Walk the AST building a "try-nesting stack" that tracks which ``try``
   node is the innermost enclosing block.
2. When a ``.resolve()`` call is found inside a ``try`` body, check
   whether the ``except`` clauses of that innermost ``try`` cover
   ``RuntimeError`` (any of: bare ``except``, ``except RuntimeError``,
   ``except Exception``, ``except BaseException``, or a tuple that
   includes any of these).
3. If not covered, the site is a violation unless it carries the opt-out
   comment on or within two lines above the call.

Allowlisted paths (never flagged):

- ``src/utils/**``   — utility helpers and test-ops scripts
- ``src/cli/path_validation.py`` — the canonical resolve wrapper itself

Opt-out
-------
Add ``# noqa: F11-resolve`` as a trailing comment on the ``.resolve()``
call line, or on a standalone comment line up to two lines above it.
Use this only when the call site is provably safe despite not catching
``RuntimeError`` (e.g. the call is inside a generator consumed by a
broader ``try`` that does catch it). Include a short reason::

    # noqa: F11-resolve — caller's try already covers RuntimeError
    path.resolve()

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

# Allowlisted path prefixes (POSIX, relative to _ROOT).
_ALLOWLISTED_PREFIXES: tuple[str, ...] = ("src/utils/",)
# Allowlisted exact paths (POSIX, relative to _ROOT).
_ALLOWLISTED_EXACT: frozenset[str] = frozenset(
    {
        "src/cli/path_validation.py",
    }
)

# Exception names that cover RuntimeError transitively.
_COVERING_NAMES: frozenset[str] = frozenset({"RuntimeError", "Exception", "BaseException"})

# Opt-out marker. Only real comment tokens are matched (tokenize-based).
_OPT_OUT_RE = re.compile(r"#\s*noqa:\s*F11-resolve\b")

# Lines above the call to scan for the opt-out marker.
_MARKER_WINDOW_ABOVE = 2


# ---------------------------------------------------------------------------
# Exception-handler analysis helpers
# ---------------------------------------------------------------------------


def _exc_type_covers_runtime_error(node: ast.expr) -> bool:
    """True if *node* (an exception type expression) transitively covers ``RuntimeError``.

    Handles:
    - ``RuntimeError`` / ``Exception`` / ``BaseException``
    - Attribute forms: ``builtins.RuntimeError``
    - Tuple forms: ``(ValueError, RuntimeError)``
    """
    if isinstance(node, ast.Name):
        return node.id in _COVERING_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _COVERING_NAMES
    if isinstance(node, ast.Tuple):
        return any(_exc_type_covers_runtime_error(elt) for elt in node.elts)
    return False


def _try_covers_runtime_error(try_node: ast.Try | ast.TryStar) -> bool:
    """True if *try_node*'s except clauses cover ``RuntimeError`` or use bare except."""
    for handler in try_node.handlers:
        if handler.type is None:
            # Bare ``except:`` — covers everything.
            return True
        if _exc_type_covers_runtime_error(handler.type):
            return True
    return False


# ---------------------------------------------------------------------------
# Opt-out marker helpers (tokenize-based, safe against string literals)
# ---------------------------------------------------------------------------


def _collect_marker_comment_lines(source: str) -> set[int]:
    """Return 1-based line numbers of every comment carrying the opt-out marker.

    Uses ``tokenize`` so that string literals containing the marker text
    cannot bypass the rail.
    """
    marker_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and _OPT_OUT_RE.search(tok.string):
                marker_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()
    return marker_lines


def _has_opt_out_in_window(marker_lines: set[int], call_line: int) -> bool:
    """True if any marker line falls within [call_line - ABOVE, call_line]."""
    start = max(1, call_line - _MARKER_WINDOW_ABOVE)
    for lineno in range(start, call_line + 1):
        if lineno in marker_lines:
            return True
    return False


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------


class _ResolveGuardVisitor(ast.NodeVisitor):
    """Walk an AST tracking try-nesting to find unguarded ``.resolve()`` calls."""

    def __init__(self, source: str) -> None:
        self._try_stack: list[ast.Try | ast.TryStar] = []
        self._violations: list[tuple[int, str]] = []
        marker_lines = _collect_marker_comment_lines(source)
        self._marker_lines = marker_lines
        self._source_lines = source.splitlines()

    def visit_Try(self, node: ast.Try | ast.TryStar) -> None:  # type: ignore[override]
        # Push this node so nested calls see it as the innermost enclosing try.
        self._try_stack.append(node)
        # Walk only the protected *body* — handlers/orelse/finalbody are
        # outside the guarded scope of this try.
        for stmt in node.body:
            self.visit(stmt)
        self._try_stack.pop()
        # Now visit handlers, orelse, finalbody without this node on the stack.
        for handler in node.handlers:
            self.visit(handler)
        for stmt in node.orelse:
            self.visit(stmt)
        for stmt in node.finalbody:
            self.visit(stmt)

    # Python 3.11+: handle try/except* the same way as try/except.
    visit_TryStar = visit_Try

    def _visit_function_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators and argument defaults execute in the *enclosing* scope, so
        # they are still protected by the current try-stack.
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        # The function body executes later, outside the enclosing try's scope.
        saved = self._try_stack
        self._try_stack = []
        for stmt in node.body:
            self.visit(stmt)
        self._try_stack = saved

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_def(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_def(node)

    # No visit_ClassDef: class bodies execute immediately in the enclosing scope,
    # so generic_visit (the default) correctly inherits the current try-stack.

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "resolve" and self._try_stack:
            innermost = self._try_stack[-1]
            if not _try_covers_runtime_error(innermost):
                if not _has_opt_out_in_window(self._marker_lines, node.lineno):
                    self._violations.append((node.lineno, self._line_text(node.lineno)))
        self.generic_visit(node)

    def _line_text(self, lineno: int) -> str:
        if 1 <= lineno <= len(self._source_lines):
            return self._source_lines[lineno - 1].rstrip()
        return f"<line {lineno}>"

    @property
    def violations(self) -> list[tuple[int, str]]:
        return sorted(self._violations)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return ``[(lineno, line_text)]`` for every unguarded ``.resolve()`` call.

    Files that fail to parse (invalid Python) yield zero violations.
    Allowlisted files yield zero violations.
    """
    if path.is_relative_to(_ROOT):
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _ALLOWLISTED_EXACT:
            return []
        if any(rel.startswith(prefix) for prefix in _ALLOWLISTED_PREFIXES):
            return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    visitor = _ResolveGuardVisitor(source)
    visitor.visit(tree)
    return visitor.violations


def _iter_src_files() -> list[Path]:
    """Return all ``.py`` files under ``src/``."""
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
        "ERROR (F11-resolve): .resolve() call inside a try block that does not\n"
        "handle RuntimeError. Path.resolve() raises RuntimeError (Python < 3.13)\n"
        "or OSError (Python >= 3.13) on symlink loops. Add both to the except clause:\n\n"
        "    try:\n"
        "        real = entry.resolve()\n"
        "    except (ValueError, RuntimeError, OSError):\n"
        "        ...\n\n"
        "Or, if the site is intentionally not guarded at this level, add:\n"
        "    # noqa: F11-resolve — <reason>\n"
        "on or up to two lines above the .resolve() call.\n",
        file=sys.stderr,
    )
    for rel_path, lineno, line in all_violations:
        print(f"  {rel_path}:{lineno}: {line}", file=sys.stderr)
    print(
        f"\n{len(all_violations)} violation(s) across "
        f"{len({v[0] for v in all_violations})} file(s).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
