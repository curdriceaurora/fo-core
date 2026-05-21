#!/usr/bin/env python3
"""defusedxml-fallback rail.

Background
----------
The dedup ODT extractor (``src/services/deduplication/extractor.py``) imports
``defusedxml.ElementTree`` for safe XML parsing — but PR #307 review flagged
that on ``ImportError`` it silently falls back to the stdlib
``xml.etree.ElementTree``, which is vulnerable to billion-laughs / XXE /
external-DTD attacks. ``defusedxml`` is declared in ``pyproject.toml`` so the
fallback only triggers in broken environments — but those environments lose
the security guarantee silently.

This rail catches that pattern: a ``try:`` block that imports anything from
``defusedxml`` paired with an ``except ImportError:`` (or bare except) whose
body imports from ``xml.``/``xml.etree``. Fix by failing closed — either
hard-require defusedxml at module import, or raise a configuration error if
the user invokes the XML-using code path without it.

Detection
---------
AST-based. For every ``ast.Try``:

1. Body contains any of:
   - ``import defusedxml`` / ``import defusedxml.X``
   - ``from defusedxml import X`` / ``from defusedxml.X import Y``
2. At least one handler catches ``ImportError`` (or is bare).
3. That handler's body contains any of:
   - ``import xml.etree.X`` / ``import xml.X``
   - ``from xml.etree...import X`` / ``from xml import X``

If all three hold → flag the line of the defusedxml import.

Opt-out
-------
``# defusedxml-fallback: ok — <reason>`` on the defusedxml import line.

Scope
-----
``src/**/*.py`` only.

Exit 0 = no violations (or advisory mode).
Exit 1 = violations and not advisory.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from collections.abc import Callable
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _ROOT / "src"

_NOQA_RE = re.compile(r"#\s*defusedxml-fallback:\s*ok\b")

# Phase 1: advisory. Promote to enforcing once the one known site
# (extractor.py:28-33) is fixed under issue #323.
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


def _imports_in(stmts: list[ast.stmt]) -> list[tuple[int, str]]:
    """Return [(lineno, module_name)] of imports in *stmts*.

    Handles both ``import X.Y`` and ``from X.Y import Z`` forms.
    """
    out: list[tuple[int, str]] = []
    for stmt in stmts:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                out.append((stmt.lineno, alias.name))
        elif isinstance(stmt, ast.ImportFrom) and stmt.module is not None:
            out.append((stmt.lineno, stmt.module))
    return out


def _is_module_prefix(mod: str, prefix: str) -> bool:
    """True if *mod* equals *prefix* or starts with ``prefix.``."""
    return mod == prefix or mod.startswith(prefix + ".")


def _handler_catches_importerror(handler: ast.ExceptHandler) -> bool:
    """True if the handler is bare or names ImportError/ModuleNotFoundError/Exception."""
    if handler.type is None:
        return True
    names: list[str] = []
    node = handler.type
    if isinstance(node, ast.Name):
        names.append(node.id)
    elif isinstance(node, ast.Tuple):
        for el in node.elts:
            if isinstance(el, ast.Name):
                names.append(el.id)
            elif isinstance(el, ast.Attribute):
                names.append(el.attr)
    elif isinstance(node, ast.Attribute):
        names.append(node.attr)
    return any(
        n in {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"} for n in names
    )


def _handler_reraises(handler: ast.ExceptHandler) -> bool:
    """True if the handler unconditionally re-raises (top-level ``raise``)."""
    for stmt in handler.body:
        if isinstance(stmt, ast.Raise):
            return True
    return False


def _is_xml_module(mod: str) -> bool:
    """True if *mod* is the stdlib ``xml`` package or a sub-module."""
    return mod == "xml" or mod.startswith("xml.")


def _scope_xml_imports_ordered(nodes: list[ast.stmt]) -> list[tuple[int, str]]:
    """Yield ``(lineno, name)`` pairs for xml import aliases at this scope.

    Returned in source order. Recurses into compound statements
    (``if``/``try``/``with``/``for``/``while``) at the same scope level
    but NOT into nested function/class bodies — those have their own
    scope. Each pair represents a name bound by an xml import; for a
    Try at this scope, the names visible at the Try's line are the ones
    whose lineno is strictly less than the Try's.

    Handles both import forms:
        import xml.etree.ElementTree as _stdlib_ET   → "_stdlib_ET"
        import xml.etree.ElementTree                 → "xml"
        from xml.etree import ElementTree as _ET     → "_ET"
        from xml.etree.ElementTree import parse      → "parse"
    """
    out: list[tuple[int, str]] = []

    def _collect(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if _is_xml_module(alias.name):
                        out.append((stmt.lineno, alias.asname or alias.name.split(".")[0]))
            elif isinstance(stmt, ast.ImportFrom) and stmt.module is not None:
                if _is_xml_module(stmt.module):
                    for alias in stmt.names:
                        out.append((stmt.lineno, alias.asname or alias.name))
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Nested scope — its imports aren't bound at this level.
                continue
            else:
                # Recurse into compound statements at the same scope level.
                for _field, value in ast.iter_fields(stmt):
                    if isinstance(value, list):
                        sub = [x for x in value if isinstance(x, ast.stmt)]
                        if sub:
                            _collect(sub)

    _collect(nodes)
    return out


def _collect_scope_xml_names(nodes: list[ast.stmt]) -> set[str]:
    """Convenience: full set of xml aliases at this scope, ignoring order.

    Used as the outer-scope alias set when recursing into nested function
    bodies — function bodies use late binding, so they see every name
    bound in the enclosing scope at call time, regardless of source order.
    """
    return {name for _, name in _scope_xml_imports_ordered(nodes)}


def _handler_references_any(handler: ast.ExceptHandler, names: set[str]) -> bool:
    """True if the except-body *reads* any identifier in *names*.

    Only ``ast.Load`` contexts count — a handler that merely *writes* to
    the stdlib xml name (e.g. ``except ImportError: parse = None``) is not
    a real bridge to stdlib parsing; it's clearing the binding. The
    bridge requires that the handler *uses* the stdlib value to fulfil
    the defusedxml alias.
    """
    for stmt in handler.body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and sub.id in names and isinstance(sub.ctx, ast.Load):
                return True
    return False


def _try_triggers_fallback(node: ast.Try, visible_xml_names: set[str]) -> bool:
    """True if the Try block silently bridges defusedxml→stdlib xml.

    *visible_xml_names* is the LEGB-resolved set of stdlib ``xml.*``
    aliases visible from the scope containing the Try — module scope plus
    every enclosing function/class scope. This is the set the except
    handler can legitimately read to bridge to stdlib parsing.
    """
    body_imports = _imports_in(node.body)
    has_defusedxml = any(_is_module_prefix(mod, "defusedxml") for _, mod in body_imports)
    if not has_defusedxml:
        return False
    for handler in node.handlers:
        if not _handler_catches_importerror(handler):
            continue
        if _handler_reraises(handler):
            # Fails closed — caller gets ImportError, not silent stdlib.
            continue
        handler_imports = _imports_in(handler.body)
        if any(_is_xml_module(mod) for _, mod in handler_imports):
            # Variant 1: stdlib re-import inside except.
            return True
        # Variant 2: except body actually reads (ast.Load) a stdlib xml
        # alias visible in the Try's lexical scope (LEGB). Pure writes
        # don't count (handler that does `parse = None` is clearing the
        # binding, not bridging).
        if visible_xml_names and _handler_references_any(handler, visible_xml_names):
            return True
    return False


def _emit_violations(
    try_node: ast.Try,
    lines: list[str],
    noqa_lines: set[int],
    violations: list[tuple[int, str]],
) -> None:
    """Append one violation row per defusedxml import line in *try_node*."""
    for ln, mod in _imports_in(try_node.body):
        if not _is_module_prefix(mod, "defusedxml"):
            continue
        if any(x in noqa_lines for x in range(ln - 2, ln + 7)):
            continue
        if 0 < ln <= len(lines):
            violations.append((ln, lines[ln - 1].rstrip()))


def _recurse_compound(stmt: ast.stmt, visit: Callable[[ast.stmt], None]) -> None:
    """Recurse ``visit`` into every statement-list child field of *stmt*.

    For if/for/while/with/async variants — preserves scope.
    """
    for _field, value in ast.iter_fields(stmt):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.stmt):
                    visit(item)


def _scan_scope(
    nodes: list[ast.stmt],
    outer_xml_names: set[str],
    lines: list[str],
    noqa_lines: set[int],
    violations: list[tuple[int, str]],
) -> None:
    """Walk *nodes* with LEGB-resolved xml-alias set *outer_xml_names*.

    A Try at this scope is checked against ``outer_xml_names`` plus the
    xml imports at THIS scope whose source line is strictly before the
    Try's line — this respects statement order, so a Try whose only
    bridge alias is imported later in the file is correctly recognised
    as a runtime NameError (fail-closed) rather than a silent fallback.

    Functions/classes inside this scope are recursed with the FULL local
    xml alias set as their outer — because Python uses late binding,
    function bodies see every name in their enclosing scope at call
    time, regardless of source order.
    """
    ordered_imports = _scope_xml_imports_ordered(nodes)
    full_local_xml = outer_xml_names | {name for _, name in ordered_imports}

    def _visible_at(lineno: int) -> set[str]:
        return outer_xml_names | {name for ln, name in ordered_imports if ln < lineno}

    def _visit(stmt: ast.stmt) -> None:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Late binding: function/class body sees the full outer set.
            _scan_scope(stmt.body, full_local_xml, lines, noqa_lines, violations)
            return
        if isinstance(stmt, ast.Try):
            visible = _visible_at(stmt.lineno)
            if _try_triggers_fallback(stmt, visible):
                _emit_violations(stmt, lines, noqa_lines, violations)
            # Recurse into the Try's body / handlers / orelse / finally —
            # nested try/function/class blocks still need their own scan.
            for s in stmt.body:
                _visit(s)
            for h in stmt.handlers:
                for s in h.body:
                    _visit(s)
            for s in stmt.orelse:
                _visit(s)
            for s in stmt.finalbody:
                _visit(s)
            return
        _recurse_compound(stmt, _visit)

    for stmt in nodes:
        _visit(stmt)


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return [(line_number, snippet)] for each unexempted match in *path*.

    Flags two pattern variants — both leave the caller with stdlib XML
    parsing after a defusedxml import failure:

    1. ``try: import defusedxml.X except ImportError: import xml.Y as Z``
       (re-import the stdlib name into the same scope).
    2. ``try: import defusedxml.X except ImportError: _ET = _stdlib_ET``
       (assign-from-prior-stdlib-import in the except body, with the
       stdlib import sitting outside the try at module scope).

    The rule of thumb: a ``try: import defusedxml`` whose handler doesn't
    re-raise is suspicious. The handler MUST either propagate the error
    or refuse to provide a fallback.
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
    noqa_lines = _collect_noqa_lines(text)
    _scan_scope(tree.body, set(), lines, noqa_lines, violations)
    return sorted(set(violations))


def _iter_src_files() -> list[Path]:
    return sorted(_SRC_DIR.rglob("*.py"))


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
        print("[defusedxml-fallback] 0 site(s).", file=sys.stderr)
        return 0

    print(
        f"[defusedxml-fallback] {len(all_violations)} site(s) across "
        f"{len({v[0] for v in all_violations})} file(s).\n"
        "A try/except-ImportError fallback from defusedxml to stdlib xml "
        "re-enables billion-laughs / XXE / external-DTD attacks silently.\n"
        "Either hard-require defusedxml at module import (let ImportError "
        "propagate) or raise a configuration error when the XML codepath "
        "is invoked without it.\n"
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
            "\nADVISORY mode — exiting 0. Tracking issue #323.",
            file=sys.stderr,
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
