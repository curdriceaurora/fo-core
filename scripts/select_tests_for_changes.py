#!/usr/bin/env python3
"""Select pytest paths for files changed relative to a git ref or staging area.

Usage
-----
Local (staged changes)::

    python scripts/select_tests_for_changes.py --staged --format args

CI (diff against base branch)::

    python scripts/select_tests_for_changes.py --base origin/main --format json

Output
------
``--format args``  (default)
    Space-joined list of pytest paths, ready for shell expansion.

``--format json``
    JSON array of pytest paths.

Behaviour
---------
- Changed test files are included directly (if they exist on disk).
- Changed ``src/<package>/…`` files map to their paired ``tests/<package>/``
  directory via :data:`PACKAGE_TEST_MAP`.
- Paths that do not exist on disk are silently filtered out.
- When no mapped test path is found the selector falls back to
  :data:`FALLBACK_PATHS` (``tests/ci``).
- Output is always deduplicated and sorted.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Mapping: src package prefix → list of test directories
# ---------------------------------------------------------------------------

PACKAGE_TEST_MAP: dict[str, list[str]] = {
    "src/cli": ["tests/cli"],
    "src/config": ["tests/config"],
    "src/core": ["tests/core"],
    "src/daemon": ["tests/daemon"],
    "src/events": ["tests/events"],
    "src/history": ["tests/history"],
    "src/integrations": ["tests/integrations"],
    "src/interfaces": ["tests/interfaces"],
    "src/methodologies": ["tests/methodologies"],
    "src/models": ["tests/models"],
    "src/optimization": ["tests/optimization"],
    "src/parallel": ["tests/parallel"],
    "src/pipeline": ["tests/pipeline"],
    "src/services": ["tests/services"],
    "src/undo": ["tests/undo"],
    "src/updater": ["tests/updater"],
    "src/utils": ["tests/utils"],
    "src/watcher": ["tests/watcher"],
}

# Fallback when no mapped or test-file path is found
FALLBACK_PATHS: list[str] = ["tests/ci"]


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def get_changed_files(staged: bool = False, base: str | None = None) -> list[str]:
    """Return a list of changed file paths from git.

    Args:
        staged: If True, use ``git diff --cached`` (staged changes).
        base: If provided, diff against this ref (e.g. ``origin/main``).

    Returns:
        List of relative file paths reported by git.

    Raises:
        ValueError: If neither ``staged`` nor ``base`` is provided.
    """
    if staged:
        cmd = ["git", "diff", "--cached", "--name-only"]
    elif base is not None:
        cmd = ["git", "diff", base, "--name-only"]
    else:
        raise ValueError("Either staged=True or a base ref must be provided")

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [line for line in result.stdout.splitlines() if line.strip()]


def select_test_paths(changed_files: list[str], repo_root: Path) -> list[str]:
    """Map changed files to pytest paths.

    Args:
        changed_files: Relative file paths (as returned by git).
        repo_root: Absolute path to the repository root; used to verify
            that candidate test paths exist on disk.

    Returns:
        Sorted, deduplicated list of pytest paths relative to ``repo_root``.
        Returns :data:`FALLBACK_PATHS` (filtered to existing paths) when no
        source-file mapping or test-file passthrough produces a result.
    """
    selected: set[str] = set()

    for changed in changed_files:
        path = Path(changed)
        parts = path.parts

        if not parts:
            continue

        # --- Test files: include directly if they exist on disk -----------
        if parts[0] == "tests" and path.suffix == ".py":
            if (repo_root / path).exists():
                selected.add(str(path))
            continue

        # --- Source files: look up PACKAGE_TEST_MAP ----------------------
        if parts[0] == "src" and len(parts) >= 2:
            prefix = f"src/{parts[1]}"
            test_dirs = PACKAGE_TEST_MAP.get(prefix, [])
            for td in test_dirs:
                if (repo_root / td).exists():
                    selected.add(td)
            # Unmapped src files are counted as "src change" but produce no dirs.
            # They will fall through to the fallback below.

    # --- Fallback: use tests/ci when nothing mapped ----------------------
    if not selected:
        for fb in FALLBACK_PATHS:
            if (repo_root / fb).exists():
                selected.add(fb)

    return sorted(selected)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select pytest paths for changed files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--staged",
        action="store_true",
        help="Use staged (cached) changes (git diff --cached).",
    )
    source.add_argument(
        "--base",
        metavar="REF",
        help="Compare against this git ref (e.g. origin/main).",
    )
    parser.add_argument(
        "--format",
        choices=["json", "args"],
        default="args",
        help="Output format: 'json' (JSON array) or 'args' (space-joined, default).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent

    try:
        changed = get_changed_files(staged=args.staged, base=args.base)
    except subprocess.CalledProcessError as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return 1

    paths = select_test_paths(changed, repo_root)

    if args.format == "json":
        print(json.dumps(paths))
    else:
        print(" ".join(paths))

    return 0


if __name__ == "__main__":
    sys.exit(main())
