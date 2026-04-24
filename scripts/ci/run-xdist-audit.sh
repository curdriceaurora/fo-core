#!/usr/bin/env bash
# run-xdist-audit.sh — Run the non-integration, non-benchmark suite 3 times
# under xdist parallelism and report any non-deterministic failures.
#
# Usage: bash scripts/ci/run-xdist-audit.sh [output-dir]
# Output: per-run logs in OUTPUT_DIR, summary printed to stdout.

set -euo pipefail

OUTPUT_DIR="${1:-/tmp/xdist-audit-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUTPUT_DIR"

echo "=== xdist audit — 3 runs — output: $OUTPUT_DIR ==="

FAILED_RUNS=0
for i in 1 2 3; do
    echo ""
    echo "--- Run $i/3 ---"
    LOG="$OUTPUT_DIR/run-$i.txt"
    # pytest exits non-zero on test failures; capture it so we can continue
    pytest tests/ \
        -m "not integration and not benchmark and not e2e" \
        -n auto \
        --dist=loadgroup \
        --timeout=30 \
        -q \
        --tb=short \
        --override-ini="addopts=" \
        2>&1 | tee "$LOG" || true

    FAILURES=$(grep -c "^FAILED\|^ERROR" "$LOG" || true)
    echo "Run $i: $FAILURES failure(s)/error(s)"
    if [ "$FAILURES" -gt 0 ]; then
        FAILED_RUNS=$((FAILED_RUNS + 1))
    fi
done

echo ""
echo "=== Summary ==="
echo "Runs with failures: $FAILED_RUNS/3"
echo ""
echo "=== Tests that failed in ANY run (sorted by frequency) ==="
grep -h "^FAILED\|^ERROR" "$OUTPUT_DIR"/run-*.txt 2>/dev/null \
    | sort | uniq -c | sort -rn \
    || echo "(no failures found)"

echo ""
echo "=== Full logs in: $OUTPUT_DIR ==="
