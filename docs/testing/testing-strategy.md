# Testing Strategy

## CI Strategy — Three Tiers

| Tier | Workflow | Trigger | Marker filter | Approx time |
|------|----------|---------|--------------|-------------|
| Per-commit (fast) | `ci.yml` `test` job | Every push to PR branch | `ci and not benchmark` | ~2 min |
| Per-PR lifecycle | `pr-integration.yml` | PR opened / ready-for-review | `integration and not benchmark` | ~3–5 min |
| Post-merge full | `ci.yml` `test-full` | Push to main | all (6 shards × py3.11+3.12) | ~2–3 min/shard |
| Nightly matrix | `ci-full.yml` | Daily 06:00 UTC | Linux: full-suite (6 shards, py3.11+3.12); macOS + Windows: `ci/smoke` subset | ~15 min |

**Marker rules for new tests:**

- `@pytest.mark.ci` — runs on every PR push. Use for fast unit-style tests (no I/O, no external deps).
- `@pytest.mark.integration` — runs at PR open/ready gates and on main push. Use when the test
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
