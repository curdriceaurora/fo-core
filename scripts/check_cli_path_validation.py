#!/usr/bin/env python3
"""G3 rail: CLI commands that accept a ``Path`` argument must route it
through ``cli.path_validation.resolve_cli_path`` before any use.

Epic A.cli (hardening roadmap #154, §2.5) wired every path-taking CLI
command through that helper so user input can't skip the
expand-user/resolve/existence-check boundary. G3 prevents regression:
any new or edited ``@<app>.command()`` function that binds a ``Path``
parameter must call ``resolve_cli_path(<param>, ...)`` (or explicitly
opt out with ``# noqa: G3 (reason)``).

**Scope**: ``src/cli/**/*.py``. Other files are not CLI entry points.

**Pattern**:

- Function is decorated with ``@<x>.command(...)``.
- Function has a parameter whose annotation is ``Path``, ``Path | None``,
  or ``Optional[Path]``.
- Function body calls ``resolve_cli_path(<param_name>, ...)`` OR calls
  ``validate_pair(<param>, <other>)`` for coherence-checked pairs.

**Exemptions** (line-level ``# noqa: G3`` on the ``def`` line, or
param-level via a comment):

- Callback functions that accept a path but delegate to another helper
  that itself calls ``resolve_cli_path`` — opt out by adding
  ``# noqa: G3 (delegates to <helper>)``.
- Commands that accept a path purely as display data (e.g. ``fo config
  show --file PATH`` where ``PATH`` is printed, not opened) — opt out
  with ``# noqa: G3 (display-only)``.

Exit 0 = clean. Exit 1 = violations.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CLI_DIR = _ROOT / "src" / "cli"

_NOQA_G3_RE = re.compile(r"#\s*noqa:\s*G3\b")

_PATH_ANNOTATION_NAMES = frozenset({"Path", "PosixPath", "WindowsPath"})
_ALLOWED_VALIDATORS = frozenset({"resolve_cli_path", "validate_pair", "validate_within_roots"})

# Helper functions named ``_validate_*`` by convention delegate to one of
# the above. Treat calls to them as validator invocations so the rail
# doesn't require duplicating the validation call in every command body
# (see e.g. ``src/cli/benchmark.py::_validate_compare_path``).
_VALIDATOR_HELPER_PREFIX = "_validate_"


def _is_path_annotation(node: ast.expr | None) -> bool:
    """True if ``node`` is an annotation that includes ``Path``.

    Handles bare ``Path``, ``Path | None``, ``Optional[Path]``, and
    ``list[Path]`` (caller decides whether list-of-paths counts).
    """
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in _PATH_ANNOTATION_NAMES
    if isinstance(node, ast.Attribute):
        # ``pathlib.Path`` form
        return node.attr in _PATH_ANNOTATION_NAMES
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # Union form: ``Path | None``, ``Path | str``, etc.
        return _is_path_annotation(node.left) or _is_path_annotation(node.right)
    if isinstance(node, ast.Subscript):
        # ``Optional[Path]``, ``list[Path]`` — recurse into the slice.
        return _is_path_annotation(node.slice)
    return False


def _is_command_decorator(dec: ast.expr) -> bool:
    """True if ``dec`` looks like ``@<something>.command(...)``."""
    # @foo.command()
    if isinstance(dec, ast.Call):
        func = dec.func
    else:
        func = dec
    if isinstance(func, ast.Attribute) and func.attr == "command":
        return True
    # @app.command — bare attribute (no parens)
    return False


def _has_noqa_g3(line: str) -> bool:
    return bool(_NOQA_G3_RE.search(line))


def _collect_validator_call_targets(body: list[ast.stmt]) -> set[str]:
    """Return the set of argument names passed positionally to any
    allowed validator call anywhere in ``body``.

    Handles nested function bodies too (a validator call inside a
    helper defined alongside the main logic still counts — the name
    is validated somewhere in the function's scope).
    """
    targets: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            name: str | None = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            # Direct validator call OR a ``_validate_*`` helper. Both
            # count as "the parameter was routed through a validator."
            if name in _ALLOWED_VALIDATORS or (
                name is not None and name.startswith(_VALIDATOR_HELPER_PREFIX)
            ):
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        targets.add(arg.id)
            self.generic_visit(node)

    visitor = _Visitor()
    for stmt in body:
        visitor.visit(stmt)
    return targets


def _iter_cli_command_functions(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield every ``@<x>.command(...)``-decorated function in ``tree``."""
    commands: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if _is_command_decorator(dec):
                commands.append(node)
                break
    return commands


def find_violations(path: Path) -> list[tuple[int, str, str]]:
    """Return [(lineno, func_name, param_name)] for every CLI command
    whose ``Path`` parameter is not passed to an allowed validator."""
    violations: list[tuple[int, str, str]] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return violations

    source_lines = source.splitlines()

    for func in _iter_cli_command_functions(tree):
        validated = _collect_validator_call_targets(func.body)

        # Check the ``def`` line (and the line right before, where the
        # decorator lives) for a ``# noqa: G3`` marker.
        def_line_idx = func.lineno - 1
        noqa_window_start = max(0, def_line_idx - len(func.decorator_list) - 1)
        noqa_window_end = min(len(source_lines), def_line_idx + 1)
        function_has_noqa = any(
            _has_noqa_g3(source_lines[i]) for i in range(noqa_window_start, noqa_window_end)
        )
        if function_has_noqa:
            continue

        all_args = [*func.args.args, *func.args.kwonlyargs]
        for arg in all_args:
            if not _is_path_annotation(arg.annotation):
                continue
            # Skip params whose annotation is a list-of-paths — those
            # are bulk inputs and are validated per-element inside the
            # function body (harder to statically check).
            if isinstance(arg.annotation, ast.Subscript):
                slice_node = arg.annotation.slice
                if isinstance(slice_node, ast.Name) and slice_node.id == "Path":
                    # e.g. ``list[Path]``
                    continue
            # Per-line noqa: a ``# noqa: G3`` on the line that defines
            # the parameter exempts it.
            if 0 < arg.lineno <= len(source_lines):
                if _has_noqa_g3(source_lines[arg.lineno - 1]):
                    continue
            if arg.arg not in validated:
                violations.append((func.lineno, func.name, arg.arg))

    return violations


def main() -> int:
    all_violations: list[tuple[Path, int, str, str]] = []
    for path in sorted(_CLI_DIR.rglob("*.py")):
        if path.name.startswith("_"):
            continue  # skip __init__, __main__
        for lineno, func_name, param_name in find_violations(path):
            all_violations.append((path.relative_to(_ROOT), lineno, func_name, param_name))

    if not all_violations:
        return 0

    print(
        "ERROR (G3): CLI command(s) accept a Path parameter without routing "
        "it through `resolve_cli_path()` (or `validate_pair` / "
        "`validate_within_roots`).\n"
        "Add the validator call near the top of the function body, OR annotate "
        "the `def` with `# noqa: G3 (reason)` if the Path is not user-supplied.\n",
        file=sys.stderr,
    )
    for rel, lineno, func_name, param_name in all_violations:
        print(
            f"  {rel}:{lineno}: {func_name}() has Path parameter "
            f"'{param_name}' not passed to any allowed validator",
            file=sys.stderr,
        )
    print(
        f"\n{len(all_violations)} violation(s) across "
        f"{len({v[0] for v in all_violations})} file(s).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
