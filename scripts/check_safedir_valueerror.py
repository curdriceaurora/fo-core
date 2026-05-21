#!/usr/bin/env python3
"""SafeDir ValueError rail.

Background
----------
``SafeDir`` (``src/utils/safedir.py``) raises ``ValueError`` from its name
validation step when a path component contains a backslash or other rejected
character (a legal POSIX filename, but disallowed by the SafeDir contract). A
``try:`` block that calls a SafeDir method and catches ``SymlinkRejected`` /
``OSError`` but *not* ``ValueError`` will crash on any user file with a
backslash in its name.

This rail closes the gap that surfaced in PR #307 review (now tracked in
issue #323): ``src/cli/dedupe_v2.py:212`` and
``src/services/deduplication/backup.py:57`` both crashed ``fo dedupe resolve``
because their except clauses caught only ``(SymlinkRejected, OSError)``.

Detection
---------
For every ``ast.Try`` node:

1. Walk the body. If any ``ast.Call`` invokes a SafeDir method
   (``open_for_reader``, ``open_root``, ``open_subdir``, ``open_child``,
   ``pin_inode``, ``rename_into``) OR a SafeDir helper function
   (``safedir_image_open``, ``read_file_via_safedir`` /
   ``read_file_via_safedir_anchored``), the try block is in scope.
2. Compute the union of exception types the handlers catch. If any handler
   is bare (``except:``) or names ``Exception``/``BaseException``/
   ``ValueError``, the try is fine.
3. Otherwise, flag the line of the SafeDir call.

Opt-out
-------
``# safedir-valueerror: ok — <reason>`` on the line of the SafeDir call (or
within 2 lines above / 6 lines below, matching the SafeDir rail convention).

Scope
-----
``src/**/*.py`` only.

Exit 0 = no violations.
Exit 1 = violations found.
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

_SAFEDIR_METHODS: frozenset[str] = frozenset(
    {
        "open_for_reader",
        "open_root",
        "open_subdir",
        "open_child",
        "pin_inode",
        "rename_into",
    }
)

_SAFEDIR_FUNCS: frozenset[str] = frozenset(
    {
        "safedir_image_open",
        "read_file_via_safedir",
        "read_file_via_safedir_anchored",
    }
)

_EXC_SUBSUMING_VALUEERROR: frozenset[str] = frozenset(
    {
        "ValueError",
        "Exception",
        "BaseException",
    }
)

_ALLOWLISTED_FILES: frozenset[str] = frozenset(
    {
        # The SafeDir primitive itself defines the contract — exempt.
        "src/utils/safedir.py",
    }
)

# Phase 1 (advisory): the rail prints warnings but exits 0 so it can land
# without blocking commits. Once the violations documented in issue #323
# are fixed and the count goes to zero, this rail is promoted to enforcing
# (return 1) by setting ``_ENFORCING = True``.
#
# To enforce per-file as fixes land, add the path to ``_ENFORCING_FILES``
# (matches the SafeDir rail's ``_READ_OPEN_ENFORCED_DIRS`` convention).
_ENFORCING = False
_ENFORCING_FILES: frozenset[str] = frozenset()

_NOQA_RE = re.compile(r"#\s*safedir-valueerror:\s*ok\b")


def _collect_noqa_lines(source: str) -> set[int]:
    """Return 1-based line numbers carrying the opt-out token."""
    marker_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and _NOQA_RE.search(tok.string):
                marker_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()
    return marker_lines


def _exception_names(node: ast.expr | None) -> list[str]:
    """Return the set of exception type names referenced by a handler.type AST.

    ``ast.Name(id='ValueError')``        → ``['ValueError']``
    ``ast.Tuple([Name('A'), Name('B')])`` → ``['A', 'B']``
    ``ast.Attribute(_, attr='OSError')`` → ``['OSError']``
    ``None`` (bare ``except:``)          → ``[]`` (caller treats as wildcard)
    """
    if node is None:
        return []
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.Tuple):
        names: list[str] = []
        for el in node.elts:
            names.extend(_exception_names(el))
        return names
    return []


def _try_catches_valueerror(try_node: ast.Try) -> bool:
    """True if any handler in *try_node* is bare or names a ValueError-subsuming type."""
    for handler in try_node.handlers:
        if handler.type is None:
            return True
        for name in _exception_names(handler.type):
            if name in _EXC_SUBSUMING_VALUEERROR:
                return True
    return False


def _safedir_call_lineno(node: ast.AST) -> int | None:
    """Return the line number of *node* if it is a SafeDir call, else None.

    ``safe_dir.open_for_reader(...)`` → method-style on any receiver.
    ``SafeDir.open_root(...)``         → method-style.
    ``safedir_image_open(...)``         → bare-name call.
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _SAFEDIR_METHODS:
        return func.lineno
    if isinstance(func, ast.Name) and func.id in _SAFEDIR_FUNCS:
        return func.lineno
    return None


