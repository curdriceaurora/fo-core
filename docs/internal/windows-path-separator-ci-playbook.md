# Windows CI Path-Separator Failures: Diagnosis & Prevention Playbook

A portable guide for Python projects — especially file-organizer-style tools
that compare or build filesystem paths — whose **Windows CI jobs fail while
Linux/macOS pass**. It captures one recurring failure class (hardcoded `/`
separators in tests), how to diagnose it fast, how to fix it, and how to build a
lightweight lint rail so the class is caught at PR time instead of in a
Windows-only nightly.

This document is project-agnostic. Where it says "your detector", "your test",
"your nightly workflow", substitute the equivalent in the target repo.

## When this applies

Reach for this playbook when **all** of the following hold:

- A CI job on `windows-latest` fails, but the same tests pass on Linux/macOS.
- The failure is deterministic (same test, every run) — not a flaky timeout.
- The code under test, or the test itself, **builds or compares filesystem
  paths**.

It is especially relevant to tools that detect install locations (pipx/venv),
resolve executables, or organize files by path, because those compare a runtime
path against an expected prefix.

## The core failure pattern

Python's `os.sep` is `/` on POSIX and `\` on Windows. A test that hardcodes
forward slashes in its fixtures will silently diverge from production code that
builds the comparison side with `os.path.join(...) + os.sep` (or `os.sep`
directly) — but only on Windows.

Illustrative example (an install-method detector):

```python
# production code under test
def _detect_install_method(exe_path: str, base: str) -> str:
    if exe_path.startswith(os.path.join(base, "venvs") + os.sep):
        return "managed"
    return "system"
```

```python
# the failing test — hardcoded POSIX separators on both sides
base = "/custom/base"
fake_exe = "/custom/base/venvs/app/bin/python"
assert _detect_install_method(fake_exe, base) == "managed"
```

On Windows, `os.path.join("/custom/base", "venvs") + os.sep` evaluates to
`"/custom/base\\venvs\\"` (backslash `os.sep`). The forward-slash `fake_exe`
does not start with that prefix, so the function returns `"system"` and the
assertion fails. On Linux the separators match, so it passes.

Key insight: the production code is usually **correct** — on a real Windows
machine, `sys.executable` already uses native separators. The defect lives in
the **test data**, which mixes hardcoded `/` literals with `os.path`-built
values.

## Why it hides until the nightly

Many projects run the full OS matrix (including `windows-latest`) only in a
**scheduled nightly** or `workflow_dispatch` workflow, while per-PR CI runs
Linux-only for speed. A separator-sensitive test therefore:

1. Passes the Linux PR checks.
2. Merges.
3. Fails the next morning in the nightly Windows job — detached from the change
   that introduced it, and needing fresh hand-diagnosis each time.

The feedback loop is ~a day long. That asymmetry is the reason a PR-time
guardrail (below) is worth more than a one-line fix.

## Step 1 — Diagnose from the logs, not from hypotheses

Cross-platform CI failures invite broad guesses (file locking, encoding,
`pathlib` quirks). Resist them. Pull the **actual failed-job logs first**; the
real cause is usually one deterministic assertion.

- Open the failed Windows job and read the test summary and traceback.
- Confirm it is the *same* test failing every run (deterministic), not a moving
  target (which would suggest flakiness/parallelism instead).
- Look at the assertion: a `==` mismatch where one side has `/` and the other
  has `\`, or a `startswith`/path comparison returning the wrong branch, is the
  signature of this class.

If the failure is instead a `PermissionError`/`WinError 32` on file teardown, an
encoding error on non-ASCII filenames, or a wall-clock timeout, this is a
*different* class — see "Adjacent Windows failure classes" at the end.

## Step 2 — Fix the test data

Build the fixtures the same way production builds paths, so they agree on every
platform:

```python
base = os.path.join(os.sep, "custom", "base")
fake_exe = os.path.join(base, "venvs", "app", "bin", "python")
```

Guidelines:

- Prefer `tmp_path` (pytest) for real files; use `os.path.join` / `os.sep` for
  synthetic path *strings* that are only compared, never opened.
- Keep **both sides of a comparison consistent**. If production calls
  `os.path.expanduser` (or `os.path.join`), the test fixture must use the same
  construction — never a raw literal on one side and an `os.path` value on the
  other.
- Verify cross-platform without a Windows machine by simulating both separator
  regimes:

```python
import ntpath, posixpath

def _detect(exe, base, p):  # p is posixpath or ntpath
    return "managed" if exe.startswith(p.join(base, "venvs") + p.sep) else "system"

for mod in (posixpath, ntpath):
    base = mod.join(mod.sep, "custom", "base")
    exe = mod.join(base, "venvs", "app", "bin", "python")
    assert _detect(exe, base, mod) == "managed"
```

After merging, trigger the nightly (`workflow_dispatch`) to confirm the real
Windows job is green rather than trusting local reasoning alone.

## Step 3 — Build a PR-time guardrail (recommended)

A one-line fix removes today's failure; a lint rail removes the class. The rail
flags separator-sensitive path literals in tests so the next one fails a PR
check, not a nightly.

### Detection heuristic

Scan every test file's AST. Flag an assignment

```text
<name> = <string-expression>
```

when **both** hold:

1. `<name>` is **path-like** — a snake_case component is in a small set such as
   `exe`, `path`, `dir`, `home`, `venv`, `bin`, `root`, `file`. This keeps the
   rail focused on values fed into path comparisons and away from URL fragments,
   dict keys, and other incidental `/` strings.
2. `<string-expression>` reduces to a **separator-sensitive absolute POSIX
   path** — it starts with `/`, has at least two non-empty `/`-separated
   segments, is not a URL (`://`), and is not a documented adversarial input
   (e.g. `/etc/passwd` used as a path-validation argument).

