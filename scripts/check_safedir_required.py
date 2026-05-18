#!/usr/bin/env python3
"""SafeDir rail — security hardening anchor (tracking: #264).

This rail flags filesystem-access call sites in ``src/`` that read or write
files **from the user-supplied organize root** without going through the
``SafeDir`` primitive (#266). Symlink-following content readers are the
LLM-exfiltration vector documented in #264.

Status — phase 1 (PR1 / #265): **ADVISORY** for the whole tree. The detector
runs, reports counts, and exits 0 regardless. The companion CI test
``tests/ci/test_symlink_safety_lints.py`` records the current baseline.

Subsequent PRs flip directories from ADVISORY → ENFORCING as they migrate:

- PR3 / #267 — read-side (services/text_processor, pipeline/stages,
  utils/readers, services/deduplication/extractor, services/search/
  hybrid_retriever, core/organizer, methodologies/para/detection/heuristics)
- PR4 / #268 — services/deduplication/{hasher, backup}, cli/dedupe_v2
- PR5 / #269 — undo/{durable_move, rollback}, history
- PR6 / #270 — watcher, daemon, pipeline/stages/{writer, postprocessor},
  core/file_ops

When all of ``src/`` is enforcing, this script exits 1 on any violation.

Detection
---------
AST-based. Flags calls whose function is one of ``_FLAGGED_CALLS`` — content
readers and shutil move/copy that follow symlinks by default.

The opt-out marker ``# safedir: ok — <reason>`` works the same as the
atomic-write rail: tokenised comments only (no bypass via string literal),
window of 2 lines above through 6 below.

Compliant call sites are recognised once SafeDir lands (PR2) — calls that
go through ``safe_dir.open_for_reader`` / ``safe_dir.unlink`` etc. are not
flagged because they don't match the surface patterns below.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from collections.abc import Iterator
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _ROOT / "src"


_ALLOWLISTED_FILES = frozenset(
    {
        # The SafeDir primitive itself, once it lands (PR2 / #266).
        "src/utils/safedir.py",
        # Already-hardened FS primitives.
        "src/utils/atomic_write.py",
        "src/utils/atomic_io.py",
        # path_guard owns safe_walk; the symlink filtering is the rail.
        "src/core/path_guard.py",
        # Undo storage paths are app-owned; the trash/durable-move modules
        # have their own dedicated symlink-aware codepaths.
        "src/undo/trash_gc.py",
        "src/undo/durable_move.py",
    }
)


# Function/attribute names that follow symlinks by default and read or move
# user-root content. Detection is by function NAME — the receiver doesn't
# matter, because every library reader on this list takes a path string and
# opens it with the platform default (which follows symlinks).
_FLAGGED_CALLS: frozenset[str] = frozenset(
    {
        # Document readers — both `from X import Y; Y(...)` and `import X; X.Y(...)`
        "fitz.open",
        "docx.Document",
        "Document",
        "openpyxl.load_workbook",
        "load_workbook",
        "pptx.Presentation",
        "Presentation",
        "pypdf.PdfReader",
        "PdfReader",
        # Image
        "Image.open",
        # Archives
        "py7zr.SevenZipFile",
        "SevenZipFile",
        "rarfile.RarFile",
        "RarFile",
        "tarfile.open",
        "zipfile.ZipFile",
        "ZipFile",
        # shutil — copy/move follow symlinks on the source side by default
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copytree",
        "shutil.copystat",
        "shutil.move",
    }
)


_OPT_OUT_RE = re.compile(r"#\s*safedir:\s*ok\s*[-—]\s*\S")
_MARKER_WINDOW_ABOVE = 2
_MARKER_WINDOW_BELOW = 6


# Directories where bare ``open(path, "r"...)`` / ``Path.open("r"...)`` /
# ``io.open(path, "r"...)`` read calls are also flagged (deferred from
# #271 / per CodeRabbit review on PR1). Empty for now — PR3i flips the
# already-migrated reader and dedup dirs into this set with opt-out
# markers on the legitimate post-migration sites. Adding a directory
# here is what turns the bare-open detection on for files inside it.
_READ_OPEN_ENFORCED_DIRS: frozenset[str] = frozenset()

# Read-mode characters. ``open(...)`` defaults to ``"r"`` so a call with
# no mode argument is also a read. ``"r+"`` / ``"w+"`` / ``"a+"`` are
# read-and-write — flagged because they open the underlying file (and
# could still dereference a symlink). The detector flags any mode whose
# letter set intersects ``{"r", "+"}`` and is not a pure write
# (``"w"``/``"a"``/``"x"`` without ``"+"``).
_WRITE_ONLY_MODE_LETTERS = frozenset("wax")

# Stdlib module-style ``.open`` APIs that share the builtin ``open(file, mode)``
# signature — mode is the 2nd positional argument, not the 1st. Without this
# allowlist the rail would parse the *filename* argument as the mode and the
# classification becomes filename-dependent (e.g. ``gzip.open("/tmp/a", "rb")``
# would extract ``"/tmp/a"`` as mode → ``'a'`` letter → mis-classified as
# write-only → false negative). For every other ``<X>.open(...)`` receiver
# (Path, file-object instance methods), mode lives at position 0.
_MODULE_STYLE_OPEN_RECEIVERS = frozenset({"io", "gzip", "bz2", "lzma", "tarfile", "builtins"})


def _is_read_mode(mode_str: str | None) -> bool:
    """Return True if *mode_str* (or default-``"r"`` when None) reads."""
    if mode_str is None:
        return True  # default mode is "r"
    letters = set(mode_str)
    # Pure write/append/exclusive ("w", "wb", "a", "x") never reads.
    if letters & {"r", "+"}:
        return True
    if letters & _WRITE_ONLY_MODE_LETTERS:
        return False
    # Mode like "b" (no r/w/+/a/x) is malformed; treat as read for safety.
    return True


def _mode_arg(node: ast.Call, mode_position: int) -> str | None:
    """Extract the literal mode string from *node*, if statically known.

    Args:
        node: The call AST.
        mode_position: 0-based index where ``mode`` appears positionally
            (``Path.open(mode)`` → 0; ``open(file, mode)`` → 1).

    Returns ``None`` when the mode is omitted (treated as default-read by
    callers) or when it's a non-literal expression we can't reason about
    statically (also treated conservatively as read).
    """
    if len(node.args) > mode_position:
        arg = node.args[mode_position]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        return None  # dynamic — caller treats as read
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            if isinstance(value, str):
                return value
    return None  # no mode argument → default "r"


def _build_module_alias_map(tree: ast.Module) -> dict[str, str]:
    """Walk **module-scope** ``import`` statements and build a local-name →
    real-module map.

    Handles ``import gzip`` → ``{"gzip": "gzip"}`` and the aliased form
    ``import gzip as gz`` → ``{"gz": "gzip"}``.

    Only direct children of ``tree.body`` are considered. Imports nested
    inside a function / class / lambda body bind their names in that
    inner scope, not at module level — including them here would let a
    nested ``import pathlib as gz`` clobber the module-level
    ``import gzip as gz`` mapping and hide real reads. The
    ``_active_aliases_at_call`` resolver applies per-call scope+order
    awareness on top of this base map.

    Dotted imports (``import gzip.partial``) collapse to their top-level
    module name — the receiver in a call like ``gzip.partial.open(...)``
    is the chained attribute, not a bare ``Name``, so the AST branch in
    ``_bare_open_violation`` would not match it anyway.
    """
    aliases: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias_node in stmt.names:
                top_level = alias_node.name.split(".", 1)[0]
                # Python's actual binding rule: ``import x.y`` binds ``x``
                # in the local namespace; ``import x.y as z`` binds ``z``.
                # So the local key is the asname when present, otherwise
                # the top-level (NOT the full dotted name).
                local = alias_node.asname if alias_node.asname else top_level
                aliases[local] = top_level
    return aliases


def _build_parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    """Map child AST node → its parent for the entire tree."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


