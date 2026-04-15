#!/usr/bin/env bash
# scripts/coverage/ratchet.sh — one-command integration coverage ratchet
#
# Usage (from repo root):
#   bash scripts/coverage/ratchet.sh [check|update|dry-run]
#
#   check    Measure then gate-check against baseline (mirrors CI). DEFAULT.
#   update   Measure then ratchet baseline floors upward (never lowers).
#   dry-run  Measure then preview changes without writing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MEASURE_SCRIPT="${REPO_ROOT}/.claude/scripts/measure-integration-coverage.sh"
CHECKER="${REPO_ROOT}/scripts/check_module_coverage_floor.py"
BASELINE="${REPO_ROOT}/scripts/coverage/integration_module_floor_baseline.json"
if [[ "$#" -gt 1 ]]; then
  echo "ERROR: too many arguments" >&2
  echo "Usage: bash scripts/coverage/ratchet.sh [check|update|dry-run]" >&2
  exit 2
fi

MODE="${1:-check}"

case "${MODE}" in
  check|update|dry-run) ;;
  *)
    echo "ERROR: unknown mode '${MODE}'" >&2
    echo "Usage: bash scripts/coverage/ratchet.sh [check|update|dry-run]" >&2
    exit 2
    ;;
esac

REPORT="$(mktemp "${TMPDIR:-/tmp}/integration-coverage-report.XXXXXX.txt")"
trap 'rm -f "${REPORT}"' EXIT

echo "==> [1/2] Measuring integration coverage (mode: ${MODE})..."
bash "${MEASURE_SCRIPT}" | tee "${REPORT}"

echo ""
echo "==> [2/2] Evaluating coverage floors..."

case "${MODE}" in
  check)
    python3 "${CHECKER}" --report-path "${REPORT}" --baseline-path "${BASELINE}"
    ;;
  update)
    python3 "${CHECKER}" --report-path "${REPORT}" --baseline-path "${BASELINE}" --update-baseline
    echo ""
    echo "==> Baseline updated. Commit ${BASELINE} with your PR."
    ;;
  dry-run)
    python3 "${CHECKER}" --report-path "${REPORT}" --baseline-path "${BASELINE}" --update-baseline --dry-run
    ;;
esac
