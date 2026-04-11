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
"""

from __future__ import annotations

import argparse
import json
import re
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
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid baseline file {path}: expected top-level JSON object")
    if "modules" not in data or not isinstance(data["modules"], dict):
        raise ValueError(f"Invalid baseline file {path}: expected top-level 'modules' object")
    return data


def _default_repo_root() -> Path:
    # Script lives at <repo>/scripts/check_module_coverage_floor.py.
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
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
    return parser.parse_args()


def validate_inputs(args: argparse.Namespace) -> int:
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


def main() -> int:
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

    repo_root = args.repo_root.resolve() if args.repo_root else _default_repo_root()

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
