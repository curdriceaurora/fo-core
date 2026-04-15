# xdist Hardening Audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit the test suite for xdist parallelism races, fix confirmed failures, write a guidance doc, and decide whether to re-enable xdist for the `test-full` job.

**Architecture:** Four phases: (1) create and run an audit script 3× to surface non-deterministic failures, (2) document findings, (3) fix only confirmed failures using the minimal safe pattern for each, (4) conditionally re-enable xdist in `ci.yml` if root causes are resolved. End with a guidance doc in `.claude/rules/`.

**Context:** The `test-full` job was switched from xdist to a 6-directory-shard matrix as an emergency workaround. The comment in `ci.yml` says "all shards can use xdist again instead of the emergency micro-shard matrix." Two recent races are already fixed: config dir shared state (#91) and sklearn `sys.modules` leak (`4f47168`). This audit finds what remains.

**Tech Stack:** pytest-xdist (`-n auto`, `--dist=loadgroup`), `unittest.mock.patch.dict`, `pytest.mark.xdist_group`

---

### Task 1: Create the xdist audit script

**Files:**
- Create: `scripts/ci/run-xdist-audit.sh`

- [ ] **Step 1: Write the audit script**

Create `scripts/ci/run-xdist-audit.sh`:

```bash
#!/usr/bin/env bash
# run-xdist-audit.sh — Run the non-integration, non-benchmark suite 3 times
# under xdist parallelism and report any non-deterministic failures.
#
# Usage: bash scripts/ci/run-xdist-audit.sh [output-dir]
# Output: per-run logs in OUTPUT_DIR, summary printed to stdout.

set -euo pipefail

OUTPUT_DIR="${1:-/tmp/xdist-audit-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUTPUT_DIR"

echo "=== xdist audit — 3 runs — output: $OUTPUT_DIR ==="

FAILED_RUNS=0
for i in 1 2 3; do
    echo ""
    echo "--- Run $i/3 ---"
    LOG="$OUTPUT_DIR/run-$i.txt"
    # pytest exits non-zero on test failures; capture it so we can continue
    pytest tests/ \
        -m "not integration and not benchmark and not e2e" \
        -n auto \
        --timeout=30 \
        -q \
        --tb=short \
        --override-ini="addopts=" \
        2>&1 | tee "$LOG" || true

    FAILURES=$(grep -c "^FAILED\|^ERROR" "$LOG" || true)
    echo "Run $i: $FAILURES failure(s)/error(s)"
    if [ "$FAILURES" -gt 0 ]; then
        FAILED_RUNS=$((FAILED_RUNS + 1))
    fi
done

echo ""
echo "=== Summary ==="
echo "Runs with failures: $FAILED_RUNS/3"
echo ""
echo "=== Tests that failed in ANY run (sorted by frequency) ==="
grep -h "^FAILED\|^ERROR" "$OUTPUT_DIR"/run-*.txt 2>/dev/null \
    | sort | uniq -c | sort -rn \
    || echo "(no failures found)"

echo ""
echo "=== Full logs in: $OUTPUT_DIR ==="
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/ci/run-xdist-audit.sh
```

- [ ] **Step 3: Commit the script before running it**

```bash
git add scripts/ci/run-xdist-audit.sh
git commit -m "ci: add xdist audit script for parallelism race detection"
```

---

### Task 2: Run the audit and document findings

**Files:**
- Create: `docs/internal/xdist-audit-2026-04-15.md`

- [ ] **Step 1: Install dependencies, download NLTK data, and run the audit**

The `test-full` CI job (which the audit mimics) installs `[dev,search]`, downloads NLTK data,
and exports `GITHUB_TOKEN`. Omitting these can cause failures that are not xdist races —
NLTK lookups error on missing corpora, and some tests branch on `GITHUB_TOKEN` presence.

```bash
pip install -e ".[dev,search]" --quiet
python -c "import nltk; nltk.download('stopwords', quiet=True); nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True); nltk.download('wordnet', quiet=True)"
export GITHUB_TOKEN="${GITHUB_TOKEN:-dummy-token-for-local-audit}"
bash scripts/ci/run-xdist-audit.sh /tmp/xdist-audit-results
```

Expected runtime: ~15–20 minutes (3 × ~5 min suite). Let it complete all 3 runs even if failures appear in run 1.

- [ ] **Step 2: Identify confirmed flakes (failures appearing in 2+ runs)**

```bash
grep -h "^FAILED\|^ERROR" /tmp/xdist-audit-results/run-*.txt \
    | sort | uniq -c | sort -rn | head -30
```

Any test appearing in 2+ runs is a confirmed xdist flake. Tests appearing only once may be genuine test failures unrelated to parallelism — check them but don't assume they're xdist races.

- [ ] **Step 3: For each confirmed flake, identify the root cause**

For each failing test path, check:

```bash
# What does the test use?
grep -n "os.environ\|monkeypatch\|tmp_path\|sys.modules\|module-level\|singleton\|@lru_cache\|DEFAULT_\|_registry\|conftest" \
    <failing-test-file.py>

# Does a conftest fixture affect isolation?
# Check tests/conftest.py, tests/<subdir>/conftest.py
```

Root cause categories:
- **env race**: test sets `os.environ` directly (not via monkeypatch); under xdist each worker
  is a separate process so the mutation is **not** visible to other workers, but it is visible
  to later tests running sequentially **in the same worker process** after the mutating test —
  if that later test expects the variable to be absent or to have a different value, it races
- **sys.modules leak**: a mock of an optional dep leaves `sys.modules` entries after teardown — fix with `patch.dict(sys.modules, ...)`
- **shared singleton**: a module-level object (e.g. `_registry`) is mutated by one test and read by another — fix with `@pytest.mark.xdist_group(name="<group>")` **and `--dist=loadgroup`** in the pytest command
- **tmp_path collision**: two tests write to the same non-`tmp_path` path — fix by using `tmp_path` fixture
- **teardown crash**: a fixture finalizer fails because another worker already cleaned up a shared resource — fix by narrowing fixture scope

- [ ] **Step 4: Write the findings document**

Create `docs/internal/xdist-audit-2026-04-15.md`:

```markdown
# xdist Audit Findings — 2026-04-15

## Setup

- Command: `bash scripts/ci/run-xdist-audit.sh`
- Suite: non-integration, non-benchmark, non-e2e (`-n auto`, `--timeout=30`)
- Runs: 3

## Results

<!-- Fill in from audit output -->

### Confirmed flakes (appeared in 2+ runs)

| Test | Runs failed | Root cause category | Fix applied |
|------|-------------|---------------------|-------------|
| (list from grep output) | | | |

### Single-run failures (not confirmed xdist flakes)

| Test | Notes |
|------|-------|
| (list) | |

## Historical context

Two races fixed before this audit:
- PR #91 (`821d5bf`): config dir shared state — `test_config_edit_persists_text_model`
  used module-level `DEFAULT_CONFIG_DIR`; fixed with `monkeypatch.setattr`
- `4f47168`: sklearn `sys.modules` leak — `DocumentEmbedder` mock left
  `sklearn.feature_extraction` and `sklearn.feature_extraction.text` pointing at
  mock after teardown; fixed with `patch.dict(sys.modules, ...)`

## Re-enablement decision

<!-- Fill in after fixes: was xdist re-enabled for test-full? -->
```

Commit the findings document even if the table is partially filled — it serves as the audit record.

- [ ] **Step 5: Commit the document stub**

```bash
git add docs/internal/xdist-audit-2026-04-15.md
git commit -m "docs: add xdist audit findings document (in progress)"
```

---

### Task 3: Fix each confirmed flake

**Files:** Varies per flake — typically test files under `tests/`

For each confirmed flake, apply the minimal fix matching its root cause:

- [ ] **Fix pattern A — sys.modules leak**

Replace any fixture that manually saves/restores `sys.modules` entries:

```python
# BAD — try/finally leaves sub-modules if an exception interrupts teardown
@pytest.fixture
def mock_sklearn(monkeypatch):
    real = sys.modules.get("sklearn")
    sys.modules["sklearn"] = MagicMock()
    yield
    if real is None:
        del sys.modules["sklearn"]
    else:
        sys.modules["sklearn"] = real
```

With `patch.dict` which restores ALL touched keys atomically:

```python
# GOOD — patch.dict restores all three entries on context exit
@pytest.fixture
def mock_sklearn():
    mock = MagicMock()
    with patch.dict(sys.modules, {
        "sklearn": mock,
        "sklearn.feature_extraction": mock.feature_extraction,
        "sklearn.feature_extraction.text": mock.feature_extraction.text,
    }):
        yield mock
```

After each fix, run the affected test file in isolation to confirm it passes:

```bash
pytest <affected-test-file.py> -v --override-ini="addopts="
```

(`--override-ini="addopts="` suppresses the `--cov-fail-under=95` injected by `pyproject.toml`
so the single-file run does not fail for unrelated coverage reasons.)

- [ ] **Fix pattern B — xdist_group for shared singletons**

For tests that mutate a module-level singleton (e.g. a registry or cache):

```python
# Add to each test class or function that mutates the singleton
@pytest.mark.xdist_group(name="provider-registry")
class TestProviderRegistry:
    ...
```

Then ensure the pytest invocation that runs these tests uses `--dist=loadgroup`:

```bash
# In the relevant CI step or local run command
pytest tests/path/to/test_registry.py --dist=loadgroup -n auto
```

If the affected tests are part of the integration suite (run via `pr-integration.yml`), add `--dist=loadgroup` to that workflow's pytest command as well.

- [ ] **Fix pattern C — direct os.environ mutation**

```python
# BAD — mutates process-level env; leaks to later tests in the same worker process
os.environ["FO_PROVIDER"] = "openai"
# ... test ...
del os.environ["FO_PROVIDER"]
```

```python
# GOOD — monkeypatch is automatically scoped and cleaned up per test
def test_something(monkeypatch):
    monkeypatch.setenv("FO_PROVIDER", "openai")
    # ... test ...
```

- [ ] **Step: After all fixes, re-run the audit to confirm zero confirmed flakes**

```bash
bash scripts/ci/run-xdist-audit.sh /tmp/xdist-audit-after-fixes
grep -h "^FAILED\|^ERROR" /tmp/xdist-audit-after-fixes/run-*.txt \
    | sort | uniq -c | sort -rn \
    || echo "No failures — audit clean"
```

Expected: `No failures — audit clean` (or only single-run failures unrelated to xdist).

- [ ] **Step: Commit all fixes**

```bash
git add tests/  # add only the changed test files
git commit -m "test: fix xdist parallelism races found in audit

<list each test and the fix applied>

Part of workstream 4 of #92."
```

---

### Task 4: Update the findings document

**Files:**
- Modify: `docs/internal/xdist-audit-2026-04-15.md`

- [ ] **Step 1: Fill in the confirmed-flakes table with actual findings and fixes applied**

Edit the table in `docs/internal/xdist-audit-2026-04-15.md` with real data from the audit run.

- [ ] **Step 2: Record the re-enablement decision**

If all confirmed flakes are fixed AND the after-fixes audit is clean:

Edit `docs/internal/xdist-audit-2026-04-15.md` — set the re-enablement decision to:
> "xdist re-enabled for test-full and nightly in this PR — see ci.yml and ci-full.yml changes"

Then update **both** workflow files that carry the 6-shard fallback. Each has the comment
*"all shards can use xdist again instead of the emergency micro-shard matrix"* and must be
updated together so the Linux nightly and the main push use the same concurrency shape.

**In `.github/workflows/ci.yml`** — find the `Test (shard ${{ matrix.shard }}, ...)` job
and replace its matrix and test step. Keep the existing pinned SHA action refs exactly as
they appear in the current file (do not introduce new action versions):

```yaml
  test-full:
    name: "Test full suite (py${{ matrix.python-version }})"
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405  # v6.2.0
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - name: Install dependencies
        run: |
          pip install -e ".[dev,search]"
          pip install "pytest-asyncio>=0.23.0" faker --no-cache-dir
      - name: Cache NLTK data
        uses: actions/cache@v5
        with:
          path: ~/nltk_data
          key: nltk-${{ runner.os }}-${{ hashFiles('**/pyproject.toml') }}
          restore-keys: nltk-${{ runner.os }}-
      - name: Download NLTK data
        run: python -c "import nltk; nltk.download('stopwords', quiet=True); nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True); nltk.download('wordnet', quiet=True)"
      - name: Run full test suite
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          pytest tests/ \
            -m "not benchmark and not e2e" \
            --strict-markers \
            -n auto \
            --timeout=60 \
            --cov=file_organizer \
            --cov-branch \
            --cov-report=xml \
            --override-ini="addopts="
          mv .coverage .coverage.py${{ matrix.python-version }}
      - name: Upload coverage data
        uses: actions/upload-artifact@v4
        with:
          name: coverage-${{ matrix.python-version }}
          path: .coverage.py${{ matrix.python-version }}
          include-hidden-files: true
          retention-days: 1
```

**In `.github/workflows/ci-full.yml`** — find the `test-linux-full` job and replace its
matrix and test step. Keep the existing `@v6` / `@v7.0.1` action refs that are already in
that file (do not pin to SHAs — that would introduce unrelated workflow churn):

```yaml
  test-linux-full:
    name: "Test Linux (py${{ matrix.python-version }})"
    runs-on: ubuntu-latest
    timeout-minutes: 20
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - name: Install dependencies
        run: |
          pip install -e ".[dev,search]"
          pip install "pytest-asyncio>=0.23.0" faker --no-cache-dir
      - name: Cache NLTK data
        uses: actions/cache@v5
        with:
          path: ~/nltk_data
          key: nltk-${{ runner.os }}-${{ hashFiles('**/pyproject.toml') }}
          restore-keys: nltk-${{ runner.os }}-
      - name: Download NLTK data
        run: python -c "import nltk; nltk.download('stopwords', quiet=True); nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True); nltk.download('wordnet', quiet=True)"
      - name: Run full test suite
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          pytest tests/ \
            -m "not benchmark and not e2e" \
            --strict-markers \
            -n auto \
            --timeout=60 \
            --cov=file_organizer \
            --cov-report= \
            --override-ini="addopts="
          mv .coverage .coverage.py${{ matrix.python-version }}
      - name: Upload coverage data
        uses: actions/upload-artifact@v7.0.1
        with:
          name: daily-coverage-${{ matrix.python-version }}
          path: .coverage.py${{ matrix.python-version }}
          include-hidden-files: true
          retention-days: 1
```

**Also update both downstream coverage-gate jobs** — the test-full / test-linux-full
rewrites change the artifact names (from `coverage-<ver>-shard-<N>` / `daily-coverage-<ver>-<N>`
to `coverage-<ver>` / `daily-coverage-<ver>`) and the file names (from `.coverage.shard-*` to
`.coverage.py*`). The coverage-gate jobs in both workflow files must be updated together or the
gate will find no artifacts and `coverage combine` will fail.

In **`ci.yml`** find the `coverage-gate` job (currently `needs: test-full`) and update two lines:

```yaml
      - name: Download coverage data (py3.11 shards)
        uses: actions/download-artifact@v8
        with:
          pattern: coverage-3.11    # was: coverage-3.11-*
          merge-multiple: true
      - name: Combine and enforce gate
        run: |
          coverage combine .coverage.py*    # was: .coverage.shard-*
          coverage report --fail-under=93
          coverage xml
```

In **`ci-full.yml`** find the `coverage-gate` job (currently `needs: test-linux-full`) and update
two lines:

```yaml
      - name: Download coverage data (py3.11 shards)
        uses: actions/download-artifact@v8
        with:
          pattern: daily-coverage-3.11    # was: daily-coverage-3.11-*
          merge-multiple: true
      - name: Combine and enforce gate
        run: |
          coverage combine .coverage.py*    # was: .coverage.shard-*
          coverage report --fail-under=93
```

**If the audit finds unfixed flakes** — set the re-enablement decision to:
> "Deferred — N flakes remain unfixed (see table). Follow-up issue: #<new-issue>"

Leave both shard matrices unchanged.

- [ ] **Step 3: Commit the updated document (and workflow changes if applicable)**

```bash
git add docs/internal/xdist-audit-2026-04-15.md
# If re-enabling xdist:
# git add .github/workflows/ci.yml .github/workflows/ci-full.yml
git commit -m "docs: complete xdist audit findings and re-enablement decision

Part of workstream 4 of #92."
```

---

### Task 5: Write the xdist-safe patterns guidance doc

**Files:**
- Create: `.claude/rules/xdist-safe-patterns.md`

- [ ] **Step 1: Create the guidance doc**

Create `.claude/rules/xdist-safe-patterns.md`:

```markdown
# xdist-Safe Test Patterns

**Purpose**: Prevent parallelism races when tests run with pytest-xdist (`-n auto`).

fo-core runs integration tests on PRs with `-n=auto` (xdist). This doc records
patterns that are safe, unsafe, and how to fix each.

---

## Pattern 1: Environment Variables

**Unsafe**: Direct `os.environ` mutation leaks to other tests in the same worker.

```python
# BAD
os.environ["FO_PROVIDER"] = "openai"
# test ...
del os.environ["FO_PROVIDER"]
```

**Safe**: `pytest.MonkeyPatch` is function-scoped and always restores the original value.

```python
# GOOD
def test_something(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FO_PROVIDER", "openai")
    # test ...
```

---

## Pattern 2: Optional Dependency Mocking (sys.modules)

**Unsafe**: Manual save/restore of `sys.modules` leaves sub-module keys behind if an
exception interrupts teardown (e.g. `sys.modules["sklearn"]` restored but
`sys.modules["sklearn.feature_extraction"]` still points at the mock).

```python
# BAD
real = sys.modules.get("sklearn")
sys.modules["sklearn"] = MagicMock()
yield
sys.modules["sklearn"] = real  # sub-modules not restored
```

**Safe**: `patch.dict` restores ALL listed keys atomically on context exit — including
on exceptions.

```python
# GOOD
from unittest.mock import patch

@pytest.fixture
def mock_sklearn() -> Generator[MagicMock, None, None]:
    mock = MagicMock()
    with patch.dict(sys.modules, {
        "sklearn": mock,
        "sklearn.feature_extraction": mock.feature_extraction,
        "sklearn.feature_extraction.text": mock.feature_extraction.text,
    }):
        yield mock
```

**Rule**: When mocking a package with sub-modules, list ALL sub-module keys that the
code under test imports, not just the top-level key.

---

## Pattern 3: Shared Singletons

**Unsafe**: Tests that mutate a module-level singleton (e.g. a registry or cache)
without cleanup can affect other tests in the same worker process.

**Safe option A** — use `xdist_group` to serialize tests that share the singleton.
**Requires `--dist=loadgroup`** in the pytest invocation — without this flag the
marker is parsed but provides NO serialization guarantee.

```python
@pytest.mark.xdist_group(name="provider-registry")
class TestProviderRegistryMutation:
    ...
```

Run with: `pytest ... --dist=loadgroup -n auto`

**Safe option B** — add a cleanup fixture that resets the singleton after each test.

```python
@pytest.fixture(autouse=True)
def reset_registry() -> Generator[None, None, None]:
    yield
    _registry._reset_for_testing()
```

---

## Pattern 4: File System

**Unsafe**: Writing to a hardcoded path (e.g. `/tmp/test_output`) causes collisions
when multiple workers run simultaneously.

```python
# BAD
Path("/tmp/test_output").write_text("result")
```

**Safe**: `tmp_path` gives each test a unique directory automatically.

```python
# GOOD
def test_something(tmp_path: Path) -> None:
    output = tmp_path / "test_output"
    output.write_text("result")
```

---

## Pattern 5: Config Directory Isolation

The `DEFAULT_CONFIG_DIR` module-level constant evaluates to `~/.config/file-organizer`
at import time. Tests that invoke CLI commands without overriding it read and write the
real user config — a cross-test race under xdist.

**Fix**: Use `monkeypatch.setattr` to point it at `tmp_path` per test.

```python
def test_config_edit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("file_organizer.config.manager.DEFAULT_CONFIG_DIR", tmp_path)
    runner.invoke(app, ["config", "edit", "--text-model", "llama3.2:3b"])
    result = runner.invoke(app, ["config", "show"])
    assert "llama3.2:3b" in result.output
```

The integration test conftest already provides `_isolate_user_env(tmp_path)` as an
`autouse` fixture that sets `HOME` and `XDG_*` dirs — use it for integration tests.
For unit tests, use `monkeypatch.setattr` on `DEFAULT_CONFIG_DIR` directly.

---

## Module-level CliRunner

Module-level `runner = CliRunner()` instances are **safe** under xdist because each
xdist worker loads the module independently and `CliRunner.invoke()` is stateless.
However, per-test `@pytest.fixture` runners are preferred for explicitness:

```python
@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()
```

---

**Last Updated**: 2026-04-15
**Status**: Active
**Related**: `tests/integration/conftest.py` (`_isolate_user_env`), issue #92 workstream 4
```

- [ ] **Step 2: Run pre-commit validation**

```bash
bash .claude/scripts/pre-commit-validation.sh
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add .claude/rules/xdist-safe-patterns.md
git commit -m "docs: add xdist-safe test patterns guidance doc

Documents the five race-prone patterns found in the fo-core test suite
and their safe counterparts. Includes the --dist=loadgroup requirement
for xdist_group markers.

Closes workstream 4 of #92."
```
