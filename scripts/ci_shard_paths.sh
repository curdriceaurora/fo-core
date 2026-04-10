#!/usr/bin/env bash
# ci_shard_paths.sh — single source of truth for the 6-shard test split.
#
# Usage: bash scripts/ci_shard_paths.sh <shard_number>
# Prints a space-separated list of pytest path arguments to stdout.
#
# Both ci.yml (push) and ci-full.yml (daily) import this script so the mapping
# stays in sync. When test directories are added or moved, update ONLY this
# file; both pipelines will pick up the change automatically.
#
# Shard sizing goal: ~3 000-4 500 test functions per shard, keeping runtime
# balanced without over-fragmenting the matrix.

set -euo pipefail

SHARD="${1:-}"
if [[ -z "$SHARD" ]]; then
    echo "Usage: $0 <shard_number>" >&2
    exit 1
fi

case "$SHARD" in
    1) PATHS="tests/integration" ;;
    2) PATHS="tests/services tests/models tests/events tests/optimization" ;;
    3) PATHS="tests/cli tests/methodologies tests/parallel tests/pipeline" ;;
    4) PATHS="tests/ci tests/unit" ;;
    5) PATHS="tests/utils tests/undo tests/history tests/daemon tests/watcher tests/updater tests/test_*.py" ;;
    6) PATHS="tests/core tests/config tests/docs tests/integrations tests/interfaces" ;;
    *)
        echo "Unknown shard: $SHARD (valid: 1-6)" >&2
        exit 1
        ;;
esac

export PATHS
echo "$PATHS"
