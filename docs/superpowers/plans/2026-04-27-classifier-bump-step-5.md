# Classifier Bump to Beta (Step 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bump fo-core from `2.0.0-alpha.3` / `Development Status :: 3 - Alpha` to `2.0.0-beta.1` / `Development Status :: 4 - Beta`. Open the public pre-release channel. Add the Beta bug-report template. Cut the release.

**Prerequisites:** Steps 1–4 must merge first. This plan validates that every entry-checklist row in `docs/release/beta-criteria.md` §2 is closed before flipping the classifier.

**Architecture:** Three changes — a version bump in `pyproject.toml`, a CHANGELOG entry summarizing what beta promises, and a new GitHub Issue Template. Plus a release run that produces the `2.0.0-beta.1` tag and PyPI artifact.

**Tech Stack:** `pyproject.toml`, GitHub release workflow (existing), `gh release create`.

**Out of scope:** Renaming `2.0.0-alpha.X` releases. Restructuring the changelog. Anything that the beta-criteria doc explicitly defers.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Bump `version` and `Development Status` classifier |
| `CHANGELOG.md` | Modify | Add a `## [2.0.0-beta.1]` section summarizing the beta-readiness work |
| `.github/ISSUE_TEMPLATE/beta-bug.md` | Create | Beta-specific bug template requiring `--debug` output and `fo doctor` summary |
| `README.md` | Modify | Update the Releases section to reflect beta status |
| `tests/ci/test_release_metadata.py` | Create | Guard test asserting version + classifier match each other |

Plan conventions: see [2A plan](2026-04-27-audio-model-wiring-2a.md) "Conventions for this plan" section.

---

## Task 1: Pre-bump audit — verify every entry-checklist row is closed

This task ships no code. It's a hard gate before any version bump.

**Files:** none (audit only)

- [ ] **Step 1: Walk the §2 entry checklist**

Open `docs/release/beta-criteria.md` and tick each row:

- [ ] **Audio works end-to-end.** Verify: `pytest tests/integration/test_audio_model_integration.py tests/integration/test_organize_audio_e2e.py -v` passes. `grep "NotImplementedError" src/models/audio_model.py` returns no matches. README and `pyproject.toml` `[media]` description match the wired surface.

- [ ] **Integration coverage floors.** Verify: `bash .claude/scripts/measure-integration-coverage.sh` reports global ≥ 75%, and `scripts/coverage/integration_module_floor_baseline.json` shows ≥ 70% for every module previously below.

- [ ] **Daemon smoke test in CI.** Verify: `tests/integration/test_daemon_smoke.py` exists and is green in the latest `test-integration` CI run on main.

- [ ] **`--debug` flag wired.** Verify: `fo --debug version` runs without error; an intentional failure (`fo --debug organize /nonexistent /nonexistent`) prints a Rich traceback. Documented in `docs/troubleshooting.md`.

- [ ] **Doc-honesty pass.** Verify: every command in `docs/cli-reference.md` is registered in `src/cli/main.py`. Every extra in the README's Optional Feature Packs table maps to a real `pyproject.toml` extra. `grep -rn "NotImplementedError" src/` from any documented entry point returns no matches.

- [ ] **Schema-stability test.** Verify: `pytest tests/integration/test_config_schema_stability.py -v` passes; the test runs in the `ci` marker subset (every PR).

- [ ] **Bug-report template exists.** Will be true after Task 4 of this plan.

- [ ] **Step 2: If any row fails, STOP**

Do not proceed to the bump. File the gap as a tracked issue, close it via the relevant earlier step's plan, then return to this audit.

- [ ] **Step 3: If all rows pass, capture evidence**

Save the output of:

```bash
bash .claude/scripts/measure-integration-coverage.sh > /tmp/beta-cov-final.txt
pytest -m "ci" -v > /tmp/beta-ci-final.txt
fo --debug version > /tmp/beta-debug-evidence.txt 2>&1
```

These outputs go into the PR body for the bump.

---

## Task 2: Add the Beta bug-report template

**Files:**
- Create: `.github/ISSUE_TEMPLATE/beta-bug.md`

- [ ] **Step 1: Write the template**

````markdown
---
name: Beta bug
about: Report a bug in a 2.0.0-beta.X release
title: "[BETA] "
labels: bug, beta
assignees: ""
---

<!--
Thanks for testing the fo-core beta channel!
Filing a bug here helps us decide when this build is ready to graduate to GA.
-->

## What happened

<!-- One-line description of the bug. -->

## fo --debug output

