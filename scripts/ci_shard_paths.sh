#!/usr/bin/env bash
# ci_shard_paths.sh — single source of truth for the 16-shard test split.
#
# Usage: bash scripts/ci_shard_paths.sh <shard_number>
# Prints a space-separated list of pytest path arguments to stdout.
#
# Both ci.yml (push) and ci-full.yml (daily) import this script so the mapping
# stays in sync. When test directories are added or moved, update ONLY this
# file; both pipelines will pick up the change automatically.
#
# Shard sizing goal: ~3 000-4 500 test functions per shard, keeping per-xdist-worker
# RAM below the threshold that triggers Python GC-finaliser hangs (~1.3 GB/worker).
# The API and web suites run in-process because those tests create many async
# HTTP-client objects that accumulate in GC differently from synchronous tests.
# The API suite is split into one coarse shard plus tiny in-process shards for
# the historically sticky second half. Shard 10 is further broken into single
# files so the next CI run can identify the exact hanging test module.

set -euo pipefail

SHARD="${1:-}"
if [[ -z "$SHARD" ]]; then
    echo "Usage: $0 <shard_number>" >&2
    exit 1
fi

case "$SHARD" in
    1) PATHS="tests/integration tests/e2e" ;;
    2) PATHS="tests/services tests/models tests/events tests/optimization" ;;
    3)
        PATHS="\
tests/api/test_analyze_router.py \
tests/api/test_api_keys.py \
tests/api/test_api_server_config.py \
tests/api/test_api_utils_coverage.py \
tests/api/test_auth.py \
tests/api/test_auth_models.py \
tests/api/test_auth_rate_limit.py \
tests/api/test_auth_rate_limit_coverage.py \
tests/api/test_auth_router.py \
tests/api/test_auth_router_coverage.py \
tests/api/test_auth_store.py \
tests/api/test_auth_store_coverage.py \
tests/api/test_cache.py \
tests/api/test_cache_thread_safety.py \
tests/api/test_config_router.py \
tests/api/test_daemon_router.py \
tests/api/test_database.py \
tests/api/test_db_models.py \
tests/api/test_db_module.py \
tests/api/test_dedupe_router.py \
tests/api/test_dependencies.py \
tests/api/test_exceptions.py \
tests/api/test_file_metadata_repo.py \
tests/api/test_files_router.py"
        ;;
    4) PATHS="tests/cli tests/methodologies tests/ci tests/unit tests/plugins tests/tui tests/parallel tests/pipeline" ;;
    5) PATHS="tests/utils tests/undo tests/history tests/daemon tests/deploy tests/watcher tests/updater tests/core tests/config tests/client tests/docs tests/desktop tests/integrations tests/interfaces tests/test_*.py" ;;
    6) PATHS="tests/web" ;;
    7)
        PATHS="\
tests/api/test_health_endpoint.py \
tests/api/test_health_router.py \
tests/api/test_integrations_router.py"
        ;;
    8)
        PATHS="\
tests/api/test_job_repo.py \
tests/api/test_jobs.py \
tests/api/test_main_app.py"
        ;;
    9)
        PATHS="\
tests/api/test_marketplace_router.py \
tests/api/test_middleware.py \
tests/api/test_organize_router.py"
        ;;
    10)
        PATHS="\
tests/api/test_rate_limit.py"
        ;;
    11)
        PATHS="\
tests/api/test_realtime.py"
        ;;
    12)
        PATHS="\
tests/api/test_realtime_router.py"
        ;;
    13)
        PATHS="\
tests/api/test_realtime_router_coverage.py \
tests/api/test_realtime_ws_coverage.py \
tests/api/test_search.py"
        ;;
    14)
        PATHS="\
tests/api/test_search_router.py \
tests/api/test_service_facade.py \
tests/api/test_service_facade_coverage.py"
        ;;
    15)
        PATHS="\
tests/api/test_session_repo.py \
tests/api/test_settings_repo.py \
tests/api/test_system_router.py"
        ;;
    16)
        PATHS="\
tests/api/test_utils.py \
tests/api/test_workspace_repo.py"
        ;;
    *)
        echo "Unknown shard: $SHARD (valid: 1-16)" >&2
        exit 1
        ;;
esac

export PATHS
echo "$PATHS"
