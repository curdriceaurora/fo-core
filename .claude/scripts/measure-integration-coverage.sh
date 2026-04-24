#!/usr/bin/env bash
# Measure integration-test coverage against the same targets CI uses.
#
# Rebased to ``--cov=src`` after the flatten in PR #104 removed the
# top-level ``fo`` package. The previous ``--cov=fo`` path collected
# zero files because the package no longer exists, which silently
# masked per-module coverage drift — an entire class of D2-style
# regressions (PR #186) went undetected locally until main CI caught
# them.
#
# Always run this before opening a PR that changes code under ``src/``,
# per the MEMORY rule "Run integration gate locally before first push".
set -euo pipefail

# Install optional search extras so rank-bm25 / sklearn tests run (not skip).
# Uses --quiet to suppress pip noise; harmless if already installed.
pip install -e ".[search]" --quiet

coverage erase
pytest tests/ -m "integration" \
    --strict-markers \
    --cov=src \
    --cov-branch \
    --cov-report=term-missing \
    --override-ini="addopts=" \
    "$@"
