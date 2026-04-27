# Guardrail Workflow

Guardrails exist to turn repeated PR review findings into enforced checks. The
goal is to catch high-confidence problems before review, without creating a
second competing policy engine.

## Canonical Ownership

Each blocking guardrail belongs in exactly one enforced layer:

| Guardrail type | Canonical home | Why |
|----------------|----------------|-----|
| Staged diff and mechanical checks | `.pre-commit-config.yaml` | Fast, deterministic checks on changed files before commit |
| Semantic regressions and contract checks | `tests/ci/` | Python-based assertions are easier to evolve and review than shell heuristics |
| CI-only runtime support | `.github/workflows/ci.yml` | Some guardrails need GitHub permissions or environment variables to behave in PR CI |
| Pre-PR orchestration | `.claude/scripts/pre-commit-validation.sh` | Runs the enforced layers together before push or PR creation |
| Explanatory guidance | `memory/`, `.claude/rules/`, and related docs | Captures lessons and rationale, but should not be the only blocking source |

Pitfall: if a rule only exists in prose or only exists in the shell script, it
will drift. Blocking policy belongs in pre-commit hooks or `tests/ci`.

## Canonical Pre-PR Flow

Run this before opening or updating a PR:

```bash
bash .claude/scripts/pre-commit-validation.sh
```

That command must expand to this canonical command set:

1. `pre-commit validate-config`
2. `pre-commit run --all-files`
3. `pytest tests/ci -q --no-cov --override-ini="addopts="`

Why this split:
- `pre-commit` is the staged-file gate — `--all-files` ensures local and CI run the same hooks
- `tests/ci` is the semantic gate
- the shell script is just the orchestrator

## How to Add a New Guardrail

Add a new rule to the narrowest layer that can enforce it cleanly:

1. Use `.pre-commit-config.yaml` for changed-file, grep-like, formatter, lint, or focused test gates.
2. Use `tests/ci/` for AST checks, repository-wide assertions, workflow contracts, or docs/code alignment.
3. Update `.github/workflows/ci.yml` when a CI-only guardrail needs runtime support such as `GITHUB_TOKEN` or `pull-requests: read`.
4. Update documentation after the enforcement layer exists, not before.

Do not add new blocking policy directly to `.claude/scripts/pre-commit-validation.sh`.

## Retired Guardrail Packs

The legacy API-compat and memory-lifecycle detector packs from the pre-CLI-only
architecture are retired. References to `review_regressions` modules and their
paired tests were removed to keep this document aligned with the active codebase.

If equivalent checks are reintroduced, document the canonical ownership again in
this file and add corresponding `tests/ci/` coverage in the same change.

## F11-resolve Rail

