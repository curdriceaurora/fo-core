# Integration Module Coverage Floors

This directory contains the per-module integration coverage baseline used by CI.

## Policy

1. The existing global integration gate stays in place (`--cov-fail-under=71.9`).
2. A per-module gate enforces non-regression for every `src/file_organizer/*.py` module seen in integration coverage output.
3. New modules must meet the same minimum bar (`71.9%` by default).

This makes coverage improvements durable and prevents hidden regressions in low-visibility modules.

## Files

- `integration_module_floor_baseline.json`: baseline module floors.
- `../check_module_coverage_floor.py`: checker invoked by `.github/workflows/ci.yml`.

## How to ratchet floors upward

1. Improve integration tests for target modules.
2. Run the integration coverage command locally and capture term output.
3. Regenerate module floors from that run, then review and commit the updated JSON.

When coverage improves materially, update the baseline in the same PR so future changes cannot regress.
