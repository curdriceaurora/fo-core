#!/usr/bin/env python3
"""TextIOWrapper-detach rail.

Background
----------
PR #276 review (and subsequent fix) — when a reader accepts a caller-owned
``fileobj=`` parameter and wraps it in ``io.TextIOWrapper(fileobj, ...)``,
the wrapper takes close-ownership of the underlying binary stream by
default. On garbage-collection of the wrapper (return or exception), the
underlying ``fileobj`` is closed as a side-effect, surprising callers that
still expected to read from or close their own stream.

The fix is to call ``.detach()`` on the wrapper before it goes out of
scope, which severs close-ownership.

This rail catches the regression: any function whose signature includes a
``fileobj`` parameter (and is not a test) that constructs
``io.TextIOWrapper(...)`` (or ``TextIOWrapper(...)``) wrapping that
parameter, but doesn't call ``.detach()`` on the wrapper anywhere in the
function body.

Detection
---------
For each ``FunctionDef`` / ``AsyncFunctionDef`` in ``src/``:

1. Find the ``fileobj`` parameter name (any of: ``fileobj``, ``stream``,
   ``f``). Configurable via ``_FILEOBJ_PARAM_NAMES``.
2. Find all ``Assign`` / ``AnnAssign`` whose value is a Call to
   ``TextIOWrapper(...)`` (bare or via ``io.TextIOWrapper``) wrapping the
   fileobj parameter as the first positional argument.
3. For each wrapper assignment, check the function body for a Call to
   ``<wrapper_var>.detach()``. If absent, flag.

Opt-out
-------
``# textiowrapper-detach: ok — <reason>`` on the wrapper-assignment line.

Scope
-----
``src/utils/readers/**/*.py`` (where the fileobj contract was added in
PR3a–PR3i). Easy to widen via ``_SCOPE_DIRS``.

Exit 0 = no violations (or advisory mode).
Exit 1 = violations and not advisory.
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

# Parameter names that suggest a caller-owned binary stream.
_FILEOBJ_PARAM_NAMES: frozenset[str] = frozenset({"fileobj", "stream"})

# The TextIOWrapper-detach pattern is a general anti-pattern, not specific
# to readers: any function in ``src/`` that accepts a caller-owned binary
# stream and wraps it in ``io.TextIOWrapper`` is at risk. Scoping to all
# of ``src/`` keeps the rail honest as the fileobj contract spreads.
_SCOPE_DIRS: tuple[str, ...] = ("src/",)

_NOQA_RE = re.compile(r"#\s*textiowrapper-detach:\s*ok\b")

# Phase 1: advisory. Promote per-file once readers in ``_ENFORCING_FILES``
# are confirmed clean.
_ENFORCING = False
_ENFORCING_FILES: frozenset[str] = frozenset()


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


def _is_textiowrapper_call(node: ast.AST) -> bool:
    """True if *node* is a Call to TextIOWrapper / io.TextIOWrapper."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "TextIOWrapper":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "TextIOWrapper":
        return True
    return False


def _first_positional_name(call: ast.Call) -> str | None:
    """Return the identifier name of the first positional arg, if any."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Name):
        return first.id
    return None


def _function_fileobj_param(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return the name of the fileobj-style parameter, if the function has one."""
    args = func.args
    for arg in list(args.args) + list(args.kwonlyargs) + list(args.posonlyargs):
        if arg.arg in _FILEOBJ_PARAM_NAMES:
            return arg.arg
    return None


_INNER_SCOPE_TYPES: tuple[type[ast.AST], ...] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
)


def _walk_without_inner_scopes(node: ast.AST) -> list[ast.AST]:
    """Walk *node*'s descendants without descending into nested scopes.

    A ``.detach()`` call inside a nested function or class body does not
    execute on the current function's code path. Stopping at scope
    boundaries means we only credit detach calls that actually run.

    Note: the starting *node* is always entered. Nested-scope nodes
    encountered during traversal are visited themselves but their bodies
    are not.
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


def _has_detach_call(body: list[ast.stmt], wrapper_name: str) -> bool:
    """True if *body* contains a call to ``<wrapper_name>.detach()``.

    Only counts detach calls in the current scope — does NOT descend into
    nested functions/classes/lambdas (their bodies don't run on this
    function's code path; crediting a nested unused helper's detach call
    would hide a real violation).
    """
    for stmt in body:
        # A nested-scope statement at the top of the function body
        # (def inner(), class X, lambda) is its own scope; its body does
        # NOT execute as part of the enclosing function. Skip entirely.
        if isinstance(stmt, _INNER_SCOPE_TYPES):
            continue
        for sub in _walk_without_inner_scopes(stmt):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if not isinstance(func, ast.Attribute) or func.attr != "detach":
                continue
            if isinstance(func.value, ast.Name) and func.value.id == wrapper_name:
                return True
    return False


def _wrapper_assignments(
    func: ast.FunctionDef | ast.AsyncFunctionDef, fileobj_name: str
) -> list[tuple[str, int]]:
    """Return [(wrapper_var_name, lineno)] for each TextIOWrapper(fileobj) assignment."""
    out: list[tuple[str, int]] = []
    for stmt in ast.walk(func):
        target_names: list[str] = []
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            value = stmt.value
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    target_names.append(tgt.id)
        elif isinstance(stmt, ast.AnnAssign):
            value = stmt.value
            if isinstance(stmt.target, ast.Name):
                target_names.append(stmt.target.id)
        if value is None or not target_names:
            continue
        if not _is_textiowrapper_call(value):
            continue
        if _first_positional_name(value) != fileobj_name:
            continue
        for name in target_names:
            out.append((name, stmt.lineno))
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
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fileobj = _function_fileobj_param(node)
        if fileobj is None:
            continue
        for wrapper_var, ln in _wrapper_assignments(node, fileobj):
            if _has_detach_call(node.body, wrapper_var):
                continue
            if any(x in noqa_lines for x in range(ln - 2, ln + 7)):
                continue
            if 0 < ln <= len(lines):
                violations.append((ln, lines[ln - 1].rstrip()))

    return sorted(set(violations))


def _file_in_scope(p: Path) -> bool:
    rel = p.relative_to(_ROOT).as_posix()
    return any(rel.startswith(prefix) or rel == prefix for prefix in _SCOPE_DIRS)


def _iter_src_files() -> list[Path]:
    return [p for p in sorted(_SRC_DIR.rglob("*.py")) if _file_in_scope(p)]


def _scan_all() -> list[tuple[Path, int, str]]:
    out: list[tuple[Path, int, str]] = []
    for path in _iter_src_files():
        for lineno, line in find_violations(path):
            out.append((path.relative_to(_ROOT), lineno, line))
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    force_advisory = "--advisory" in args
    force_enforce = "--enforce" in args

    all_violations = _scan_all()

    if not all_violations:
        print("[textiowrapper-detach] 0 site(s).", file=sys.stderr)
        return 0

    print(
        f"[textiowrapper-detach] {len(all_violations)} site(s) across "
        f"{len({v[0] for v in all_violations})} file(s).\n"
        "A function accepting `fileobj=` wraps it in `io.TextIOWrapper(...)` "
        "without calling `.detach()`. The wrapper closes the underlying "
        "stream on GC, breaking callers that still own the stream "
        "(see PR #276 review).\n"
        "Either call `.detach()` before the wrapper goes out of scope, or "
        "mark the line with `# textiowrapper-detach: ok — <reason>`.\n"
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
        print("\nADVISORY mode — exiting 0.", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
