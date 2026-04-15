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
# Balancing policy (duration-based, not test-count-based)
# -------------------------------------------------------
# Shards are sized to keep max/min wall-clock runtime ≤ 1.5x across both
# py3.11 and py3.12 matrix legs (median of last 3 successful main-push runs).
#
# Baseline (run 24274089925, main, 2026-04-10):
#   shard1: 4 044 tests (integration)       py3.11 127s  py3.12 138s  ← was max
#   shard2: 4 154 tests (services+...)      py3.11 ~120s py3.12 ~130s
#   shard3: 2 308 tests (cli+...)           py3.11 ~104s py3.12 ~110s
#   shard4:   373 tests (ci+unit)           py3.11  62s  py3.12  57s  ← was min
#   shard5: 1 376 tests (utils+...)         py3.11 ~80s  py3.12 ~85s
#   shard6:   344 tests (core+...)          py3.11 ~68s  py3.12 ~72s
#   Ratio: 2.05x (py3.11)  2.42x (py3.12)
#
# Remapping strategy:
#   • Split tests/integration at the alphabetical midpoint (~2 050 each half)
#     using shell glob patterns (test_[a-l]*.py vs test_[m-z]*.py); the caller
#     runs `pytest $PATHS` with $PATHS unquoted so the shell expands the globs.
#   • Split tests/services by subdirectory (heavy: intelligence+dedup; light: rest)
#   • Consolidate the formerly under-loaded shards 4, 5, 6 with the freed
#     directories to bring every shard into the ~1 700–2 500 test range.
#
# Estimated post-change counts and wall-clock (using 22 ms/test + 40 s overhead):
#   shard1: ~2 175 tests  →  ~88s
#   shard2: ~1 870 tests  →  ~81s
#   shard3: ~2 505 tests  →  ~95s  ← projected new max
#   shard4: ~1 670 tests  →  ~77s  ← projected new min
#   shard5: ~2 315 tests  →  ~91s
#   shard6: ~2 175 tests  →  ~88s
#   Projected ratio: ~1.23x  (target ≤ 1.5x)
#
# Quarterly drift check: re-run this estimate after major test additions.
# If max/min exceeds 1.5x, update the mapping and record new baseline above.

set -euo pipefail

SHARD="${1:-}"
if [[ -z "$SHARD" ]]; then
    echo "Usage: $0 <shard_number>" >&2
    exit 1
fi

case "$SHARD" in
    # Integration first half: test_a* … test_l* plus the config sub-suite.
    # Glob patterns are intentionally not expanded here — the caller runs
    # `pytest $PATHS` (unquoted) so the shell expands them at invocation time.
    1) PATHS="tests/integration/test_[a-l]*.py tests/integration/config" ;;

    # Integration second half: test_m* … test_z* (covers methodologies, naming,
    # optimization, para, parallel, pattern, pipeline, rollback, search, services,
    # setup, state, tag, template, text-processing, undo, update, utility, watcher).
    2) PATHS="tests/integration/test_[m-z]*.py" ;;

    # Heavy services (intelligence + deduplication) combined with models and events
    # — these are the deepest sub-suites by test count; grouped to share overhead.
    3) PATHS="tests/services/intelligence tests/services/deduplication tests/models tests/events" ;;

    # Light services (audio, copilot, auto_tagging, analytics, video, search, root
    # test files) plus optimization — together they balance shard 3.
    # tests/services/test_*.py expands to the root-level service test files that
    # do not live in a named sub-package.
    4) PATHS="tests/services/audio tests/services/copilot tests/services/auto_tagging tests/services/analytics tests/services/video tests/services/search tests/services/test_*.py tests/optimization" ;;

    # CLI, methodologies, parallel workers, and pipeline stages — mid-weight suite
    # unchanged from the previous shard 3 mapping.
    5) PATHS="tests/cli tests/methodologies tests/parallel tests/pipeline" ;;

    # Guardrail / unit tests plus the small-to-medium suites that were previously
    # spread across shards 4-6 and under-loaded them.
    # tests/test_*.py expands to the root-level integration / smoke tests.
    6) PATHS="tests/ci tests/unit tests/utils tests/undo tests/history tests/daemon tests/watcher tests/updater tests/core tests/config tests/docs tests/integrations tests/interfaces tests/extras tests/test_*.py" ;;

    *)
        echo "Unknown shard: $SHARD (valid: 1-6)" >&2
        exit 1
        ;;
esac

export PATHS
echo "$PATHS"
