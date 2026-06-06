#!/usr/bin/env python3
"""G2-sep rail: flag separator-sensitive POSIX path literals in test data.

Sibling to the G2 hardcoded-paths rail (``scripts/check_test_hardcoded_paths.py``).
G2 blocks ``/tmp/``, ``/Users/``, ``/home/`` literals. This rail targets a
different, separator-sensitive failure mode that G2 misses entirely.

Background
----------
A *multi-segment absolute POSIX string literal* (e.g.
``"/custom/pipx/venvs/fo-core/bin/python"``) assigned to a path-like variable
in a test hardcodes ``/`` separators. When the code under test builds the
comparison side with ``os.path.join(...) + os.sep`` — which yields BACKSLASH
separators on Windows — the forward-slash literal never matches, and the test
fails *only* on the Windows CI runner.

This is the exact root cause fixed in PR #464 (``tests/cli/test_doctor.py``
``test_detect_pipx_via_pipx_home_env``: ``fake_exe = "/custom/.../python"`` and
``custom_home = "/custom/pipx"``). Because the Windows runner only runs in the
nightly ``CI Full Matrix`` — not on PRs — this class of breakage isn't caught
at PR time; it surfaces a day later in the nightly. Hence this rail.

Detection (AST-based, ``tests/`` only)
--------------------------------------
For each ``<name> = "<literal>"`` (or annotated ``<name>: T = "<literal>"``)
assignment where BOTH hold:

1. ``<literal>`` is a separator-sensitive absolute POSIX path — it starts with
   ``/``, has at least two non-empty ``/``-separated segments, is not a URL
   (contains ``://``), and is not a documented adversarial input
   (``/etc/passwd`` etc., mirrored from G2). f-strings are handled via their
   static skeleton (``f"/custom/pipx/venvs/{name}/bin"`` counts) and constant
   string concatenation is folded (``"/custom" + "/pipx/bin"`` counts), since
   the hardcoded ``/`` prefix carries the same hazard; AND
2. ``<name>`` is path-like — one of its ``_``-split components is in
   ``_PATHISH_WORDS`` (``exe``, ``path``, ``dir``, ``home`` …). These are the
   variables that get fed into path comparisons.

Flag the assignment. The fix is to build the value with ``os.path.join`` /
``os.sep`` (or use the ``tmp_path`` fixture) so it matches the platform
separators the code under test uses.

Opt-out
-------
``# g2sep: ok — <reason>`` on the assignment line — for the rare case where a
Linux-only absolute path is genuinely intended (e.g. an adversarial input not
covered by the built-in list). This dedicated token (rather than reusing ruff's
suppression namespace) matches the other advisory rails in this repo.

Lifecycle
---------
Phase 1: advisory. The pre-commit hook passes ``--advisory`` (exits 0 even on
violation); ``tests/ci/test_g2_separator_paths_rail.py`` pins the baseline at 0
so any regression fails CI via the baseline test. Promote to enforcing by
dropping ``--advisory`` once the baseline has held at 0.

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

# Project root — the script lives at ``scripts/check_test_separator_paths.py``.
_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _ROOT / "tests"

# A separator-sensitive absolute POSIX literal: starts with ``/`` and has at
# least two non-empty ``/``-separated segments. ``/tmp`` (single segment) does
# not match — that bare form is G2's job; we target multi-segment paths whose
# embedded separators are the portability hazard.
_SEP_RE = re.compile(r"^/[^/\s]+/[^/\s]+")

# Adversarial inputs that are legitimately hardcoded as path-validation test
# inputs (T13 allowance). Mirrors ``_ADVERSARIAL_INPUTS`` in the G2 detector.
# Substring match: any literal containing one of these is exempt.
_ADVERSARIAL_INPUTS = (
    "/etc/passwd",
    "/etc/shadow",
    "/proc/self/mem",
    "/root/",
    "/dev/null",
    "/dev/zero",
)

# ``_``-split name components that mark a variable as path-like. A literal is
# only flagged when assigned to a variable whose snake_case components include
# one of these — keeping the rail focused on values that feed path comparisons
# and away from URL fragments, dict keys, and other incidental ``/`` strings.
_PATHISH_WORDS = frozenset(
    {
        "exe",
        "executable",
        "path",
        "dir",
        "home",
        "venv",
        "venvs",
        "bin",
        "root",
        "file",
    }
)

# Allow an in-line ``# g2sep: ok — <reason>`` opt-out. A dedicated token
# (rather than reusing ruff's suppression namespace) keeps this rail independent
# of G2 and matches the convention used by the other advisory rails in this repo.
_OPT_OUT_RE = re.compile(r"#\s*g2sep:\s*ok\b")

# Phase 1: advisory. Flip to ``True`` (and drop ``--advisory`` from the
# pre-commit hook) to promote to globally enforcing.
_ENFORCING = False


def is_separator_sensitive(value: str) -> bool:
    """True if *value* is a multi-segment absolute POSIX path literal.

    Excludes URLs (``scheme://host/path``) and documented adversarial inputs.
    """
    if "://" in value:  # URL, not a filesystem path
        return False
    if not _SEP_RE.match(value):
        return False
    if any(marker in value for marker in _ADVERSARIAL_INPUTS):
        return False
    return True


def is_pathish_name(name: str) -> bool:
    """True if *name*'s snake_case components include a path-like word."""
    return any(part in _PATHISH_WORDS for part in name.lower().split("_"))


def _has_opt_out(line: str) -> bool:
    """True if *line* carries the ``# g2sep: ok`` opt-out token."""
    return bool(_OPT_OUT_RE.search(line))


def _collect_opt_out_lines(source: str) -> set[int]:
    """Return 1-based line numbers carrying the ``# g2sep: ok`` token."""
    marker_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and _OPT_OUT_RE.search(tok.string):
                marker_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()
    return marker_lines


# Placeholder substituted for an f-string ``{...}`` interpolation when building
# the static path skeleton — a non-slash, non-space sentinel so the interpolation
# reads as a single path segment for separator-sensitivity matching.
_FSTRING_PLACEHOLDER = "\x00"


def _literal_skeleton(value: ast.expr) -> str | None:
    """Return the static string skeleton of *value*, or None if not a str literal.

    For a plain ``ast.Constant`` str this is the value itself. For an f-string
    (``ast.JoinedStr``) the literal parts are kept verbatim and each ``{...}``
    interpolation is replaced by a single-segment placeholder, so a hardcoded
    absolute prefix like ``f"/custom/pipx/venvs/{name}/bin/python"`` is still
    caught — it has the same Windows separator hazard as the plain literal.

    String concatenation (``"/custom" + "/pipx/bin"``) is folded recursively. A
    *dynamic* operand (a ``Name``, call, etc.) contributes a single-segment
    placeholder — the same treatment as an f-string ``{...}`` interpolation — so
    a hardcoded absolute prefix survives a dynamic suffix:
    ``"/custom/pipx/venvs/" + name + "/bin/python"`` reduces to
    ``"/custom/pipx/venvs/<x>/bin/python"`` and is still caught. A concatenation
    with no static part at all (``a + b``) returns None. Because the leading
    placeholder fails the ``^/`` anchor, ``var + "/bin"`` (dynamic prefix) is
    still not flagged.
    """
    if isinstance(value, ast.Constant):
        return value.value if isinstance(value.value, str) else None
    if isinstance(value, ast.JoinedStr):
        parts: list[str] = []
        for part in value.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            else:  # FormattedValue (a `{...}` interpolation) or nested JoinedStr
                parts.append(_FSTRING_PLACEHOLDER)
        return "".join(parts)
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        left = _literal_skeleton(value.left)
        right = _literal_skeleton(value.right)
        if left is None and right is None:
            return None  # no static content — not a hardcoded path
        left_s = left if left is not None else _FSTRING_PLACEHOLDER
        right_s = right if right is not None else _FSTRING_PLACEHOLDER
        return left_s + right_s
    return None


def _assign_targets(node: ast.AST) -> list[tuple[str, ast.expr]]:
    """Return [(target_name, value)] for a simple Assign / AnnAssign node."""
    out: list[tuple[str, ast.expr]] = []
    if isinstance(node, ast.Assign):
        if node.value is None:
            return out
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out.append((tgt.id, node.value))
    elif isinstance(node, ast.AnnAssign):
        if node.value is not None and isinstance(node.target, ast.Name):
            out.append((node.target.id, node.value))
    return out


def find_violations(path: Path) -> list[tuple[int, str, str]]:
    """Return ``[(lineno, var_name, literal)]`` for each unexempted match."""
    violations: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return violations

    opt_out_lines = _collect_opt_out_lines(text)

    for node in ast.walk(tree):
        for name, value in _assign_targets(node):
            skeleton = _literal_skeleton(value)
            if skeleton is None:
                continue
            if not is_pathish_name(name):
                continue
            if not is_separator_sensitive(skeleton):
                continue
            lineno = getattr(node, "lineno", value.lineno)
            # Honour an opt-out marker anywhere across the assignment's span.
            end = getattr(node, "end_lineno", lineno) or lineno
            if any(ln in opt_out_lines for ln in range(lineno, end + 1)):
                continue
            # Render f-string interpolations readably in the reported literal.
            violations.append((lineno, name, skeleton.replace(_FSTRING_PLACEHOLDER, "{...}")))
    return sorted(set(violations))


def _iter_test_files() -> list[Path]:
    """Return every ``.py`` file under ``tests/``, sorted for stable output."""
    return sorted(_TESTS_DIR.rglob("*.py"))


def _scan_all() -> list[tuple[Path, int, str, str]]:
    """Scan all test files and return ``(rel_path, lineno, var, literal)`` hits."""
    out: list[tuple[Path, int, str, str]] = []
    for path in _iter_test_files():
        for lineno, name, literal in find_violations(path):
            out.append((path.relative_to(_ROOT), lineno, name, literal))
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Exit 0 when clean or advisory; 1 on violation if enforcing."""
    args = argv if argv is not None else sys.argv[1:]
    force_advisory = "--advisory" in args

    all_violations = _scan_all()

    if not all_violations:
        print("[g2sep] 0 separator-sensitive path literal(s).", file=sys.stderr)
        return 0

    print(
        f"[g2sep] {len(all_violations)} separator-sensitive path literal(s) "
        f"across {len({v[0] for v in all_violations})} file(s).\n"
        "A multi-segment absolute POSIX literal is assigned to a path-like "
        "variable. Hardcoded '/' separators don't match os.path.join(...) + "
        "os.sep on Windows, so the test fails only on the Windows CI runner "
        "(see PR #464).\n"
        "Build the value with os.path.join / os.sep (or use tmp_path), or mark "
        "the line with `# g2sep: ok — <reason>` if a Linux-only path is "
        "genuinely intended.\n"
        "Breakdown:",
        file=sys.stderr,
    )
    for path, lineno, name, literal in all_violations:
        print(f"  {path}:{lineno}: {name} = {literal!r}", file=sys.stderr)

    if force_advisory or not _ENFORCING:
        print("\nADVISORY mode — exiting 0.", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
