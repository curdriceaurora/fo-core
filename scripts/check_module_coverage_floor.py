#!/usr/bin/env python3
"""Enforce per-module integration coverage floors from pytest-cov term report.

Why this exists:
- The global integration gate (single % threshold) can pass while individual
  modules regress sharply.
- This script adds a per-module non-regression gate with explicit, reviewable
  floors stored in version control.

Expected report format is pytest-cov's table output (``--cov-report=term-missing``),
for rows like:
    src/file_organizer/foo.py   123   10   20   4   88%   12-18, 44

--update-baseline mode
----------------------
Pass ``--update-baseline`` to write the current report's coverage values back to
the baseline file instead of running the gate check::

    pytest ... | tee /tmp/report.txt
    python scripts/check_module_coverage_floor.py \\
      --report-path /tmp/report.txt \\
      --baseline-path scripts/coverage/integration_module_floor_baseline.json \\
      --update-baseline

Behaviour:
- Existing modules: floor is set to actual (floors are a ratchet — never lowered)
- New modules: added with floor = actual
- Deleted modules (in baseline but not in report and not on disk): removed
- ``generated_at_utc`` and ``source`` block updated with current timestamp
- Exits 0; does not run the gate check
- Add ``--dry-run`` to print the diff without writing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
ROW_RE = re.compile(
    r"(src/file_organizer/\S+)\s+"  # module path
    r"(\d+)\s+"  # stmts
    r"(\d+)\s+"  # miss
    r"(\d+)\s+"  # branch
    r"(\d+)\s+"  # brpart
    r"(\d+(?:\.\d+)?)%\s*"  # cover
    r"(.*)$"  # missing column (optional)
)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from *text*."""
    return ANSI_RE.sub("", text)


def parse_report(report_path: Path) -> dict[str, float]:
    """Parse pytest-cov term report and return module -> coverage percent."""
    modules: dict[str, float] = {}
    for raw_line in report_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = _strip_ansi(raw_line)
        # GH Actions prefixes with tab-separated metadata; keep last segment.
        line = line.split("\t")[-1].strip()
        match = ROW_RE.search(line)
        if not match:
            continue
        path = match.group(1)
        cover = float(match.group(6))
        modules[path] = cover
    return modules


