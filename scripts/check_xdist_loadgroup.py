#!/usr/bin/env python3
"""G5 rail: any pytest invocation using ``-n auto`` must also use
``--dist=loadgroup``.

Without ``--dist=loadgroup``, ``@pytest.mark.xdist_group`` is parsed but
provides NO serialization guarantee for tests that share a module-level
singleton or other cross-test state. Missing the flag is a silent
correctness bug — tests may pass locally (single worker) and flake on CI
where xdist spreads work across multiple workers (see
``.claude/rules/xdist-safe-patterns.md`` Pattern 3).

This script scans YAML (CI configs, pre-commit), shell, Makefile, and
Python files for ``-n auto`` / ``-n=auto`` / ``-n"auto"`` pytest
invocations. For each file, every command that contains ``-n auto`` must
also contain ``--dist=loadgroup`` within a 5-line window following the
``-n`` flag (accounting for backslash line continuations).

Scan scope (commands only matter inside a pytest invocation):

- ``.github/workflows/*.yml`` (CI)
- ``.pre-commit-config.yaml``
- ``Makefile``, ``*.mk``
- ``*.sh``, ``*.bash``, ``*.zsh``
- ``pyproject.toml`` (addopts)

Honors ``# noqa: G5`` on the same line as the ``-n auto`` match.

Exit 0 = clean.
Exit 1 = violations (missing ``--dist=loadgroup`` for a ``-n auto``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Paths to scan. Relative to _ROOT.
_SCAN_TARGETS: tuple[Path, ...] = (
    _ROOT / ".github" / "workflows",
    _ROOT / ".pre-commit-config.yaml",
    _ROOT / "Makefile",
    _ROOT / "pyproject.toml",
    _ROOT / "scripts",
    _ROOT / ".claude" / "scripts",
)

_SHELLY_SUFFIXES = {".sh", ".bash", ".zsh"}
_SHELLY_NAMES = {"Makefile"}
_SCANNABLE_SUFFIXES = {".yml", ".yaml", ".toml", ".sh", ".bash", ".zsh", ".mk", ".py"}

# Match ``-n auto`` / ``-n=auto`` / ``-n "auto"`` / ``-n'auto'``. The
# trailing ``\b`` rules out surface-look-alikes like ``-n autox`` (which
# pytest would reject anyway, but the detector should not match prose).
_N_AUTO_RE = re.compile(r'-n[\s=]+[\'"]?auto[\'"]?\b(?!\w)')
_LOADGROUP_RE = re.compile(r'--dist[\s=]+[\'"]?loadgroup[\'"]?|-dloadgroup')
_NOQA_G5_RE = re.compile(r"#\s*noqa:\s*G5\b")

# A pytest invocation can span up to this many lines after ``-n auto`` via
# backslash continuations or YAML block scalars. Five lines covers all
# known real-world cases in this repo without being unboundedly greedy.
_LOADGROUP_WINDOW = 5


def _iter_candidate_files() -> list[Path]:
    """Yield every file under the scan targets that we should inspect."""
    files: list[Path] = []
    for target in _SCAN_TARGETS:
        if not target.exists():
            continue
        if target.is_file():
            files.append(target)
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in _SCANNABLE_SUFFIXES or path.name in _SHELLY_NAMES:
                files.append(path)
    return sorted(set(files))


def _is_reference_not_command(line: str, path: Path) -> bool:
    """True if the ``-n auto`` match is a prose reference, not an actual
    pytest invocation.

    Reference contexts (exempt):

    - Full-line comments in shell / Makefile / YAML (``#`` or ``-`` + ``#``).
    - Python docstring / ``#`` comment lines.
    - YAML ``#`` comment lines.

    Command contexts (not exempt): any line where ``-n auto`` appears as
    part of a running shell command.
    """
    stripped = line.lstrip()
    # YAML / shell / Makefile line comments
    if stripped.startswith("#"):
        return True
    # Python docstrings / inline descriptions in scripts: look for the
    # flag appearing inside a docstring-style bullet point (``- `` + text
    # containing the flag in backticks).
    if path.suffix == ".py" and "``" in line and "-n" in line[: line.find("-n") + 5]:
        # Rough heuristic: the flag is inside backtick-quoted prose.
        if "``-n" in line or "``pytest" in line:
            return True
    return False


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, line_text)] for every ``-n auto`` without a
    nearby ``--dist=loadgroup`` in ``path``."""
    violations: list[tuple[int, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return violations

    for i, line in enumerate(lines):
        if not _N_AUTO_RE.search(line):
            continue
        if _NOQA_G5_RE.search(line):
            continue
        if _is_reference_not_command(line, path):
            continue
        # Look for --dist=loadgroup on the same line OR within a
        # _LOADGROUP_WINDOW-line window around it (covers backslash
        # continuations in shell and YAML block scalars, plus the
        # occasional ``--dist`` preceding the ``-n`` flag).
        window_end = min(i + _LOADGROUP_WINDOW + 1, len(lines))
        window_start = max(i - _LOADGROUP_WINDOW, 0)
        window = "\n".join(lines[window_start:window_end])
        if _LOADGROUP_RE.search(window):
            continue
        violations.append((i + 1, line.rstrip()))
    return violations


def main() -> int:
    all_violations: list[tuple[Path, int, str]] = []
    for path in _iter_candidate_files():
        for lineno, line in find_violations(path):
            try:
                rel = path.relative_to(_ROOT)
            except ValueError:
                rel = path
            all_violations.append((rel, lineno, line))

    if not all_violations:
        return 0

    print(
        "ERROR (G5): pytest `-n auto` found without `--dist=loadgroup` "
        "nearby.\n"
        "Without `--dist=loadgroup`, `@pytest.mark.xdist_group` markers are "
        "silently non-enforcing and singleton-sharing tests will race "
        "under xdist.\n"
        "Add `--dist=loadgroup` to the same command, or append "
        "`# noqa: G5 (reason)` if xdist-group serialization is genuinely "
        "not needed.\n",
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