_SCOPE_BOUNDARY_TYPES: tuple[type[ast.AST], ...] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    # Python 3 scopes comprehension targets to the comprehension itself,
    # NOT the enclosing function or module. Without excluding these, a
    # ``[gz for gz in xs]`` comprehension target would be treated as a
    # rebind of the module-level ``gz`` and incorrectly drop the alias.
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _iter_excluding_nested_scopes(node: ast.AST) -> Iterator[ast.AST]:
    """Yield *node* and its descendants, but stop descending at nested
    scope boundaries (function / async-function / class / lambda /
    list-comp / set-comp / dict-comp / generator-expression). Each of
    these introduces its own name-resolution context that should not
    influence bindings in the enclosing scope.
    """
    yield node
    if isinstance(node, _SCOPE_BOUNDARY_TYPES):
        return
    for child in ast.iter_child_nodes(node):
        yield from _iter_excluding_nested_scopes(child)


def _collect_globally_declared_names(body: list[ast.stmt]) -> set[str]:
    """Walk *body* (excluding nested scopes) and collect names declared
    by ``global`` / ``nonlocal`` statements. Those names are NOT local
    to the enclosing function even if assigned later in the body."""
    declared: set[str] = set()
    for stmt in body:
        for node in _iter_excluding_nested_scopes(stmt):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared.update(node.names)
    return declared


