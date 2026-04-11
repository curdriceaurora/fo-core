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
    """
    Remove ANSI escape sequences from a string.
    
    Returns:
        str: The input string with all ANSI escape sequences removed.
    """
    return ANSI_RE.sub("", text)


def parse_report(report_path: Path) -> dict[str, float]:
    """
    Parse a pytest-cov "term-missing" report file and extract per-module coverage percentages.
    
    This function reads the given report file, ignores ANSI escape sequences and any
    GitHub Actions leading metadata, then scans for table rows produced by
    pytest-cov. It returns a mapping from module path (as shown in the report)
    to the module's coverage percentage.
    
    Parameters:
        report_path (Path): Path to the pytest-cov term-missing report file to parse.
    
    Returns:
        dict[str, float]: Mapping of module path to coverage percentage (e.g., {"src/foo.py": 87.5}).
    """
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
    """
    Load and validate a JSON baseline containing per-module coverage floors.
    
    Parameters:
        path (Path): Filesystem path to the JSON baseline file.
    
    Returns:
        dict[str, Any]: The parsed JSON object. The object is guaranteed to contain a top-level
        "modules" key whose value is a dict mapping module paths to their coverage floor values.
    
    Raises:
        ValueError: If the file's top-level "modules" key is missing or is not a dictionary.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if "modules" not in data or not isinstance(data["modules"], dict):
        raise ValueError(f"Invalid baseline file {path}: expected top-level 'modules' object")
    return data


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments for the per-module coverage floor checker.
    
    Parameters:
        None
    
    Returns:
        args (argparse.Namespace): Parsed arguments with the following attributes:
            report_path (Path): Path to the pytest-cov term-missing report file (required).
            baseline_path (Path): Path to the JSON baseline file containing a top-level "modules" mapping (required).
            new_module_min (float): Minimum coverage percent required for modules not present in the baseline.
            tolerance (float): Allowed negative coverage drift (percent points) before reporting a failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--baseline-path", type=Path, required=True)
    parser.add_argument(
        "--new-module-min",
        type=float,
        default=71.9,
        help="Minimum coverage required for modules not yet present in baseline.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.5,
        help="Allowed negative drift before failing (to absorb tiny CI variance).",
    )
    return parser.parse_args()


def validate_inputs(args: argparse.Namespace) -> int:
    """
    Validate that the provided report and baseline file paths exist.
    
    Parameters:
        args (argparse.Namespace): Namespace containing `report_path` and `baseline_path` (Path objects).
    
    Returns:
        int: `0` if both paths exist, `2` if either path is missing (an error message is printed).
    """
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
    """
    Determine coverage regressions, low-coverage new modules, and baseline modules missing from the report.
    
    Parameters:
        report_modules (dict[str, float]): Mapping of module path to observed coverage percentage from the current report.
        baseline_modules (dict[str, float]): Mapping of module path to configured coverage floor percentage from the baseline.
        tolerance (float): Allowed negative drift (percentage points) before a module is considered failing the floor.
        new_module_min (float): Minimum coverage percentage required for modules not present in the baseline.
        repo_root (Path): Repository root used to check whether baseline-listed module files exist on disk.
    
    Returns:
        tuple[list[tuple[str, float, float]], list[tuple[str, float]], list[str]]:
            - regressions: list of (module, actual_coverage, floor) for baseline modules whose actual coverage plus
              tolerance is less than the configured floor.
            - low_new_modules: list of (module, actual_coverage) for modules absent from the baseline whose actual
              coverage plus tolerance is less than new_module_min.
            - missing_from_report: list of baseline module paths that were not found in the report but whose files
              exist under repo_root.
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
) -> None:
    """
    Print a human-readable summary of per-module coverage results and list any regressions, newly low modules, and baseline modules missing from the report.
    
    Parameters:
        report_modules (dict[str, float]): Mapping of module path to observed coverage percentage.
        regressions (list[tuple[str, float, float]]): Entries of (module, actual_coverage, baseline_floor) where actual coverage plus tolerance fell below the baseline floor.
        low_new_modules (list[tuple[str, float]]): Entries of (module, actual_coverage) for modules not present in the baseline whose coverage is below the configured new-module minimum.
        missing_from_report (list[str]): Baseline module paths that were not found in the parsed coverage report but exist in the repository.
        new_module_min (float): Configured minimum coverage percentage used when reporting low new modules.
    """
    print(
        f"Per-module coverage check: {len(report_modules)} modules parsed, "
        f"{len(regressions)} regressions, {len(low_new_modules)} low new modules"
    )

    if regressions:
        print("\nCoverage regressions (actual < baseline floor):")
        for module, actual, floor in sorted(regressions, key=lambda x: x[1] - x[2]):
            print(f"  - {module}: actual={actual:.1f}% floor={floor:.1f}%")

    if low_new_modules:
        print("\nNew modules below required minimum:")
        for module, actual in sorted(low_new_modules, key=lambda x: x[1]):
            print(f"  - {module}: actual={actual:.1f}% min={new_module_min:.1f}%")

    if missing_from_report:
        print("\nBaseline modules missing from coverage report:")
        for module in sorted(missing_from_report):
            print(f"  - {module}")


def main() -> int:
    """
    Run the per-module coverage-floor check using command-line arguments.
    
    Parses CLI arguments, validates input files, reads the coverage report and baseline, evaluates regressions and low-coverage new modules (with configured tolerance and new-module minimum), prints a summary and detailed findings, and determines the process exit status.
    
    Returns:
        int: `0` on success (no regressions, no low new modules, and no missing baseline modules),
             `1` if the coverage gate failed (any regressions, low new modules, or missing baseline modules),
             `2` for input validation or report-parsing errors.
    """
    args = parse_args()
    input_status = validate_inputs(args)
    if input_status:
        return input_status

    report_modules = parse_report(args.report_path)
    if not report_modules:
        print("ERROR: no module coverage rows parsed from report")
        return 2

    baseline = load_baseline(args.baseline_path)
    baseline_modules: dict[str, float] = {
        module: float(floor) for module, floor in baseline["modules"].items()
    }

    regressions, low_new_modules, missing_from_report = evaluate(
        report_modules,
        baseline_modules,
        tolerance=args.tolerance,
        new_module_min=args.new_module_min,
        repo_root=Path.cwd(),
    )
    print_report(
        report_modules,
        regressions,
        low_new_modules,
        missing_from_report,
        new_module_min=args.new_module_min,
    )

    if regressions or low_new_modules or missing_from_report:
        print("\nFAIL: per-module integration coverage gate failed")
        return 1

    print("PASS: per-module integration coverage gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
