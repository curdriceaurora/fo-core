# Type-Check Gate Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the strict mypy CI gate from `models/` only (1 package, 28 files) to 14 packages (164 files total) by gating 9 zero-error packages immediately and fixing 6 stale `# type: ignore` comments in 4 more packages. `models/` remains in the gate; the expansion adds 13 new packages (136 new files).

**Architecture:** Two sequential sub-tasks: (1) remove stale suppression comments from Tier 2 packages — all are `[unused-ignore]` one-liners with no logic change; (2) update the `type-check` CI job and `mypy-changed` pre-commit hook to cover all Tier 1+2 packages. Tier 3 (core, cli, watcher) and services are tracked in issue #93.

**Tech Stack:** mypy (strict mode, already configured in `pyproject.toml`), GitHub Actions `ci.yml`, `.pre-commit-config.yaml`

---

### Task 1: Remove stale `# type: ignore` comments from Tier 2 packages

All 6 errors are `[unused-ignore]` — the codebase was improved and the suppression comments
are no longer needed. Removing them is the entire fix; no logic changes.

**Files:**
- Modify: `src/file_organizer/methodologies/para/config.py:16`
- Modify: `src/file_organizer/integrations/obsidian.py:10`
- Modify: `src/file_organizer/utils/readers/cad.py:359`
- Modify: `src/file_organizer/utils/readers/__init__.py:153`
- Modify: `src/file_organizer/config/manager.py:24`
- Modify: `src/file_organizer/config/provider_env.py:36`

- [ ] **Step 1: Verify the current errors are exactly the 6 expected `[unused-ignore]` comments**

```bash
for f in \
  src/file_organizer/methodologies/para/config.py \
  src/file_organizer/integrations/obsidian.py \
  src/file_organizer/utils/readers/cad.py \
  src/file_organizer/utils/readers/__init__.py \
  src/file_organizer/config/manager.py \
  src/file_organizer/config/provider_env.py; do
  echo "=== $f ==="
  mypy "$f" --no-error-summary 2>/dev/null | grep "error:"
done
```

Expected: each file shows exactly one `Unused "type: ignore" comment  [unused-ignore]` line.

- [ ] **Step 2: Remove the stale comment from `methodologies/para/config.py:16`**

Read line 16 to see the full line, then remove only the `  # type: ignore` suffix:

```bash
sed -n '14,18p' src/file_organizer/methodologies/para/config.py
```

Edit the file: remove `  # type: ignore` (or `  # type: ignore[...]`) from the end of that line. The surrounding code is unchanged.

Verify:

```bash
mypy src/file_organizer/methodologies/ --no-error-summary 2>/dev/null | grep "error:" || echo "Clean"
```

Expected: `Clean`

- [ ] **Step 3: Remove the stale comment from `integrations/obsidian.py:10`**

```bash
sed -n '8,12p' src/file_organizer/integrations/obsidian.py
```

Remove the `  # type: ignore` suffix from line 10.

Verify:

```bash
mypy src/file_organizer/integrations/ --no-error-summary 2>/dev/null | grep "error:" || echo "Clean"
```

Expected: `Clean`

- [ ] **Step 4: Remove stale comments from `utils/readers/cad.py:359` and `utils/readers/__init__.py:153`**

```bash
sed -n '357,361p' src/file_organizer/utils/readers/cad.py
sed -n '151,155p' src/file_organizer/utils/readers/__init__.py
```

Remove the `  # type: ignore[no-any-return, operator]` suffix from each line.

Verify:

```bash
mypy src/file_organizer/utils/ --no-error-summary 2>/dev/null | grep "error:" || echo "Clean"
```

Expected: `Clean`

- [ ] **Step 5: Remove stale comments from `config/manager.py:24` and `config/provider_env.py:36`**

```bash
sed -n '22,26p' src/file_organizer/config/manager.py
sed -n '34,38p' src/file_organizer/config/provider_env.py
```

Remove the `  # type: ignore` suffix from each line.

Verify:

```bash
mypy src/file_organizer/config/ --no-error-summary 2>/dev/null | grep "error:" || echo "Clean"
```

Expected: `Clean`

- [ ] **Step 6: Confirm all 13 packages (Tier 1 + Tier 2) are now mypy-clean**

```bash
mypy \
  src/file_organizer/optimization/ \
  src/file_organizer/parallel/ \
  src/file_organizer/events/ \
  src/file_organizer/daemon/ \
  src/file_organizer/undo/ \
  src/file_organizer/history/ \
  src/file_organizer/interfaces/ \
  src/file_organizer/updater/ \
  src/file_organizer/pipeline/ \
  src/file_organizer/methodologies/ \
  src/file_organizer/integrations/ \
  src/file_organizer/utils/ \
  src/file_organizer/config/ \
  --no-error-summary 2>/dev/null | grep "error:" || echo "All 13 packages clean"
```

Expected: `All 13 packages clean`