def _add_import_bound_name(
    alias_node: ast.alias,
    target: set[str],
    excluded: set[str],
    *,
    top_level_dot: bool,
) -> None:
    """Append the local name bound by *alias_node* to *target* unless it
    is in *excluded*. ``top_level_dot=True`` collapses dotted imports
    (``import x.y``) to their top-level name."""
    if alias_node.name == "*":
        return
    if alias_node.asname:
        bound = alias_node.asname
    elif top_level_dot:
        bound = alias_node.name.split(".", 1)[0]
    else:
        bound = alias_node.name
    if bound not in excluded:
        target.add(bound)


def _function_local_names(func: ast.AST) -> set[str]:
    """Collect every local name bound by *func* (FunctionDef / AsyncFunctionDef
    / Lambda) — parameters AND any Store-context Name / nested ``Import`` in
    the body, BUT NOT inside further-nested functions/classes/lambdas/
    comprehensions (those have their own scope).

    Names declared ``global`` / ``nonlocal`` are NOT local — assignments
    to them bind in the enclosing scope, not the function's own
    namespace.
    """
    names: set[str] = set()
    args = func.args  # type: ignore[attr-defined]
    for arg_node in (*args.posonlyargs, *args.args, *args.kwonlyargs):
        names.add(arg_node.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    body = func.body if isinstance(func.body, list) else []  # type: ignore[attr-defined]
    declared_non_local = _collect_globally_declared_names(body)
    for stmt in body:
        for node in _iter_excluding_nested_scopes(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                if node.id not in declared_non_local:
                    names.add(node.id)
            elif isinstance(node, ast.Import):
                for alias_node in node.names:
                    _add_import_bound_name(
                        alias_node, names, declared_non_local, top_level_dot=True
                    )
            elif isinstance(node, ast.ImportFrom):
                for alias_node in node.names:
                    _add_import_bound_name(
                        alias_node, names, declared_non_local, top_level_dot=False
                    )
            elif (
                isinstance(node, ast.ExceptHandler)
                and node.name is not None
                and node.name not in declared_non_local
            ):
                # ``except ... as name:`` binds via the handler's name attr.
                names.add(node.name)
            elif (
                isinstance(node, (ast.MatchAs, ast.MatchStar))
                and node.name is not None
                and node.name not in declared_non_local
            ):
                # ``case gz:`` / ``case [x, *gz]`` — pattern capture.
                names.add(node.name)
            elif (
                isinstance(node, ast.MatchMapping)
                and node.rest is not None
                and node.rest not in declared_non_local
            ):
                # ``case {**rest}:`` — captures the unmatched keys.
                names.add(node.rest)
    return names


def _class_local_names(class_def: ast.ClassDef) -> set[str]:
    """Collect every name bound in *class_def*'s body — Name(Store)
    assignments and ``Import`` / ``ImportFrom`` aliases — excluding
    nested function / class / lambda / comprehension scopes.

    Class body has its own namespace at definition time; names bound
    here become class attributes. Calls placed DIRECTLY in the class
    body (not inside a method) resolve the receiver against this
    namespace first.
    """
    names: set[str] = set()
    for stmt in class_def.body:
        for node in _iter_excluding_nested_scopes(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.Import):
                for alias_node in node.names:
                    if alias_node.asname:
                        names.add(alias_node.asname)
                    else:
                        names.add(alias_node.name.split(".", 1)[0])
            elif isinstance(node, ast.ImportFrom):
                for alias_node in node.names:
                    if alias_node.name == "*":
                        continue
                    names.add(alias_node.asname if alias_node.asname else alias_node.name)
    # Class body also binds the class methods/nested classes themselves
    # (since those are not entered by the iterator above).
    for stmt in class_def.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(stmt.name)
    return names


def _top_level_stmt_lineno(
    node: ast.AST, parents: dict[ast.AST, ast.AST], module: ast.Module
) -> int | None:
    """Return the lineno of the top-level (module body) statement that
    contains *node*, or ``None`` if *node* is not under the module body.
    """
    cur = node
    while cur in parents:
        parent = parents[cur]
        if parent is module:
            return cur.lineno
        cur = parent
    return None


def _enclosing_function(call_node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    """Return the immediate enclosing function / async-function / lambda
    for *call_node*, or None if the call is at module / class-body
    scope."""
    cur = call_node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return cur
        if isinstance(cur, ast.Module):
            return None
    return None


def _replay_module_aliases(module: ast.Module, cutoff_lineno: int | None) -> dict[str, str]:
    """Walk module body in source order; build the active alias map.

    Walks every top-level statement with ``lineno <= cutoff_lineno`` (all
    statements if cutoff is None). For each statement:

    - ``ast.Import`` adds/updates aliases (handles same-name re-imports
      in source order — later ``import X as gz`` overwrites earlier
      ``import Y as gz``).
    - ``ast.ImportFrom`` drops any matching alias. ``from M import X as gz``
      binds ``gz`` to something from module M (typically a class /
      function / constant — not a module). Be conservative and drop.
    - ``ast.FunctionDef`` / ``ast.AsyncFunctionDef`` / ``ast.ClassDef``
      at module level rebind the name to a function / class object —
      drop the alias.
    - Any ``ast.Name(Store)`` (excluding nested function / class /
      lambda / comprehension scopes) removes the matching alias.
    """
    active: dict[str, str] = {}
    for stmt in module.body:
        if cutoff_lineno is not None and stmt.lineno > cutoff_lineno:
            break
        _apply_stmt_to_alias_map(stmt, active)
        for node in _iter_excluding_nested_scopes(stmt):
            _drop_alias_for_node_binding(node, active)
    return active


def _apply_stmt_to_alias_map(stmt: ast.stmt, active: dict[str, str]) -> None:
    """Process a top-level module statement: add aliases for Import,
    drop for ImportFrom / FunctionDef / AsyncFunctionDef / ClassDef."""
    if isinstance(stmt, ast.Import):
        for alias_node in stmt.names:
            top_level = alias_node.name.split(".", 1)[0]
            local = alias_node.asname if alias_node.asname else top_level
            active[local] = top_level
    elif isinstance(stmt, ast.ImportFrom):
        for alias_node in stmt.names:
            if alias_node.name == "*":
                continue
            bound = alias_node.asname if alias_node.asname else alias_node.name
            active.pop(bound, None)
    elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        active.pop(stmt.name, None)


def _drop_alias_for_node_binding(node: ast.AST, active: dict[str, str]) -> None:
    """For nested binder nodes (Name(Store), ExceptHandler, MatchAs,
    MatchStar, MatchMapping) that bind a name within the current
    statement's subtree, drop the matching alias.

    Comments:
    - ``Name(Store)``: standard assignment target.
    - ``ExceptHandler``: ``except ... as gz`` binds via the handler's
      ``name`` attribute (raw str). Python deletes the name after the
      handler exits, but we conservatively drop for the rest of the
      file (false negatives are worse than false positives for a
      security rail).
    - ``MatchAs`` / ``MatchStar``: ``case gz`` / ``case [*gz]`` bind
      via the pattern's ``name`` (raw str).
    - ``MatchMapping``: ``case {**rest}`` binds via ``rest`` (raw str).
    """
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        active.pop(node.id, None)
    elif isinstance(node, ast.ExceptHandler) and node.name is not None:
        active.pop(node.name, None)
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
        active.pop(node.name, None)
    elif isinstance(node, ast.MatchMapping) and node.rest is not None:
        active.pop(node.rest, None)


def _drop_function_shadowed(
    active: dict[str, str],
    call_node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> None:
    """Walk enclosing scopes; drop aliases shadowed by each scope's
    local bindings. Mutates *active* in place.

    Python LEGB rule with the class-scope caveat:
    - Function / async-function / lambda: collect locals; continue
      outward through enclosing FUNCTIONS (Python skips intervening
      class scopes when a method looks up a free variable).
    - Class body: applies ONLY when it is the *immediate* enclosing
      scope of the call (e.g. a statement directly in the class body,
      not a call inside a method). Methods inside the class do not see
      the class namespace via LEGB.
    """
    cur = call_node
    is_immediate_scope = True
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.Module):
            return
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            local_names = _function_local_names(cur)
            for name in list(active):
                if name in local_names:
                    del active[name]
            is_immediate_scope = False
        elif isinstance(cur, ast.ClassDef):
            if is_immediate_scope:
                local_names = _class_local_names(cur)
                for name in list(active):
                    if name in local_names:
                        del active[name]
            is_immediate_scope = False


def _active_aliases_at_call(
    call_node: ast.Call,
    base_aliases: dict[str, str],
    parents: dict[ast.AST, ast.AST],
    module: ast.Module,
) -> dict[str, str]:
    """Return the alias map effective at *call_node*'s location.

    Uses a **replay** model rather than build-then-filter:

    1. Determine the cutoff. For calls *inside* a function/lambda, the
       cutoff is end-of-file — by invocation time the module body has
       fully executed, so every module-level binding is visible. For
       module-level calls, the cutoff is the lineno of the call's
       containing top-level statement (inclusive — bindings in the
       statement's header like ``for gz in ...:`` apply to the body).
    2. Walk ``module.body`` in source order. For each statement at
       ``lineno <= cutoff`` (or all statements if cutoff is None):

       - ``ast.Import`` adds / updates aliases (top-level module name,
         keyed by asname or top-level local name). This means a later
         ``import pathlib as gz`` correctly overwrites an earlier
         ``import gzip as gz`` from that line forward, and calls between
         the two see the EARLIER mapping.
       - Any ``ast.Name(Store)`` within the statement (excluding nested
         function / class / lambda scopes, which Python treats as
         separate name-resolution contexts) removes the name from the
         alias map. So ``for gz in items:`` drops ``gz`` for calls inside
         the body, and ``gz = Path(...)`` followed by ``gz.open('wb')``
         drops ``gz`` before the call.

    3. Apply function-scope shadowing on top: walk outward from the call
       to the module. At each enclosing function / lambda, drop any
       alias whose name is locally bound (parameter, ``Name(Store)``,
       or nested ``Import``).

    The replay model naturally handles:

    - Same-line rebinds (``for gz in ...: gz.open(...)`` — the for-target
      and the call share the for-statement's lineno; the target binding
      is collected when walking the statement).
    - Multiple imports of the same alias name (calls between them see
      the earlier mapping; calls after the second see the new mapping).
    - Module-level rebinds AFTER an in-function definition (the function
      call sees the post-rebind value because cutoff is end-of-file).
    """
    if not base_aliases:
        return {}

    # Determine cutoff:
    # - In-function call: cutoff = the immediate enclosing def's lineno.
    #   This is a static heuristic — Python doesn't snapshot module
    #   bindings at def-time, so the call's *runtime* alias depends on
    #   when the function is invoked. Using the def's lineno captures
    #   the "imported, then defined, then called during module init"
    #   pattern (no post-def rebinds visible). Trade-off vs end-of-file
    #   cutoff: choosing the def lineno produces false positives for
    #   functions invoked AFTER a module-level rebind, but avoids false
    #   negatives for functions invoked during module init.
    # - Module-level call: cutoff = the call's top-level statement
    #   lineno (inclusive — so bindings in the statement's header,
    #   like ``for gz in ...:``, apply to the call in the body).
    enclosing_fn = _enclosing_function(call_node, parents)
    if enclosing_fn is not None:
        cutoff_lineno: int | None = enclosing_fn.lineno
    else:
        cutoff_lineno = _top_level_stmt_lineno(call_node, parents, module)
        if cutoff_lineno is None:
            return {}

    active = _replay_module_aliases(module, cutoff_lineno)
    if not active:
        return active
    _drop_function_shadowed(active, call_node, parents)
    return active


def _bare_open_violation(
    node: ast.Call,
    module_aliases: dict[str, str] | None = None,
) -> str | None:
    """Return a synthetic name for a bare-open read, or ``None``.

    Detects:
      - ``open(path, "r"...)``           → ``"open"``
      - ``io.open(path, "r"...)``        → ``"io.open"``
      - ``<X>.open("r"...)`` on Path/file → ``"<receiver>.open"``  (any X
        — the rail can't statically tell SafeDir from Path, so the
        marker is the disambiguator)

    When *module_aliases* is supplied, the receiver in ``<X>.open(...)``
    is resolved through the map before checking ``_MODULE_STYLE_OPEN_RECEIVERS``,
    so ``import gzip as gz`` followed by ``gz.open(p, "rb")`` correctly
    routes to the module-style classification.

    The detector only returns a name; the caller checks
    ``_READ_OPEN_ENFORCED_DIRS`` membership.
    """
    func = node.func
    # `open(...)` builtin — Name node, id == "open"
    if isinstance(func, ast.Name) and func.id == "open":
        if _is_read_mode(_mode_arg(node, mode_position=1)):
            return "open"
        return None
    # ``<receiver>.open(...)`` — Attribute node, attr == "open"
    if isinstance(func, ast.Attribute) and func.attr == "open":
        receiver = func.value
        # Module-style: ``io.open(path, "rb")`` / ``gzip.open(path, "rb")`` /
        # ``bz2.open(...)`` etc. Mode is positional arg 1 (signature mirrors
        # the builtin ``open(file, mode, ...)``). Aliases resolve through
        # *module_aliases* — ``import gzip as gz; gz.open(...)`` reports
        # the canonical name (``gzip.open``) so output is alias-agnostic.
        if isinstance(receiver, ast.Name):
            canonical = (module_aliases or {}).get(receiver.id, receiver.id)
            if canonical in _MODULE_STYLE_OPEN_RECEIVERS:
                if _is_read_mode(_mode_arg(node, mode_position=1)):
                    return f"{canonical}.open"
                return None
        # ``path.open("rb")`` — Path-like / instance method. Mode is
        # positional arg 0 (the receiver doesn't appear in node.args).
        if _is_read_mode(_mode_arg(node, mode_position=0)):
            return "Path.open"
        return None
    return None


def _file_under_enforced_dir(path: Path) -> bool:
    """Return True if *path* is inside a directory in ``_READ_OPEN_ENFORCED_DIRS``."""
    if not _READ_OPEN_ENFORCED_DIRS:
        return False
    try:
        rel = path.resolve().relative_to(_SRC_DIR.resolve().parent).as_posix()
    except (OSError, ValueError):
        return False
    return any(rel == d or rel.startswith(d.rstrip("/") + "/") for d in _READ_OPEN_ENFORCED_DIRS)


def _call_name(node: ast.Call) -> str | None:
    """Return the dotted name of *node*'s callee, or ``None`` if dynamic.

    Examples::

        fitz.open(...)               -> "fitz.open"
        Image.open(...)              -> "Image.open"
        shutil.copy2(...)            -> "shutil.copy2"
        Document(...)                -> "Document"
        Path(p).read_text(...)       -> None     (not a flagged surface)
        getattr(x, "open")(...)      -> None     (dynamic)
    """
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
        return ".".join(reversed(parts))
    return None


def _collect_marker_comment_lines(source: str) -> set[int]:
    """1-based line numbers of every Python comment carrying the opt-out marker."""
    marker_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and _OPT_OUT_RE.search(tok.string):
                marker_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()
    return marker_lines


def _has_opt_out_in_window(marker_lines: set[int], call_line: int, total_lines: int) -> bool:
    start = max(1, call_line - _MARKER_WINDOW_ABOVE)
    end = min(total_lines, call_line + _MARKER_WINDOW_BELOW)
    return any(lineno in marker_lines for lineno in range(start, end + 1))


def find_violations(path: Path) -> list[tuple[int, str, str]]:
    """Return ``[(line_no, call_name, line_text)]`` for each unexempted flagged call.

    Two detection passes:

    1. Library-call surface (``_FLAGGED_CALLS``) — always-on, all dirs.
       Catches ``fitz.open`` / ``Image.open`` / ``zipfile.ZipFile`` etc.
       regardless of which directory the call lives in.
    2. Bare-``open`` reads (``open`` / ``io.open`` / ``Path.open``) —
       only flagged when *path* is inside a directory listed in
       ``_READ_OPEN_ENFORCED_DIRS``. Empty by default for backwards
       compatibility; PR3i populates it for the migrated reader and
       dedup directories. Per-directory scoping keeps the baseline
       count from ballooning when the bare-open class is enabled on
       a fresh subsystem.

    Opt-out markers (``# safedir: ok — <reason>``) apply to both
    passes uniformly.
    """
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
    total = len(lines)
    bare_open_active = _file_under_enforced_dir(path)
    # Pre-compute the base alias map and parent map once per file. The
    # per-call resolver ``_active_aliases_at_call`` combines them with
    # scope + order awareness for each individual call site.
    base_aliases: dict[str, str] = {}
    parents: dict[ast.AST, ast.AST] = {}
    if bare_open_active:
        base_aliases = _build_module_alias_map(tree)
        if base_aliases:
            parents = _build_parent_map(tree)
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Pass 1: library-call surface
        name = _call_name(node)
        if name is not None and name in _FLAGGED_CALLS:
            if _has_opt_out_in_window(marker_lines, node.lineno, total):
                continue
            excerpt = lines[node.lineno - 1].rstrip() if 1 <= node.lineno <= total else ""
            out.append((node.lineno, name, excerpt))
            continue
        # Pass 2: bare-open read (directory-scoped)
        if not bare_open_active:
            continue
        # Resolve aliases active at THIS call's location. Earlier
        # commits used a single file-global alias map and either kept
        # the alias even when a later rebind invalidated it (false
        # positive for write-mode .open) or dropped the alias because
        # of a later/unrelated rebind (false negative for legitimate
        # reads). Per-call resolution avoids both.
        effective_aliases = (
            _active_aliases_at_call(node, base_aliases, parents, tree) if base_aliases else {}
        )
        bare_name = _bare_open_violation(node, effective_aliases)
        if bare_name is None:
            continue
        if _has_opt_out_in_window(marker_lines, node.lineno, total):
            continue
        excerpt = lines[node.lineno - 1].rstrip() if 1 <= node.lineno <= total else ""
        out.append((node.lineno, bare_name, excerpt))
    return out


def scan_tree(src_dir: Path = _SRC_DIR) -> list[tuple[Path, int, str, str]]:
    """Scan every ``*.py`` under *src_dir*; return all violations.

    Symlinks and dot-prefixed paths are filtered before indexing (S1/S2 —
    `search-generation-patterns.md`). A symlinked source file could point at a
    target outside `src/` whose contents shouldn't drive rail decisions; hidden
    directories (`.venv/`, `.tox/`) sometimes appear under `src/` in unusual
    checkouts and would balloon the scan scope.
    """
    out: list[tuple[Path, int, str, str]] = []
    src_root = src_dir.resolve()
    for py in sorted(src_dir.rglob("*.py")):
        try:
            if py.is_symlink():
                continue
        except OSError:
            continue
        try:
            rel_parts = py.resolve().relative_to(src_root).parts
        except (OSError, ValueError):
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue
        rel = py.relative_to(_ROOT).as_posix()
        if rel in _ALLOWLISTED_FILES:
            continue
        for lineno, name, excerpt in find_violations(py):
            out.append((py, lineno, name, excerpt))
    return out


def main(argv: list[str]) -> int:
    advisory = "--advisory" in argv
    violations = scan_tree()
    if not violations:
        return 0
    by_call: dict[str, int] = {}
    for _, _, name, _ in violations:
        by_call[name] = by_call.get(name, 0) + 1
    print(
        f"[safedir-rail] {len(violations)} call site(s) read/move user-root data "
        "without going through SafeDir.",
        file=sys.stderr,
    )
    print("[safedir-rail] Breakdown by callee:", file=sys.stderr)
    for name, count in sorted(by_call.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {count:>4}  {name}", file=sys.stderr)
    if "--verbose" in argv:
        # Metadata only — no source-line excerpts in CI logs (S5,
        # `search-generation-patterns.md`). Path + line + callee is enough
        # to navigate from `gh run view` to the offending site.
        print("[safedir-rail] Call sites:", file=sys.stderr)
        for path, lineno, name, _excerpt in violations:
            rel = path.relative_to(_ROOT).as_posix()
            print(f"  {rel}:{lineno}  {name}", file=sys.stderr)
    if advisory:
        print(
            "[safedir-rail] ADVISORY mode (phase 1 — tracking #264). Exit 0.",
            file=sys.stderr,
        )
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    sys.exit(main(sys.argv[1:]))
