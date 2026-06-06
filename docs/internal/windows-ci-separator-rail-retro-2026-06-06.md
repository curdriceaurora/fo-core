# Retro: Windows CI Failure → Separator-Sensitivity Rail (2026-06-06)

A retrospective on fixing a recurring nightly Windows CI failure and building a
PR-time guardrail so the same class of bug is caught before it reaches the
nightly.

## Summary

The nightly `CI Full Matrix` (`.github/workflows/ci-full.yml`) had its
**Test Windows (Python 3.12)** job failing every run. Root cause was a single
test with hardcoded POSIX path separators. We fixed the test, verified the fix
on a live Windows runner, then built an advisory lint rail to prevent the whole
class of breakage at PR time.

| Item | Outcome |
|------|---------|
| #464 | Fix the failing test | Merged; Windows job verified green |
| #465 → #466 | New `g2sep` rail (literals + f-strings) | Merged |
| #467 → #470 | Rail folds string concatenation; evasion sites cleaned | Merged |

## Timeline

1. Reviewer flagged repeated Windows + Python 3.12 shard failures across repos.
2. Pulled the actual failed-job logs instead of guessing. The nightly had failed
   daily; the Windows job failed on exactly one test, deterministically.
3. Diagnosed and fixed the test (#464), merged, then triggered the nightly via
   `workflow_dispatch` to confirm the Windows job passed on `main`.
4. Built the `g2sep` advisory rail (#466) to catch the class at PR time.
5. Hardened the rail through review feedback (#470): f-strings, then string
   concatenation, then a cleanup of two pre-existing rail-evasion sites.

## Root cause

The failing test was
`tests/cli/test_doctor.py::TestInstallMethodDetection::test_detect_pipx_via_pipx_home_env`.
It hardcoded forward-slash path separators on both sides of an assertion:

```python
custom_home = "/custom/pipx"
fake_exe = "/custom/pipx/venvs/fo-core/bin/python"
```

Production (`src/cli/doctor.py:119`) builds its match prefix with native
separators:

```python
if exe_path.startswith(os.path.join(pipx_home, "venvs") + os.sep):
    return "pipx"
```

On Windows, `os.path.join("/custom/pipx", "venvs") + os.sep` yields
`"/custom/pipx\\venvs\\"` (backslash `os.sep`). The forward-slash `fake_exe`
never starts with that prefix, so `_detect_install_method()` returned `"pip"`
and the assertion `== "pipx"` failed. The sibling detection tests passed on
Windows because they build paths via `os.path.expanduser`, staying consistent
with production's `os.path` semantics.

It was a **test-only** defect — production is correct on real Windows pipx
installs, where `sys.executable` already uses native separators.

## Why it kept recurring

The Windows runner only runs in the nightly `ci-full.yml` (scheduled +
`workflow_dispatch`), not on `ci.yml`'s per-PR jobs. So a separator-sensitive
test passes on the Linux PR checks, merges, and only fails the next morning in
the nightly — where it has to be hand-diagnosed each time. The feedback loop was
roughly a day long and detached from the change that caused it.

## The fix (#464)

Build the test fixtures with `os.path.join` / `os.sep` so the data matches
production's construction on every platform:

```python
custom_home = os.path.join(os.sep, "custom", "pipx")
fake_exe = os.path.join(custom_home, "venvs", "fo-core", "bin", "python")
```

Verified equivalent under both `posixpath` and `ntpath` (returns `"pipx"` on
both). A review comment also moved the `PIPX_HOME` mutation to
`monkeypatch.setenv` for xdist-safe isolation. After merge, a `workflow_dispatch`
run of `ci-full.yml` confirmed all five matrix jobs green — including
**Test Windows (Python 3.12)**.

## The guardrail (#466, #470)

A new advisory rail, `g2sep`, detects the class at PR time:

- `scripts/check_test_separator_paths.py` — AST detector over `tests/`. Flags a
  separator-sensitive absolute POSIX literal assigned to a path-like variable
  (`exe`/`path`/`dir`/`home`/`venv`/`bin`/`root`/`file` components). Exempts
  URLs, documented adversarial inputs (`/etc/passwd`, …), and lines marked
  `# g2sep: ok — <reason>`.
- `tests/ci/test_g2_separator_paths_rail.py` — predicate and integration tests
  plus a baseline test pinning the count at 0 (the real gate while the hook is
  advisory).
- `.pre-commit-config.yaml` — the hook in `--advisory` mode beside G2.

Detection scope was chosen empirically. Two candidate triggers were measured
against the live `tests/` tree: a function-context trigger produced 5 hits (all
false positives — URL fragments, config strings), while the path-like-variable
trigger produced 0 and matched the exact #464 shape. The precise trigger was
chosen so the rail lands clean and ratchets immediately.

Review feedback then widened coverage to the other ways the same hazard is
written:

- f-strings (`f"/custom/pipx/venvs/{name}/bin/python"`) via a static skeleton.
- string concatenation (`"/custom" + "/pipx/bin"`), with dynamic operands
  contributing a single-segment placeholder so a hardcoded prefix survives a
  dynamic suffix.
- the cleanup also removed two pre-existing `/home/user/` literals in
  `test_doctor.py` that were split across `+` specifically to evade the existing
  line-based G2 rail.

## What went well

- **Logs before guesses.** The reviewer's hypotheses were broad
  (file locking, encoding, pathlib). Reading the actual failed-job logs narrowed
  it to one deterministic test in minutes and avoided a commit-and-pray cycle.
- **Verified the platform fix on the platform.** Rather than trust local
  reasoning, we dispatched the nightly and confirmed the real Windows job green.
- **Turned a one-off fix into a class fix.** The rail means the next
  separator-sensitive literal fails a PR check, not a nightly.
- **Empirical scope selection.** Measuring trigger candidates before committing
  to a heuristic kept the baseline at 0 and false positives near zero.
- **Review feedback strengthened the rail.** Codex caught a genuinely
  ineffective baseline gate (the advisory script exits 0, so the gate had to
  assert the scanned count directly) and two coverage gaps (f-strings,
  concatenation). Each was reproduced, fixed, and covered by tests.

## What was tricky

- **Advisory exit code vs. the gate.** Because the hook is advisory (exits 0
  even on violation), the baseline test — not the exit code — is the real CI
  gate. The first version asserted the return code, which was always 0. Fixed to
  assert `len(_scan_all()) <= _BASELINE_VIOLATIONS`, matching the convention in
  `test_textiowrapper_detach_rail.py`.
- **Self-flagging.** The rail's own test file contains separator-sensitive
  literals as test data. They are built via concatenation and bound to
  non-path-like names so the rail does not flag itself.
- **A deferred decision.** Folding concatenation surfaced two pre-existing
  `/home/user/` sites. Rather than guess, we paused to confirm scope, then
  cleaned them up in the same follow-up rather than grandfathering a non-zero
  baseline.
- **No "CI passed" webhook.** PR webhooks deliver failures, comments, and
  reviews — never CI success, new pushes, or merge-conflict transitions. Success
  had to be polled.

## Cross-platform test rules of thumb

- Build path test data with `os.path.join` / `os.sep` (or `tmp_path`), never
  hardcoded `/`-separated literals, when the value is compared against
  code-under-test that constructs paths with `os.sep`.
- When patching, keep both sides of a comparison consistent
  (`os.path.expanduser` on one side and a raw literal on the other will diverge
  on Windows).
- A bug that only the nightly Windows runner can see is worth a PR-time rail,
  not just a one-line fix.

## References

- PRs: #464 (fix), #466 (rail), #470 (concatenation + cleanup).
- Issues: #465 (rail), #467 (concatenation follow-up) — both closed.
- Code: `scripts/check_test_separator_paths.py`,
  `tests/ci/test_g2_separator_paths_rail.py`,
  `src/cli/doctor.py`, `tests/cli/test_doctor.py`.
- Rule note: `.claude/rules/test-generation-patterns.md` (T13, G2-sep sub-rail).