- [ ] **Step 7: Run the existing test suite for the modified packages to confirm no regressions**

```bash
pytest tests/config/ tests/utils/ tests/integrations/ tests/methodologies/ \
  -v --timeout=30 --override-ini="addopts="
```

`--override-ini="addopts="` suppresses the repo-wide `--cov-fail-under=95` from
`pyproject.toml` so the run validates correctness only, not global coverage thresholds.

Expected: all tests pass.

- [ ] **Step 8: Commit the source fixes**

```bash
git add \
  src/file_organizer/methodologies/para/config.py \
  src/file_organizer/integrations/obsidian.py \
  src/file_organizer/utils/readers/cad.py \
  src/file_organizer/utils/readers/__init__.py \
  src/file_organizer/config/manager.py \
  src/file_organizer/config/provider_env.py
git commit -m "fix: remove 6 stale type: ignore comments from Tier 2 packages

All six were [unused-ignore] — the code was improved and the suppression
comments became stale. No logic changes.

Prepares methodologies, integrations, utils, config for strict mypy gate."
```

---

### Task 2: Expand the mypy gate in CI and pre-commit

**Files:**
- Modify: `.github/workflows/ci.yml` (type-check job)
- Modify: `.pre-commit-config.yaml` (mypy-changed hook)

- [ ] **Step 1: Read the current mypy step in ci.yml**

```bash
grep -n "mypy\|type-check\|type_check" .github/workflows/ci.yml | head -20
```

Find the step that runs `mypy src/file_organizer/models/` — note its line number.

- [ ] **Step 2: Replace the mypy command in ci.yml to cover all Tier 1+2 packages**

Find the line that reads:

```yaml
          mypy src/file_organizer/models/
```

Replace it with:

```yaml
          mypy \
            src/file_organizer/models/ \
            src/file_organizer/optimization/ \
            src/file_organizer/parallel/ \
            src/file_organizer/events/ \
            src/file_organizer/daemon/ \
            src/file_organizer/undo/ \
            src/file_organizer/history/ \
            src/file_organizer/interfaces/ \
            src/file_organizer/updater/ \
            src/file_organizer/pipeline/ \
            src/file_organizer/methodologies/ \
            src/file_organizer/integrations/ \
            src/file_organizer/utils/ \
            src/file_organizer/config/
```

- [ ] **Step 3: Verify the ci.yml change produces valid YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML OK"
```

Expected: `YAML OK`

- [ ] **Step 4: Read the current mypy-changed hook in .pre-commit-config.yaml**

```bash
grep -A 10 "mypy-changed\|mypy" .pre-commit-config.yaml
```

Find the hook entry — it currently passes `src/file_organizer/models/*.py` or similar.

- [ ] **Step 5: Update the mypy-changed hook to cover Tier 1+2 packages**

Find the hook entry that includes `src/file_organizer/models/` and update its `files` pattern to include all 13 gated packages. The pattern is a regex matching file paths:

Replace the existing `files:` value with a single-line pattern (do **not** use a folded
`>-` block — YAML folded scalars insert spaces between continuation lines, which would embed
literal spaces in the regex and silently break path matching):

```yaml
        files: ^src/file_organizer/(models|optimization|parallel|events|daemon|undo|history|interfaces|updater|pipeline|methodologies|integrations|utils|config)/
```

The regex is intentionally on one line so the value contains no whitespace, and the hook
triggers whenever any file under the 14 gated packages changes.

- [ ] **Step 6: Run the expanded mypy gate locally to confirm it passes**

```bash
mypy \
  src/file_organizer/models/ \
  src/file_organizer/optimization/ \
  src/file_organizer/parallel/ \
  src/file_organizer/events/ \
  src/file_organizer/daemon/ \
  src/file_organizer/undo/ \
  src/file_organizer/history/ \
  src/file_organizer/interfaces/ \
  src/file_organizer/updater/ \
  src/file_organizer/pipeline/ \
  src/file_organizer/methodologies/ \
  src/file_organizer/integrations/ \
  src/file_organizer/utils/ \
  src/file_organizer/config/
```

Expected: `Success: no issues found in N source files`

- [ ] **Step 7: Run pre-commit validation**

```bash
bash .claude/scripts/pre-commit-validation.sh
```

Expected: passes.

- [ ] **Step 8: Commit the CI and pre-commit changes**

```bash
git add .github/workflows/ci.yml .pre-commit-config.yaml
git commit -m "ci: expand strict mypy gate from 1 to 14 packages (164 files)

Adds 13 new packages to the existing models/ gate: optimization, parallel,
events, daemon, undo, history, interfaces, updater, pipeline (Tier 1 — zero
errors) and methodologies, integrations, utils, config (Tier 2 — stale
ignores removed in previous commit). Total gated: 164 files across 14 packages.

Tier 3 (core, cli, watcher) and services tracked in issue #93.

Closes workstream 3 of #92."
```
