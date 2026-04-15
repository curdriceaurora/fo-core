# PR-time Integration Coverage Hard Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add integration coverage floor enforcement to PR CI so regressions are caught before merge, not after.

**Architecture:** Modify `.github/workflows/pr-integration.yml` to add `--cov` flags to the existing pytest run, then add three sequential gate steps that mirror `test-integration` in `ci.yml`: per-module floors via `scripts/check_module_coverage_floor.py`, global 71.9% floor, and an enforcer step that exits non-zero if either gate was breached.

**Tech Stack:** GitHub Actions, pytest-cov, `scripts/check_module_coverage_floor.py` (existing), `scripts/coverage/integration_module_floor_baseline.json` (existing, 287 module floors)

---

## Task 1: Add coverage measurement and gate steps to pr-integration.yml

**Files:**
- Modify: `.github/workflows/pr-integration.yml`

- [ ] **Step 1: Read the current workflow to understand the existing step structure**

```bash
cat .github/workflows/pr-integration.yml
```

Confirm the existing `Run integration tests` step has no `id:` field and no `--cov` flags.

- [ ] **Step 2: Replace the `Run integration tests` step with the gated version**

In `.github/workflows/pr-integration.yml`, replace the entire `- name: Run integration tests` block:

```yaml
      - name: Run integration tests
        # Runs ALL integration-marked tests (including those without the ci marker).
        # This catches test files added in PRs that target integration scenarios
        # but were never dual-tagged ci+integration.
        # -n=auto: xdist parallelises across available CPUs (~2 workers on ubuntu-latest).
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          pytest tests/ \
            --strict-markers \
            -m "integration and not benchmark" \
            --timeout=60 \
            -n=auto \
            --override-ini="addopts=" \
            -q
```

With:

```yaml
      - name: Run integration tests
        # --cov adds ~15-20% overhead to the 3-5 min run; bounded by cancel-in-progress.
        # Output teed to RUNNER_TEMP so the per-module floor script can parse it.
        id: integration_tests
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          set -o pipefail
          pytest tests/ \
            --strict-markers \
            -m "integration and not benchmark" \
            --cov=file_organizer \
            --cov-branch \
            --cov-report=term-missing \
            --cov-report=xml \
            --timeout=60 \
            -n=auto \
            --override-ini="addopts=" \
            | tee "$RUNNER_TEMP/integration-coverage-report.txt"

      - name: Check per-module integration coverage floors
        id: per_module_gate
        if: always() && steps.integration_tests.outcome != 'cancelled'
        continue-on-error: true
        run: |
          python scripts/check_module_coverage_floor.py \
            --report-path "$RUNNER_TEMP/integration-coverage-report.txt" \
            --baseline-path scripts/coverage/integration_module_floor_baseline.json

      - name: Check global integration coverage floor
        id: global_gate
        if: always() && steps.integration_tests.outcome != 'cancelled'
        continue-on-error: true
        run: coverage report --fail-under=71.9

      - name: Enforce integration gate outcomes
        if: always() && steps.integration_tests.outcome != 'cancelled'
        run: |
          if [ "${{ steps.integration_tests.outcome }}" != "success" ]; then
            echo "FAIL: integration tests failed before coverage gates could run"
            exit 1
          fi
          if [ "${{ steps.per_module_gate.outcome }}" != "success" ]; then
            echo "FAIL: per-module integration coverage gate failed"
            exit 1
          fi
          if [ "${{ steps.global_gate.outcome }}" != "success" ]; then
            echo "FAIL: global integration coverage floor failed (< 71.9%)"
            exit 1
          fi
          echo "OK: all integration coverage gates passed"
```