Handle the three ways the same literal is written:

- **Plain string** — `"/custom/base/bin/python"`.
- **f-string** — `f"/custom/base/{name}/bin/python"`: build a *static skeleton*
  by keeping literal parts and replacing each `{...}` interpolation with a
  single-segment placeholder. A hardcoded `/` prefix still trips the check.
- **Concatenation** — `"/custom" + "/base/bin"`: fold `+` recursively. A dynamic
  operand contributes the same single-segment placeholder, so a hardcoded prefix
  survives a dynamic suffix (`"/custom/base/" + name + "/bin"`). A fully dynamic
  expression (`a + b`) yields no static prefix and is not flagged. A dynamic
  *prefix* (`base + "/sub"`) starts with the placeholder, fails the leading-`/`
  anchor, and is correctly not flagged.

### Tuning to avoid false positives

Pick the trigger empirically against the real test tree, not from intuition.
Measure candidate triggers and count hits:

- A "the enclosing function uses `os.path.join`/`os.sep`" trigger tends to
  over-match (URL fragments, model-path config strings, unrelated keys).
- The "path-like variable name + separator-sensitive literal" trigger is far
  more precise and usually starts at a near-zero baseline.

Choose the trigger that yields the smallest honest baseline while still catching
the original bug shape.

### Lifecycle: advisory → enforcing

Land the rail **advisory** so it never blocks unrelated work on day one:

- The pre-commit hook prints violations but exits 0 (`--advisory`).
- A baseline test pins the current count (ideally 0). **This test — not the
  hook's exit code — is the real CI gate.** Because the hook exits 0, asserting
  the script's return code would be a no-op; assert the *scanned violation
  count* directly:

```python
def test_no_regression_beyond_baseline():
    assert len(scan_all()) <= BASELINE_VIOLATIONS  # BASELINE_VIOLATIONS = 0
```

- Promote to enforcing (drop `--advisory`, fail on violation) once the baseline
  has held at 0.

### Opt-out

Provide a dedicated inline comment for the rare legitimately-Linux-only path —
e.g. `# sepcheck: ok — <reason>` on the assignment line. Use a dedicated token
rather than reusing the linter's suppression namespace (e.g. Ruff's `# noqa`),
so the two are opted out independently and the linter does not try to parse your
token as one of its codes.

### Self-flagging caution

The rail's own test file contains separator-sensitive literals as test data.
Keep them off the rail's radar: build them via concatenation and bind them to
**non-path-like** variable names, or construct them at runtime, so the detector
does not flag its own fixtures.

## Common adjacent gotchas

- **Hardcoded-home-path evasion.** A separate line-based "no `/home/`,
  `/tmp/`, `/Users/` in tests" rail can be evaded by splitting the literal
  across `+` (`"/home" + "/user/..."`). An AST rail that folds concatenation
  catches these; rebuild such constants with `os.path.join(os.sep, ...)`.
- **No "CI passed" webhook.** PR automation webhooks typically deliver
  *failures*, comments, and reviews — never CI *success*, new pushes, or
  merge-conflict transitions. To confirm green you must poll; do not wait for an
  event that never arrives.
- **Predicate test coverage.** For each `_is_X` / `_has_X` predicate in the
  detector, add a negative case: a node with the same surface shape but the
  wrong context, asserting it is *not* flagged. False positives in a guardrail
  erode trust faster than misses.

## Adjacent Windows failure classes (not this one)

If the logs show something other than a `/`-vs-`\` mismatch, you are likely
looking at a different problem:

- **`PermissionError` / `WinError 32` on teardown** — a file handle left open
  before a move/delete. Audit fixtures for unclosed `open(...)`; prefer
  `tmp_path`; consider `shutil.rmtree(..., ignore_errors=True)` in teardown.
- **Encoding (`cp1252` vs UTF-8)** — non-ASCII filenames or content. Force
  `encoding="utf-8"` on file opens; set `PYTHONUTF8=1` in the Windows job env.
- **`Path.relative_to` / `pathlib` strictness** — Python version differences in
  path internals; avoid relying on undocumented `pathlib` behavior and
  subclassing `Path`.
- **Wall-clock timeouts** — shared-runner load, not a real bug; replace strict
  timing assertions with relative bounds or remove them.

## Checklist (copy into the target project's issue)

- [ ] Pull the failed Windows job logs; confirm a single deterministic
      assertion with a `/`-vs-`\` mismatch.
- [ ] Fix the offending test data with `os.path.join` / `os.sep` / `tmp_path`;
      keep both comparison sides consistent.
- [ ] Verify under both `posixpath` and `ntpath` locally.
- [ ] Trigger the nightly full-matrix workflow and confirm the Windows job
      green.
- [ ] (Optional but recommended) Add an advisory AST rail flagging
      separator-sensitive path literals in tests; pin a baseline test;
      provide an opt-out comment.
- [ ] Sweep the test tree for hardcoded-home-path literals split across `+`.
