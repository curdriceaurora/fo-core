# Testing & QA — Coverage Report Plan

## Objective
Run full test suite with coverage, analyze results, and produce a prioritized gap analysis.

---

## Steps

### Step 1: Run Full Test Suite with Coverage

- Run `pytest --cov=file_organizer --cov-report=term-missing` on the entire test suite
- Capture pass/fail counts, skip counts, and error counts
- Generate coverage data for all `src/file_organizer/` modules

### Step 2: Analyze Coverage by Package

- Break down coverage by top-level package: `models/`, `services/`, `core/`, `cli/`, `utils/`, `api/`, `web/`, `tui/`, `daemon/`, `events/`, `pipeline/`, etc.
- Identify packages with <50% coverage (critical gaps)
- Identify packages with 50-80% coverage (moderate gaps)
- List fully covered packages (>80%)

### Step 3: Identify Failing/Error Tests

- Categorize failures: import errors, missing deps, logic bugs, stale tests
- Count tests by status: passed, failed, errors, skipped

### Step 4: Produce Summary Report

- Write summary to `tasks/coverage-report.md` with:
  - Overall metrics (total tests, pass rate, overall coverage %)
  - Per-package coverage table
  - Top 10 lowest-covered modules
  - Failure triage summary
  - Recommended priority order for gap-filling

---

## Approach Notes

- Run from branch `claude/review-and-plan-NIDeR`
- Install package in editable mode first if needed
- Use `--tb=no` for initial run to avoid huge output, then drill into failures separately
- Timeout individual test collection at reasonable limits
