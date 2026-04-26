# Integration Module Coverage Floors

This directory contains the per-module integration coverage baseline used by CI.

## Policy

1. The existing global integration gate stays in place (`coverage report --fail-under=71.9`).
2. A per-module gate enforces non-regression for Python modules under `src/` (including nested paths, e.g. `src/**/*.py`) seen in integration coverage output.
3. New modules must meet the same minimum bar (`71.9%` by default).

This makes coverage improvements durable and prevents hidden regressions in low-visibility modules.

## Files

- `integration_module_floor_baseline.json`: baseline module floors.
- `../check_module_coverage_floor.py`: checker invoked by `.github/workflows/ci.yml`.

## Known local drift

Some modules measure slightly lower locally than in CI because their branch
coverage depends on which optional extras are installed.  These modules are
listed in the `known_local_drift.modules` section of the baseline JSON.

When `check_module_coverage_floor.py` runs **without** the `CI` environment
variable set to `true`, it skips the floor check for those modules and prints a
`NOTE:` line explaining why.  CI (where `CI=true` is always set by GitHub
Actions) continues to enforce the full floors.

To add a new module to the allowlist:

```json
"known_local_drift": {
  "modules": {
    "src/path/to/module.py": "one-line reason why coverage differs by env"
  }
}
```

Only use this for genuine environment-specific drift (platform branches,
optional-dep import guards).  Do **not** use it to hide coverage regressions.

## How to ratchet floors upward

### Single command (recommended)

Run from the repository root:

```bash
bash scripts/coverage/ratchet.sh check    # gate-check (mirrors CI)
bash scripts/coverage/ratchet.sh update   # ratchet baseline upward
bash scripts/coverage/ratchet.sh dry-run  # preview changes without writing
```

The script will:

- **Raise** floors for modules whose coverage has improved (never lowered — ratchet behaviour).
- **Add** new modules with their actual coverage as the floor.
- **Remove** modules that have been deleted from disk.
- Update `generated_at_utc` in the baseline JSON.

After improving tests, run `update` and commit the updated baseline file.

### Manual

1. Improve integration tests for target modules.
2. Run `bash .claude/scripts/measure-integration-coverage.sh | tee /tmp/report.txt`.
3. Edit `integration_module_floor_baseline.json` by hand, then commit.

When coverage improves materially, update the baseline in the same PR so future changes cannot regress.