_INNER_SCOPE_TYPES: tuple[type[ast.AST], ...] = (
    ast.Try,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
)


def _walk_without_inner_scopes(node: ast.AST) -> list[ast.AST]:
    """Walk *node*'s descendants without descending into nested scopes.

    Stops at ``ast.Try`` (so a SafeDir call protected by an *inner*
    try/except that catches ``ValueError`` isn't blamed on the outer try),
    plus function/class/lambda boundaries. The starting *node* is always
    visited; nested-scope nodes encountered during traversal are visited
    themselves but their bodies are not.
    """
    out: list[ast.AST] = []
    stack: list[ast.AST] = [node]
    while stack:
        cur = stack.pop()
        out.append(cur)
        if cur is not node and isinstance(cur, _INNER_SCOPE_TYPES):
            continue
        for child in ast.iter_child_nodes(cur):
            stack.append(child)
    return out


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return [(line_number, snippet)] for each unexempted match in *path*."""
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
    noqa_lines = _collect_noqa_lines(text)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if _try_catches_valueerror(node):
            continue
        # Find SafeDir calls inside the body (not the handlers — bodies only).
        # Don't descend into nested Try / function / class scopes — those
        # have their own except clauses; a SafeDir call protected by an
        # inner try/except that catches ValueError should NOT be blamed on
        # the outer try (the inner Try is independently visited by
        # ``ast.walk(tree)`` above and gets its own check).
        for stmt in node.body:
            # A top-level inner scope inside the outer try body is the
            # scope's responsibility, not the outer try's.
            if isinstance(stmt, _INNER_SCOPE_TYPES):
                continue
            for sub in _walk_without_inner_scopes(stmt):
                call_line = _safedir_call_lineno(sub)
                if call_line is None:
                    continue
                # Opt-out window: line ± a few; we use line and -2..+6.
                if any(ln in noqa_lines for ln in range(call_line - 2, call_line + 7)):
                    continue
                if 0 < call_line <= len(lines):
                    violations.append((call_line, lines[call_line - 1].rstrip()))

    return sorted(set(violations))


def _iter_src_files() -> list[Path]:
    """Yield every ``.py`` file under ``src/`` not in the allowlist."""
    out: list[Path] = []
    for p in sorted(_SRC_DIR.rglob("*.py")):
        rel = p.relative_to(_ROOT).as_posix()
        if rel in _ALLOWLISTED_FILES:
            continue
        out.append(p)
    return out


def _scan_all() -> list[tuple[Path, int, str]]:
    """Run the AST scan across every non-allowlisted ``src/`` file."""
    out: list[tuple[Path, int, str]] = []
    for path in _iter_src_files():
        for lineno, line in find_violations(path):
            out.append((path.relative_to(_ROOT), lineno, line))
    return out


def main(argv: list[str] | None = None) -> int:
    """Scan ``src/`` and print violations.

    Default behaviour follows the SafeDir-rail convention: violations in files
    listed in ``_ENFORCING_FILES`` (or all of ``src/`` if ``_ENFORCING``) fail
    the run; everything else is reported as ADVISORY drift and the run exits
    0. The CI baseline test (``tests/ci/test_safedir_valueerror_rail.py``)
    asserts the advisory count, so any new regression beyond the recorded
    baseline shows up there.

    Flags:
        --advisory   force advisory mode regardless of _ENFORCING_FILES
        --enforce    force enforcing mode (exit 1 on any violation)
    """
    args = argv if argv is not None else sys.argv[1:]
    force_advisory = "--advisory" in args
    force_enforce = "--enforce" in args

    all_violations = _scan_all()

    if not all_violations:
        print("[safedir-valueerror] 0 call site(s).", file=sys.stderr)
        return 0

    print(
        f"[safedir-valueerror] {len(all_violations)} call site(s) across "
        f"{len({v[0] for v in all_violations})} file(s).\n"
        "Breakdown:",
        file=sys.stderr,
    )
    for path, lineno, line in all_violations:
        print(f"  {path}:{lineno}: {line}", file=sys.stderr)

    if force_enforce:
        return 1
    if force_advisory or not _ENFORCING:
        offenders_in_enforced = [v for v in all_violations if v[0].as_posix() in _ENFORCING_FILES]
        if offenders_in_enforced and not force_advisory:
            print(
                f"\n{len(offenders_in_enforced)} violation(s) in enforced files — failing.",
                file=sys.stderr,
            )
            return 1
        print(
            "\nADVISORY mode — exiting 0. "
            "Tracking gap from issue #323; the rail will go enforcing once "
            "the baseline reaches zero.\n"
            "To fix in place: extend the `except` clause to include "
            "`ValueError`, or add `# safedir-valueerror: ok — <reason>`.",
            file=sys.stderr,
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
