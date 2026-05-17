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
    """Return ``[(line_no, call_name, line_text)]`` for each unexempted flagged call."""
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
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None or name not in _FLAGGED_CALLS:
            continue
        if _has_opt_out_in_window(marker_lines, node.lineno, total):
            continue
        excerpt = lines[node.lineno - 1].rstrip() if 1 <= node.lineno <= total else ""
        out.append((node.lineno, name, excerpt))
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