def load_baseline(path: Path) -> dict[str, Any]:
    """Load and minimally validate the baseline JSON file.

    Raises:
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If the top-level structure is missing required keys.
        OSError: If the file cannot be read.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid baseline file {path}: expected top-level JSON object")
    if "modules" not in data or not isinstance(data["modules"], dict):
        raise ValueError(f"Invalid baseline file {path}: expected top-level 'modules' object")
    return data


def _default_repo_root() -> Path:
    """Return the repository root derived from this script's location."""
    # Script lives at <repo>/scripts/check_module_coverage_floor.py.
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    """Build and return the argument parser namespace."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--baseline-path", type=Path, required=True)
    parser.add_argument(
        "--new-module-min",
        type=float,
        default=None,
        help="Override minimum coverage required for modules not yet present in baseline.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="Override allowed negative drift before failing (to absorb tiny CI variance).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Repository root for checking whether baseline modules still exist on disk. "
            "Defaults to the script-anchored repository root."
        ),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        default=False,
        help=(
            "Write current coverage values back to the baseline file (ratchet mode). "
            "Floors are never lowered; new modules are added; deleted modules are removed. "
            "Exits 0 without running the gate check."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="With --update-baseline: print the diff without writing the file.",
    )
    return parser.parse_args()


def validate_inputs(args: argparse.Namespace) -> int:
    """Return 2 with an error message if required input files do not exist, else 0."""
    if not args.report_path.exists():
        print(f"ERROR: report not found: {args.report_path}")
        return 2
    if not args.baseline_path.exists():
        print(f"ERROR: baseline not found: {args.baseline_path}")
        return 2
    return 0


def evaluate(
    report_modules: dict[str, float],
    baseline_modules: dict[str, float],
    *,
    tolerance: float,
    new_module_min: float,
    repo_root: Path,
) -> tuple[list[tuple[str, float, float]], list[tuple[str, float]], list[str]]:
    """Compare report coverage against baseline floors and return gate findings.

    Returns:
        (regressions, low_new_modules, missing_from_report) where each entry is
        a tuple describing a gate violation.
    """
    regressions: list[tuple[str, float, float]] = []
    low_new_modules: list[tuple[str, float]] = []
    missing_from_report: list[str] = []

    for module, floor in baseline_modules.items():
        actual = report_modules.get(module)
        if actual is None:
            if (repo_root / module).exists():
                missing_from_report.append(module)
            continue
        if actual + tolerance < floor:
            regressions.append((module, actual, floor))

    for module, actual in report_modules.items():
        if module in baseline_modules:
            continue
        if actual + tolerance < new_module_min:
            low_new_modules.append((module, actual))

    return regressions, low_new_modules, missing_from_report


def print_report(
    report_modules: dict[str, float],
    regressions: list[tuple[str, float, float]],
    low_new_modules: list[tuple[str, float]],
    missing_from_report: list[str],
    *,
    new_module_min: float,
    tolerance: float,
    baseline_path: Path,
) -> None:
    """Print a human-readable gate result to stdout."""
    print(
        f"Per-module coverage check: {len(report_modules)} modules parsed, "
        f"{len(regressions)} regressions, {len(low_new_modules)} low new modules"
    )

    if regressions:
        print("\nCoverage regressions (actual below effective baseline floor):")
        for module, actual, floor in sorted(regressions, key=lambda x: x[1] - x[2]):
            effective_floor = floor - tolerance
            print(
                "  - "
                f"{module}: actual={actual:.1f}% "
                f"effective_floor={effective_floor:.1f}% "
                f"(baseline={floor:.1f}% tolerance={tolerance:.1f}%)"
            )

    if low_new_modules:
        print("\nNew modules below required minimum:")
        effective_min = new_module_min - tolerance
        for module, actual in sorted(low_new_modules, key=lambda x: x[1]):
            print(
                "  - "
                f"{module}: actual={actual:.1f}% "
                f"effective_min={effective_min:.1f}% "
                f"(nominal={new_module_min:.1f}% tolerance={tolerance:.1f}%)"
            )

    if missing_from_report:
        print("\nBaseline modules missing from coverage report:")
        for module in sorted(missing_from_report):
            print(f"  - {module}")
        print("\nThese modules exist on disk but were not reported by pytest-cov.")
        print("This usually means they have lost all integration test coverage.")
        print("To fix:")
        print("  1. Add integration tests so each module appears in the report, or")
        print("  2. If a module was renamed or moved, update the baseline file:")
        print(f"     {baseline_path}")


def _parse_baseline(path: Path) -> dict[str, Any] | None:
    """Load the baseline file, printing a user-friendly error and returning None on failure."""
    try:
        return load_baseline(path)
    except json.JSONDecodeError as exc:
        print(f"ERROR: baseline file is not valid JSON: {path}\n{exc}")
        return None
    except OSError as exc:
        print(f"ERROR: failed to read baseline file {path}: {exc}")
        return None
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return None


def _resolve_policy(
    baseline: dict[str, Any], args: argparse.Namespace
) -> tuple[float, float] | None:
    """Resolve (new_module_min, tolerance) from CLI args then baseline policy, or None on error."""
    policy = baseline.get("policy", {})
    if policy is None or not isinstance(policy, dict):
        policy = {}
    try:
        new_module_min = float(
            args.new_module_min
            if args.new_module_min is not None
            else policy.get("new_module_min_percent", 71.9)
        )
        tolerance = float(
            args.tolerance if args.tolerance is not None else policy.get("tolerance_percent", 0.5)
        )
    except (TypeError, ValueError) as exc:
        print(f"ERROR: invalid coverage policy in baseline: {exc}")
        return None

    return new_module_min, tolerance


def _compute_baseline_delta(
    report_modules: dict[str, float],
    existing: dict[str, float],
    repo_root: Path,
) -> tuple[dict[str, float], list[tuple[str, float, float]], list[tuple[str, float]], list[str]]:
    """Compute the ratcheted module map and the three change lists.

    Returns:
        (new_modules, raised, added, removed) where:
        - new_modules: updated module -> floor mapping
        - raised: (module, old_floor, new_floor) for improved modules
        - added: (module, actual) for brand-new modules
        - removed: module names that were deleted from disk
    """
    raised: list[tuple[str, float, float]] = []
    added: list[tuple[str, float]] = []
    removed: list[str] = []
    new_modules: dict[str, float] = {}

    for module, actual in sorted(report_modules.items()):
        if module in existing:
            old_floor = existing[module]
            new_floor = max(old_floor, actual)
            new_modules[module] = new_floor
            if new_floor > old_floor:
                raised.append((module, old_floor, new_floor))
        else:
            new_modules[module] = actual
            added.append((module, actual))

    for module in existing:
        if module not in report_modules:
            if not (repo_root / module).exists():
                removed.append(module)
            else:
                new_modules[module] = existing[module]

    return new_modules, raised, added, removed


def _print_baseline_diff(
    raised: list[tuple[str, float, float]],
    added: list[tuple[str, float]],
    removed: list[str],
) -> None:
    """Print a human-readable summary of pending baseline changes to stdout."""
    if raised:
        print(f"Raising {len(raised)} floor(s):")
        for module, old, new in raised:
            print(f"  {module}: {old:.1f}% → {new:.1f}%")
    if added:
        print(f"Adding {len(added)} new module(s):")
        for module, actual in added:
            print(f"  {module}: {actual:.1f}%")
    if removed:
        print(f"Removing {len(removed)} deleted module(s):")
        for module in removed:
            print(f"  {module}")
    if not raised and not added and not removed:
        print("No changes — baseline is already up-to-date.")


def update_baseline(
    report_modules: dict[str, float],
    baseline: dict[str, Any],
    *,
    repo_root: Path,
    dry_run: bool,
    baseline_path: Path,
) -> int:
    """Ratchet baseline floors up to current coverage values and write the result.

    Args:
        report_modules: Module -> actual coverage % from the current run.
        baseline: Full parsed baseline document (mutated in-place before serialisation).
        repo_root: Repository root used to decide whether a missing module was deleted.
        dry_run: When True, print the diff without writing.
        baseline_path: Path to the baseline JSON file to (over)write.

    Returns:
        0 on success, 2 on any failure (invalid baseline floor values or write errors).
    """
    try:
        existing: dict[str, float] = {
            module: float(floor) for module, floor in baseline.get("modules", {}).items()
        }
    except (TypeError, ValueError) as exc:
        print(f"ERROR: invalid module floor value in baseline: {exc}")
        return 2

    new_modules, raised, added, removed = _compute_baseline_delta(
        report_modules, existing, repo_root
    )
    _print_baseline_diff(raised, added, removed)

    if dry_run:
        print("(dry-run: baseline not written)")
        return 0

    baseline["modules"] = new_modules
    baseline["generated_at_utc"] = datetime.now(tz=UTC).isoformat()
    baseline["source"] = {"workflow_run_id": None, "job_id": None, "commit": None}

    payload = json.dumps(baseline, indent=2, sort_keys=False) + "\n"
    tmp_fd: int | None = None
    tmp_name: str | None = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=baseline_path.parent, suffix=".tmp", text=True)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            tmp_fd = None  # fdopen takes ownership; don't double-close on error
            fh.write(payload)
        os.replace(tmp_name, baseline_path)
    except OSError as exc:
        print(f"ERROR: failed to write baseline file {baseline_path}: {exc}")
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return 2

    print(f"Baseline written to {baseline_path}")
    return 0


def main() -> int:
    """Entry point: parse args, run gate check or baseline update, return exit code."""
    args = parse_args()
    input_status = validate_inputs(args)
    if input_status:
        return input_status

    report_modules = parse_report(args.report_path)
    if not report_modules:
        print("ERROR: no module coverage rows parsed from report")
        return 2

    baseline = _parse_baseline(args.baseline_path)
    if baseline is None:
        return 2

    repo_root = args.repo_root.resolve() if args.repo_root else _default_repo_root()

    if args.update_baseline:
        if args.dry_run:
            print("--update-baseline --dry-run: showing changes without writing")
        return update_baseline(
            report_modules,
            baseline,
            repo_root=repo_root,
            dry_run=args.dry_run,
            baseline_path=args.baseline_path,
        )

    if args.dry_run:
        print("ERROR: --dry-run requires --update-baseline")
        return 2

    policy = _resolve_policy(baseline, args)
    if policy is None:
        return 2
    new_module_min, tolerance = policy

    try:
        baseline_modules: dict[str, float] = {
            module: float(floor) for module, floor in baseline["modules"].items()
        }
    except (TypeError, ValueError) as exc:
        print(f"ERROR: invalid module floor value in baseline: {exc}")
        return 2

    regressions, low_new_modules, missing_from_report = evaluate(
        report_modules,
        baseline_modules,
        tolerance=tolerance,
        new_module_min=new_module_min,
        repo_root=repo_root,
    )
    print_report(
        report_modules,
        regressions,
        low_new_modules,
        missing_from_report,
        new_module_min=new_module_min,
        tolerance=tolerance,
        baseline_path=args.baseline_path,
    )

    if regressions or low_new_modules or missing_from_report:
        print("\nFAIL: per-module integration coverage gate failed")
        return 1

    print("PASS: per-module integration coverage gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