- [ ] **Step 3: Verify the file is valid YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/pr-integration.yml'))" && echo "YAML OK"
```

Expected output: `YAML OK`

- [ ] **Step 4a: Validate the PR-specific pytest invocation directly**

`scripts/run-local-ci.sh integration` mirrors the main CI `test-integration` job, which runs
sequentially and without `-m "integration and not benchmark"`. The PR workflow uses `-n=auto` and
excludes benchmarks. Validate the PR-specific shape explicitly first so the xdist + coverage
combination is confirmed locally before the harness run:

```bash
pip install -e ".[dev,search]" --quiet
set -o pipefail
pytest tests/ \
  --strict-markers \
  -m "integration and not benchmark" \
  --cov=file_organizer \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml \
  --timeout=60 \
  -n=auto \
  --override-ini="addopts=" \
  | tee /tmp/pr-integration-coverage-report.txt
```

Expected: pytest exits 0 (all integration tests pass) and coverage output is visible in
`/tmp/pr-integration-coverage-report.txt`. If tests fail here but not under
`run-local-ci.sh integration`, the failure is specific to the xdist + coverage combination
introduced by this PR and must be investigated before merging.

- [ ] **Step 4b: Verify the full integration coverage gate passes locally using the repo's maintained harness**

The repo provides `scripts/run-local-ci.sh` which runs the exact same commands as the main CI
`test-integration` job (pytest + per-module floor script + global floor). This confirms the
floor enforcement logic is unbroken:

```bash
bash scripts/run-local-ci.sh integration
```

Expected: `[PASS] Run integration coverage gates` — all three steps (pytest, per-module floor,
global 71.9% floor) succeed.

**If Step 4b reports floor failures:** Distinguish carefully before deciding how to proceed:

- Check whether Step 4a also failed (same tests, same coverage numbers). If Step 4a passed
  but Step 4b failed, the discrepancy is measurement noise from sequential vs parallel
  collection — document it in the PR description and do not block the merge.
- If both Step 4a and Step 4b fail, check whether the failures exist on current `main`:

  ```bash
  git stash
  bash scripts/run-local-ci.sh integration
  git stash pop
  ```

  - If the failure also appears on `main`: it is a **pre-existing main regression** — do not
    fix in this PR; open a separate issue.
  - If the failure only appears on your branch: it is a **regression introduced by this PR's
    changes** — investigate and fix before merging (this should not happen for a workflow-only
    change, but could indicate a coverage measurement difference introduced by adding `--cov`
    alongside `-n=auto` xdist).

- [ ] **Step 5: Update `docs/testing/testing-strategy.md` to reflect the new PR coverage enforcement**

The file at `docs/testing/testing-strategy.md` describes the CI tiers. The `Per-push integration`
row currently describes the PR integration job as running tests without mentioning coverage enforcement.
Update the table row and add a note below it:

In the **CI Strategy — Three Tiers** table, update the `Per-push integration` row's description
to include coverage floor enforcement:

```markdown
| Per-push integration | `pr-integration.yml` | Every push to PR branch (opened / reopened / ready-for-review / synchronize) | `integration and not benchmark` | ~4–6 min |
```

And add this note immediately after the table (before **Marker rules for new tests:**):

```markdown
> **PR integration coverage gate:** The `pr-integration.yml` workflow enforces the same
> integration coverage floors as main: 287 per-module floors via
> `scripts/check_module_coverage_floor.py` and a 71.9% global floor. A PR whose changes
> cause an integration coverage regression will fail the `Integration tests (PR)` check
> before merge. To reproduce locally: `bash scripts/run-local-ci.sh integration`.
```

- [ ] **Step 6: Run pre-commit validation**

```bash
bash .claude/scripts/pre-commit-validation.sh
```

Expected: passes.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/pr-integration.yml docs/testing/testing-strategy.md
git commit -m "ci: add integration coverage hard gate to PR workflow

Mirrors the test-integration job from ci.yml: adds --cov flags to the
integration test run, enforces 287 per-module floors via
check_module_coverage_floor.py, enforces the 71.9% global floor, and
fails the PR if either gate is breached.

Updates docs/testing/testing-strategy.md to describe the new enforcement.

Closes workstream 1 of #92."
```
