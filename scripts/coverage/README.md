# Integration Module Coverage Floors

This directory contains the per-module integration coverage baseline used by CI.

## Policy

1. The existing global integration gate stays in place (`coverage report --fail-under=71.9`).
2. A per-module gate enforces non-regression for Python modules under `src/file_organizer/` (including nested paths, e.g. `src/file_organizer/**/*.py`) seen in integration coverage output.
3. New modules must meet the same minimum bar (`71.9%` by default).

This makes coverage improvements durable and prevents hidden regressions in low-visibility modules.

## Files

- `integration_module_floor_baseline.json`: baseline module floors.
- `../check_module_coverage_floor.py`: checker invoked by `.github/workflows/ci.yml`.

## How to ratchet floors upward

### Automated (recommended)

Run the integration suite and pipe the output to the script with `--update-baseline`:

```bash
bash scripts/measure-integration-coverage.sh | tee /tmp/integration-report.txt

python3 scripts/check_module_coverage_floor.py \
  --report-path /tmp/integration-report.txt \
  --baseline-path scripts/coverage/integration_module_floor_baseline.json \
  --update-baseline
```

The script will:

- **Raise** floors for modules whose coverage has improved (never lowered — ratchet behaviour).
- **Add** new modules with their actual coverage as the floor.
- **Remove** modules that have been deleted from disk.
- Update `generated_at_utc` in the baseline JSON.

Preview changes first with `--dry-run` (prints the diff without writing):

```bash
python3 scripts/check_module_coverage_floor.py \
  --report-path /tmp/integration-report.txt \
  --baseline-path scripts/coverage/integration_module_floor_baseline.json \
  --update-baseline --dry-run
```

### Manual

1. Improve integration tests for target modules.
2. Run `bash scripts/measure-integration-coverage.sh | tee /tmp/report.txt`.
3. Edit `integration_module_floor_baseline.json` by hand, then commit.

When coverage improves materially, update the baseline in the same PR so future changes cannot regress.