<!--
REQUIRED. Re-run your failing command with --debug and paste the FULL output
below (red error line + traceback). Without this, triage usually has to ask
you to re-run.
-->

```text
$ fo --debug <your command>
<paste output here>
```

## fo doctor output

<!-- REQUIRED. Output of `fo doctor`. -->

```text
$ fo doctor
<paste output here>
```

## Environment

- OS:
- Python version (`python --version`):
- fo-core version (`fo version`):
- How installed (`pip install fo-core`, `pip install -e .`, etc.):
- Optional extras installed (`pip show fo-core`):

## Steps to reproduce

1.
2.
3.

## Expected behavior

<!-- What did you think would happen? -->

## Actual behavior

<!-- What happened instead? -->
````

- [ ] **Step 2: Lint markdown**

```bash
pymarkdown -c .pymarkdown.json scan .github/ISSUE_TEMPLATE/beta-bug.md
```

(If the project's pymarkdown config excludes `.github/`, this step is a no-op.)

- [ ] **Step 3: Commit**

```bash
git add .github/ISSUE_TEMPLATE/beta-bug.md
git commit -m "feat(github): beta bug-report template requiring --debug output"
```

---

## Task 3: Add a release-metadata sanity test

**Files:**
- Create: `tests/ci/test_release_metadata.py`

- [ ] **Step 1: Write the test**

```python
"""Guard: version string and Development Status classifier must agree."""
from __future__ import annotations
import re
from pathlib import Path

import pytest


@pytest.mark.ci
def test_version_and_classifier_agree() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    version_match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert version_match, "Could not find version line in pyproject.toml"
    version = version_match.group(1)

    expected_classifier_substring = (
        "Development Status :: 4 - Beta" if "beta" in version
        else "Development Status :: 3 - Alpha" if "alpha" in version
        else "Development Status :: 5 - Production/Stable"
    )

    assert expected_classifier_substring in pyproject, (
        f"Version {version!r} implies classifier {expected_classifier_substring!r}, "
        f"but it was not found in pyproject.toml. Update the classifier."
    )
```

- [ ] **Step 2: Run BEFORE the version bump**

```bash
pytest tests/ci/test_release_metadata.py -v
```

Expected (pre-bump): PASS — current version `2.0.0-alpha.3` matches `3 - Alpha`.

- [ ] **Step 3: Commit**

```bash
git add tests/ci/test_release_metadata.py
git commit -m "test(ci): pin version <-> Development Status classifier agreement"
```

---

## Task 4: The bump itself

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: Bump version in pyproject.toml**

Change `pyproject.toml:7`:

```toml
version = "2.0.0-beta.1"
```

- [ ] **Step 2: Bump classifier in pyproject.toml**

Change `pyproject.toml:17`:

```toml
    "Development Status :: 4 - Beta",
```

- [ ] **Step 3: Add CHANGELOG section**

Insert at the top of `CHANGELOG.md` (above the existing alpha entries):

````markdown
## [2.0.0-beta.1] — YYYY-MM-DD

First public-pre-release beta. The PyPI classifier moves from
`3 - Alpha` to `4 - Beta` and the auto-updater's pre-release channel is
open: opt in with `fo update --pre-release`.

See [docs/release/beta-criteria.md](docs/release/beta-criteria.md) for the
full beta-tester contract, the schema-frozen promise, and the rollback
path.

### What's new since 2.0.0-alpha.3

- Audio transcription is wired end-to-end. `fo organize --transcribe-audio`
  uses faster-whisper to categorize audio files by transcript content;
  `fo benchmark --suite audio --transcribe-smoke` exercises the full path.
- Global `--debug` flag surfaces tracebacks for bug reports.
- First-run setup gate now consistently blocks all non-allowlisted commands.
- Config validation errors include valid-values lists and "did you mean"
  suggestions.
- Integration coverage lifted to ≥ 75% global; per-module floor at ≥ 70%
  on every module.
- Daemon smoke test guards start/watch/stop/status/SIGTERM in CI.

### Compatibility

- Schema is frozen at version 1.0 across the entire 2.0.0-beta.X line.
- Configs written by 2.0.0-alpha.3 read cleanly under 2.0.0-beta.1.
- See `docs/release/beta-criteria.md` §3 for the precise compat contract.

### How to opt in

```bash
fo update --pre-release
fo update install
```

### How to roll back to alpha

```bash
pip install 'fo-core==2.0.0-alpha.3'
fo update --no-pre-release
```
````

(Replace `YYYY-MM-DD` with the actual release date when running this task.)

- [ ] **Step 4: Update README Releases section**

Change `README.md` (the "Releases" section near the bottom) from:

```markdown
Currently `2.0.0-alpha.3`. The criteria for promoting to beta and the contract
with public pre-release testers are documented in
[docs/release/beta-criteria.md](docs/release/beta-criteria.md).
```

to:

```markdown
Currently `2.0.0-beta.1`. The auto-updater's public pre-release channel is open
— opt in with `fo update --pre-release`. See
[docs/release/beta-criteria.md](docs/release/beta-criteria.md) for the
beta-tester contract, the schema-frozen compatibility promise, and the
rollback path.
```

- [ ] **Step 5: Fix README CLI command list to match actual surface**

A pre-existing README inaccuracy (caught during the plan review pass): the
"CLI Commands" table near the top of `README.md` describes `fo profile` as
"Hardware profiling" — but `fo profile` is actually the Click-based
configuration-profile management group registered lazily in
`src/cli/main.py:314-324` (`_register_profile_command`), and the real
hardware-detection command is `fo hardware-info` defined at
`src/cli/main.py:154`. Fix both lines while we're already touching README
for the version bump.

In the `## CLI Commands` block, replace:

```text
fo profile                    Hardware profiling
```

with two lines (preserving the column alignment of the surrounding table):

```text
fo profile                    Configuration profile management
fo hardware-info              Detect and display hardware capabilities
```

- [ ] **Step 6: Run the metadata sanity test**

```bash
pytest tests/ci/test_release_metadata.py -v
```

Expected: PASS — version `2.0.0-beta.1` now matches `4 - Beta`.

- [ ] **Step 7: Run full pre-commit validation**

```bash
bash .claude/scripts/pre-commit-validation.sh
pytest -m "ci" -v
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml CHANGELOG.md README.md
git commit -m "$(cat <<'EOF'
release: 2.0.0-beta.1 (Development Status 4 - Beta)

Closes Step 5 of the alpha→beta path. Entry checklist in
docs/release/beta-criteria.md §2 audited before this bump; evidence
attached in the PR body.

The schema is frozen at 1.0 across the beta line; alpha.3 configs
read cleanly under beta.1. Public pre-release channel is open
(`fo update --pre-release`).
EOF
)"
```

---

## Task 5: PR + release

- [ ] **Step 1: Push and open PR**

Title: `release: 2.0.0-beta.1 (4 - Beta)`

Body: include the audit evidence captured in Task 1 Step 3. Link to the beta-criteria doc and to each Step plan that contributed.

- [ ] **Step 2: Wait for CI green**

Per `.claude/rules/pr-monitoring-protocol.md`, monitor until merge.

- [ ] **Step 3: Cut the GitHub release**

After merge:

```bash
git fetch origin
git checkout main
git pull
git tag -a 2.0.0-beta.1 -m "fo-core 2.0.0-beta.1 — first public beta"
git push origin 2.0.0-beta.1

gh release create 2.0.0-beta.1 \
    --title "2.0.0-beta.1 — first public beta" \
    --notes-from-tag \
    --prerelease
```

The `--prerelease` flag is critical — it puts the release on the pre-release channel that `fo update --pre-release` consumes, NOT the stable channel.

- [ ] **Step 4: Smoke-test the published release**

In a fresh venv:

```bash
python -m venv /tmp/beta-smoke
source /tmp/beta-smoke/bin/activate
pip install --pre fo-core==2.0.0-beta.1
fo --version  # should print 2.0.0-beta.1
fo doctor
```

- [ ] **Step 5: Announce**

Per the project's communication conventions (which I don't presume — leave this step for the maintainer to fill in: discussion forum post? README banner? Discord? Mailing list?). The doc honesty principle from beta-criteria.md applies: don't promise SLAs we don't have.

---

## Verification checklist

After this plan executes:

- `pip show fo-core | grep Status` reports `Status: 4 - Beta`.
- `fo --version` reports `2.0.0-beta.1`.
- `fo update --pre-release` then `fo update check` finds the new release.
- The Beta bug-report template appears in the issue creation flow.
- `tests/ci/test_release_metadata.py` is green (and will block any future bump that forgets to update the classifier).

This closes the alpha→beta path. From here forward, development continues
under the `4 - Beta` classifier with the schema-frozen promise from
`docs/release/beta-criteria.md` §3 as the only beta-line compatibility
guarantee. There is no committed timeline for `5 - Production/Stable` per
beta-criteria.md §5.
