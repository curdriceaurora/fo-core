#!/usr/bin/env bash
# update-integration-baseline.sh — Re-run integration suite and update the
# per-module coverage ratchet baseline.
#
# Usage:
#   bash .claude/scripts/update-integration-baseline.sh
#   bash .claude/scripts/update-integration-baseline.sh --dry-run
#
# Flags:
#   --dry-run   Print what would change without writing the baseline file.
#
# The script:
#   1. Erases stale coverage data (prevents stale .coverage from unit runs
#      inflating the measurement — see CI anti-pattern C7).
#   2. Runs the integration test suite via pytest.
#   3. Captures the term-missing coverage report.
#   4. Passes it to check_module_coverage_floor.py --update-baseline.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
BASELINE="$REPO_ROOT/scripts/coverage/integration_module_floor_baseline.json"
REPORT_TMP="$(mktemp /tmp/integration_cov_report.XXXXXX.txt)"
trap 'rm -f "$REPORT_TMP"' EXIT

DRY_RUN=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN="--dry-run" ;;
    esac
done

pip install -e ".[search]" --quiet --break-system-packages 2>/dev/null || pip3 install -e ".[search]" --quiet --break-system-packages

coverage erase

pytest tests/ -m "integration" \
    --strict-markers \
    --cov=fo \
    --cov-branch \
    --cov-report=term-missing \
    --override-ini="addopts=" \
    --tb=no \
    -q 2>&1 | tee "$REPORT_TMP" || true

python3 "$REPO_ROOT/scripts/check_module_coverage_floor.py" \
    --report-path "$REPORT_TMP" \
    --baseline-path "$BASELINE" \
    --update-baseline $DRY_RUN
