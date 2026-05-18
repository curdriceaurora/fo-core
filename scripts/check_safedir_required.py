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


def _bare_open_violation(node: ast.Call) -> str | None:
    """Return a synthetic name for a bare-open read, or ``None``.

    Detects:
      - ``open(path, "r"...)``           → ``"open"``
      - ``io.open(path, "r"...)``        → ``"io.open"``
      - ``<X>.open("r"...)`` on Path/file → ``"<receiver>.open"``  (any X
        — the rail can't statically tell SafeDir from Path, so the
        marker is the disambiguator)

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
        # io.open(path, "rb") — receiver is Name "io"
        if isinstance(func.value, ast.Name) and func.value.id == "io":
            if _is_read_mode(_mode_arg(node, mode_position=1)):
                return "io.open"
            return None
        # ``path.open("rb")`` — any other receiver. Mode is positional
        # arg 0 (the receiver doesn't appear in node.args).
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
        bare_name = _bare_open_violation(node)
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
