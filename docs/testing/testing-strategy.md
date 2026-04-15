# Testing Strategy

## CI Strategy — Three Tiers

| Tier | Workflow | Trigger | Marker filter | Approx time |
|------|----------|---------|--------------|-------------|
| Per-commit (fast) | `ci.yml` `test` job | Every push to PR branch | `ci and not benchmark` | ~2 min |
| Per-push integration | `pr-integration.yml` | Every push to PR branch (opened / reopened / ready-for-review / synchronize) | `integration and not benchmark` | ~4–6 min; coverage floor enforcement |
| Post-merge full | `ci.yml` `test-full` | Push to main | non-benchmark/non-e2e (6 shards × py3.11+3.12) | ~2–3 min/shard |
| Nightly matrix | `ci-full.yml` | Daily 06:00 UTC | Linux: full-suite (6 shards, py3.11+3.12); macOS + Windows: `ci/smoke` subset | ~15 min |

> **PR integration coverage gate:** The `pr-integration.yml` workflow enforces the same
> integration coverage floors as main: 287 per-module floors via
> `scripts/check_module_coverage_floor.py` and a 71.9% global floor. A PR whose changes
> cause an integration coverage regression will fail the `Integration tests (PR)` check
> before merge. To reproduce locally: `bash scripts/run-local-ci.sh integration`.

**Marker rules for new tests:**

- `@pytest.mark.ci` — runs on every PR push. Use for fast unit-style tests (no I/O, no external deps).
- `@pytest.mark.integration` — runs on every push to an open PR and on main push. Use when the test
  touches real files, DB, or service interactions. No need to also add `ci`; the integration
  workflow picks these up automatically.
- Dual-tagging `ci + integration` is **not required** and was a historical artefact — the
  pr-integration workflow supersedes that pattern.

## Running Tests

```bash
pytest                                             # Full test suite (local)
pytest --cov=file_organizer --cov-report=html      # With coverage
pytest tests/services/ -v                          # Specific directory
pytest -m smoke -x                                 # Fast smoke suite (~3.5s, pre-commit)
pytest -m "ci and not benchmark"                   # Mirrors per-commit CI gate
pytest -m "integration and not benchmark"          # Mirrors PR integration gate
pytest -m "not regression" -x                      # All tests except regression
pytest -k "backup or dedup"                        # Filter by name
```

## Test Markers

```python
@pytest.mark.unit          # Unit tests
@pytest.mark.smoke         # Fast critical-path tests (pre-commit validation)
@pytest.mark.integration   # Integration tests — real file I/O, DB, service interactions
@pytest.mark.e2e           # End-to-end tests against real file trees
@pytest.mark.asyncio       # Async tests (services)
@pytest.mark.benchmark     # Performance benchmarks
@pytest.mark.ci            # Per-commit fast gate (kept small — no I/O, no external deps)
@pytest.mark.slow          # Slow tests
@pytest.mark.regression    # Regression tests (full suite only)
@pytest.mark.no_ollama     # Tests that verify fallback behavior when Ollama is unavailable
@pytest.mark.playwright    # Browser-based E2E tests (requires: playwright install chromium; run with --override-ini='addopts=')

def test_example():
    pass
```

## Coverage Goals

### Current Status (Epic #571 Complete)

- **Overall**: 96.8% docstring coverage | 916+ tests
- **API Module**: 92% code coverage ✅
- **Services**: 82% code coverage ✅
- **Models**: 90% code coverage ✅
- **CI Gate**: 95% minimum (coverage requirement, enforced on main pushes)

### Coverage Targets by Module

| Module | Target | Current | Status |
|--------|--------|---------|--------|
| services | 80% | 82% | ✅ +2% |
| models | 90% | 90% | ✅ Met |
| cli | 80% | 75% | 🔶 -5% |
| config | 90% | 95% | ✅ +5% |

See [Coverage Report](coverage-report.md) for detailed metrics by module.

---
