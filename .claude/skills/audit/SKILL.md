---
name: audit
description: >
  Audit changed code against project anti-pattern rules (feature-generation-patterns,
  test-generation-patterns). Use when: "audit patterns", "check anti-patterns",
  "audit code", "pattern check", or as part of /pr-prep.
metadata:
  version: 1.0.0
  author: rahul
---

# /audit - Anti-Pattern Conformance Audit

Checks changed code against the project's documented anti-pattern rules. Catches issues that /simplify (generic code review) and pre-commit (mechanical lint) miss because they don't know the project's specific lessons learned.

## Why This Exists

/simplify uses generic software engineering judgment. The pre-commit script uses regex/lint rules. Neither reads the project's anti-pattern documentation:

- `.claude/rules/feature-generation-patterns.md` (F1-F10)
- `.claude/rules/test-generation-patterns.md` (T1-T14)
- `.claude/rules/ci-generation-patterns.md` (C1-C8)

This skill bridges that gap by checking new code against documented patterns that caused past PR review churn.

## Usage

```
/audit
```

Run after writing code, before /simplify or /pr-prep.

## Workflow

### Phase 1: Identify Changes

Run `git diff main` (or `git diff HEAD` if no main divergence) to get the full diff.

Separate changes into:
- **Source code** (`src/`) - checked against feature-generation patterns
- **Test code** (`tests/`) - checked against test-generation patterns
- **CI/config** (`.github/`, `pyproject.toml`) - checked against CI patterns

### Phase 2: Launch Audit Agents in Parallel

Use the Agent tool to launch two agents concurrently.

#### Agent 1: Feature Generation Audit

Read `.claude/rules/feature-generation-patterns.md` in full, then audit ALL source code changes against each pattern:

- **F1 MISSING_ERROR_HANDLING**: For every external call (file I/O, network, model init, subprocess), identify what exceptions it can raise. Flag any `except` that's narrower than the failure modes warrant. Flag any external call with no exception handling.
- **F2 TYPE_ANNOTATION**: Every new/modified function must have parameter types and `-> return_type`. Flag any `Any` that should be concrete.
- **F3 THREAD_SAFETY**: Flag shared mutable state without locks. Flag non-atomic read-modify-write. Flag `@lru_cache` on functions reading env vars.
- **F4 SECURITY_VULN**: Flag auth tokens in query strings, unsanitized path inputs, secrets in logs, missing validation at API boundaries.
- **F5 HARDCODED_VALUE**: Flag magic strings/numbers that should come from ConfigManager or settings.
- **F6 API_CONTRACT_BROKEN**: Verify new implementations match their base class/interface.
- **F7 RESOURCE_NOT_CLOSED**: Flag file handles, connections, or processors not in context managers or missing cleanup.
- **F8 WRONG_ABSTRACTION**: Flag business logic in route handlers, mixed concerns.
- **F9 DYNAMIC_IMPORT**: Flag any `__import__()` that should be a top-level import.
- **F10 DOCSTRING_DRIFT**: When exception handling, return types, or control flow changed, flag docstrings that still describe the old behavior.

For each finding, cite the exact file, line number, and which pattern it violates. Verify against actual source code before reporting.

#### Agent 2: Test Generation Audit

Read `.claude/rules/test-generation-patterns.md` in full, then audit ALL test code changes against each anti-pattern (T1-T14):

- **T1 SOLE_ISINSTANCE**: `assert isinstance(x, T)` as the only assertion — verifies the type but not the value.
- **T2 MISSING_STATE_VERIFICATION**: Mutating method called but only return value checked — state change not verified.
- **T3 MOCK_CALL_COUNT_WITHOUT_PAYLOAD**: `mock.assert_called_once()` without `_with(...)`, or `call_count >= 1` without payload verification.
- **T4 TAUTOLOGICAL_DISJUNCTION**: `assert X or isinstance(X, T)` always passes for None + type combinations.
- **T5 IMPORTSKIP_SCOPE**: `pytest.importorskip` at module level in a file with classes that don't need the optional dep.
- **T7 UNVERIFIED_EXCEPTION_GUARD**: `try/except SomeError: pass` guarding an exception production code never raises.
- **T8 MISSING_IMPORT_GUARD**: Test file imports an optional dep at module level without `pytest.importorskip`.
- **T9 VACUOUS_TAUTOLOGY**: `assert X >= 0` on lengths/counts/durations; `assert X is not None or X is None`.
- **T10 PREDICATE_MISSING_NEGATIVE_CASE**: `_is_X()` predicate tested only with positive inputs; no test asserts False for same-shape-wrong-context.
- **T11 PRAGMA_ON_TESTED_BRANCH**: `# pragma: no cover` added to a branch that already has a dedicated test.
- **T12 FIXTURE_STATE_LEAK**: Singleton/sys.modules snapshot captured but never restored; partial sub-module restoration.
- **T13 HARDCODED_TEST_DATA_PATHS**: `/tmp/`, `/dev/null` literals in test dataclass fields or fixture dicts (use `tmp_path`).
- **T14 GENERATOR_THROW_FALSE_RAISE**: `(_ for _ in ()).throw(X(...))` as a mock side-effect — returns generator, never raises.

Also check generic test-quality concerns beyond the numbered patterns:
- **Missing mock verification**: Mock declared in `@patch` decorator but never asserted.
- **Missing negative assertions**: Happy path verified but unhappy path not asserted as *not* taken.
- **Private attribute access**: `assert obj._private_attr == val` instead of testing via public API.
- **Resource leaks**: File-backed engines without `.dispose()`, temp files without cleanup.
- **Dead test helpers**: Unused helper methods left in test files.
- **Missing direct unit tests**: New methods only exercised indirectly through integration tests.

For each finding, cite the exact test file, test method, line number, and which anti-pattern it matches.

### Phase 3: Report and Fix

Wait for both agents. Aggregate findings into a table:

```
| File | Line | Pattern | Severity | Finding |
|------|------|---------|----------|---------|
```

For each finding:
1. Verify it's real (not a false positive) by reading the actual code
2. If real: fix it directly
3. If false positive: skip with a brief note

After fixes, re-run affected tests to confirm nothing broke.

## What This Catches (That Other Tools Miss)

| Tool | What It Catches | What It Misses |
|------|----------------|---------------|
| Pre-commit script | Syntax, formatting, lint, test pass/fail | Semantic correctness, assertion quality, exception scope |
| /simplify | Generic reuse, copy-paste, efficiency | Project-specific anti-patterns, test assertion quality |
| **/audit** | **F1-F10 feature patterns, T1-T14 test patterns, C1-C8 CI patterns** | Generic code quality (that's /simplify's job) |

## Integration with /pr-prep

Add to /pr-prep Phase 2 BEFORE /simplify:

```
Phase 2: Cross-Stream Integration
  1. Run tests
  2. Run /audit          <-- NEW: project-specific pattern check
  3. Run /simplify       <-- generic code quality
  4. Re-run tests after fixes
```

## Critical Rules

- NEVER skip the test generation audit on test file changes
- NEVER report findings without verifying against actual source (agents hallucinate)
- ALWAYS read the full anti-pattern rule file before auditing (don't rely on memory)
- ALWAYS cite file:line for every finding
- Fix real findings directly; don't just report them