The F11-resolve rail (issue #216) blocks regressions of the symlink-loop
defence pattern from PRs #168, #173, and #195. `Path.resolve()` raises
`RuntimeError` on Python < 3.13 for symlink loops and `OSError` on Python >=
3.13. Any `.resolve()` call found inside a `try` block whose `except` clauses
do not cover `RuntimeError` (directly, via `Exception`, or via a bare
`except`) is flagged.

| Enforcement layer | Location | When it fires |
|-------------------|----------|---------------|
| CI backstop | `tests/ci/test_resolve_runtime_error_guard.py` | On every CI run (full scan of `src/`) |

Detection logic lives in `scripts/check_resolve_runtime_error.py`.

**Allowlist:**

- `src/utils/**` — utility helpers and ops scripts
- `src/cli/path_validation.py` — the canonical resolve wrapper (already handles both exceptions)

**Opt-out:** add `# noqa: F11-resolve — <reason>` on or up to two lines above the `.resolve()` call.

**Fix pattern:**

```python
# BAD — RuntimeError on symlink loops escapes the try (Python < 3.13)
try:
    real = entry.resolve()
except OSError:
    ...

# GOOD — explicit handling for both Python versions
try:
    real = entry.resolve()
except (ValueError, RuntimeError, OSError):
    ...
```

## Search Guardrail Rule Index

Issue `#869` adds corpus-safety checks for the search service. These are
AST-based semantic checks and therefore belong in CI tests, not shell-script
heuristics.

| Rule ID | Canonical enforced layer | Why this home |
|---------|--------------------------|---------------|
| `S1:symlink-filter(is_symlink())` | `tests/ci/test_search_code_quality.py` | Prevents symlink traversal into untrusted targets such as `/etc/passwd` or `~/.ssh/` |
| `S2:hidden-file-filter(startswith("."))` | `tests/ci/test_search_code_quality.py` | Prevents indexing of `.git`, `.env`, `.ssh/authorized_keys`, and other hidden paths |

Implementation detail:
- Guardrail logic lives in `tests/ci/test_search_code_quality.py` (function `_find_unguarded_traversals`).
- Detection uses AST call-node matching scoped to each function's own body — docstrings and comments cannot produce false positives.
- Rule definitions and fix examples live in `.claude/rules/search-generation-patterns.md` patterns S1 and S2.

## T10 Predicate Negative-Case Rule Index

Issues `#930` and `#931` add two enforcement layers for the T10 pattern: every
`_is_*`/`_has_*`/`_find_*` predicate in detector modules must have a paired
negative test case (`assert not predicate(...)`).

| Enforcement layer | Location | When it fires |
|-------------------|----------|---------------|
| Pre-commit hook | `.pre-commit-config.yaml` → `predicate-negative-coverage` | When detector modules or their test files are staged |
| CI backstop | `tests/ci/test_predicate_negative_coverage.py` | On every CI run (full scan) |

Shared logic lives in `.claude/scripts/check_predicate_negative_coverage.py`.
The CI test imports from this script via `importlib`; the pre-commit hook runs
it directly.  Update the script when new detector modules are added.

| Detector module | Test file | Module stem in `_MODULE_TO_TEST` |
|-----------------|-----------|----------------------------------|
| `correctness.py` | `test_correctness_detectors.py` | `correctness` |
| `security.py` | `test_security_detectors.py` | `security` |
| `memory_lifecycle.py` | `test_memory_lifecycle_detectors.py` | `memory_lifecycle` |
| `test_quality.py` | `test_test_quality_detectors.py` | `test_quality` |
| `api_compat.py` | `test_api_compat_detectors.py` | `api_compat` |

To add a new detector module: add its entry to `_MODULE_TO_TEST` in the script,
add `assert not predicate(...)` calls to the test file for every new predicate,
then verify `pre-commit run predicate-negative-coverage` and
`pytest tests/ci/test_predicate_negative_coverage.py` both pass.

## CLI Contract Test Conventions

Typer and Rich render help text differently across terminals and CI. For CLI
contract tests:

- Prefer `result.output` over `result.stdout`
- Normalize rendered help before asserting exact content
- Assert semantic fragments unless exact formatting is part of the contract

Example:

```python
result = runner.invoke(app, ["organize", "--help"])
assert result.exit_code == 0
rendered_help = _rendered_text(result.output)
assert "--no-prefetch" in rendered_help
```

Pitfall: direct assertions on styled help output can pass locally and fail in
GitHub Actions because Rich changes wrapping and ANSI rendering.

## GitHub-Environment Branching Helpers

Guardrail code that branches on `GITHUB_*` variables is CI-first code. Treat it
as such:

- Test both local mode and GitHub PR mode
- Cover the fallback path explicitly
- Avoid assuming a full git history or a specific merge-parent layout
- Prefer GitHub-provided PR context when the question is really about PR files

Examples of CI-only runtime support:
- `pull-requests: read` permission for PR file lookups
- `GITHUB_TOKEN` in workflow steps that call GitHub APIs

## Workflow Support Mapping

CI workflow configuration is part of the guardrail system, not an incidental
detail:

- `.github/workflows/ci.yml` must expose the permissions and environment needed
  by CI-only guardrails
- `tests/ci/test_workflows.py` should lock in those assumptions
- if a guardrail needs workflow support, document the ownership in the same PR

This keeps reviewers from having to infer whether a failure is in the rule
itself or in the CI runtime that the rule depends on.
