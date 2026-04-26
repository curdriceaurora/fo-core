# Test Generation Anti-Patterns

Reference ruleset for writing tests that catch real bugs.
Sourced from CodeRabbit and Copilot review comments across integration test PRs.

**Core question to ask before writing any assertion:**
> "If the mock was never called / returned the wrong value / mutated nothing, would my test still pass?"
> If yes — the test is not catching anything real.

---

## Pre-Generation Checklist

Before writing any test:

- [ ] Does each assertion verify a *specific value*, not just a type or non-None? (T1 — use Fix-by-type table)
- [ ] After calling a mutating method, does the test verify the mutation persisted? (T2)
- [ ] Are mocks asserted with exact call args, not just `call_count >= 1`? (T3)
- [ ] Is any assertion an `X or isinstance(X, T)` or `X is not None or X is None` tautology? (T4 / T9)
- [ ] Is `pytest.importorskip` scoped to only the classes that use the optional dep? (T5)
- [ ] Does any `try/except` guard an exception the production code never raises? (T7)
- [ ] Does this file import an optional dep at module level without `pytest.importorskip`? (T8)
- [ ] Does any assertion use `>= 0` on a length, count, or duration? Replace with a meaningful bound. (T9)
- [ ] For every `_is_X()` / `_has_X()` predicate in detector/guardrail code — is there a negative test case that passes the same surface shape with the wrong context and asserts `False`? (T10)
- [ ] About to add `# pragma: no cover`? Grep `tests/` for the enclosing function first. (T11)
- [ ] Snapshotting singleton or `sys.modules` state? Confirm teardown actually restores it (including sub-module keys). (T12)
- [ ] Any hardcoded `/tmp/` or `/dev/null` path literals in test data? Use `tmp_path`. (T13)
- [ ] Any `(_ for _ in ()).throw(...)` mock side-effect? Use a named function or `MagicMock(side_effect=...)`. (T14)

---

## Pattern T1: SOLE_ISINSTANCE — verified by CI guardrail

**What it is**: Test function's only assertion is `assert isinstance(x, T)`. Verifies the
return type but not the value. Since most methods have a defined success/failure value,
the test passes even if the implementation returns the wrong one.

**Bad**:

```python
def test_update_rule(self, rule_manager, sample_rule):
    rule_manager.add_rule("default", sample_rule)
    updated = Rule(name="pdf_to_archive", description="Updated description")
    result = rule_manager.update_rule("default", updated)
    assert isinstance(result, bool)  # passes even if update_rule returns False

def test_get_summary(self, analyzer):
    result = analyzer.get_summary()
    assert isinstance(result, str)  # passes even if get_summary returns ""

def test_load_config(self, loader):
    result = loader.load()
    assert isinstance(result, dict)  # passes even if load returns {}

def test_list_rules(self, rule_manager):
    result = rule_manager.list_rules()
    assert isinstance(result, list)  # passes even if list_rules returns []
```

**Good**:

```python
def test_update_rule(self, rule_manager, sample_rule):
    rule_manager.add_rule("default", sample_rule)
    updated = Rule(name="pdf_to_archive", description="Updated description")
    result = rule_manager.update_rule("default", updated)
    assert result is True
    retrieved = rule_manager.get_rule("default", "pdf_to_archive")
    assert retrieved is not None
    assert retrieved.description == "Updated description"
```

**Fix by type** (apply the appropriate fix for the return type):

| Return type | Weak assertion | Strong assertion |
|-------------|---------------|-----------------|
| `bool` | `assert isinstance(result, bool)` | `assert result is True` or `assert result is False` |
| `str` | `assert isinstance(result, str)` | `assert result == "expected_string"` or `assert "key" in result` |
| `dict` | `assert isinstance(result, dict)` | `assert result == {"key": "val"}` or `assert result["key"] == val` |
| `list` | `assert isinstance(result, list)` | `assert len(result) == N` + content check |
| `int` | `assert isinstance(result, int)` | `assert result == expected_int` |
| `float` | `assert isinstance(result, float)` | `assert result == pytest.approx(expected_float)` |

**Residual count**: 0 — the full residual backlog was cleaned in the C1 phase (PR #179)
before the guardrail was promoted to full-suite enforcement. Any new sole-isinstance
assertion (even in pre-existing files) is blocked by CI.

**CI enforcement**: `test_changed_tests_have_no_sole_isinstance_assertions` in
`tests/ci/test_test_quality_guardrails.py` — iterates every `*.py` under `tests/`,
not just the diff. Full-suite promotion landed in `epic-g-rails` (G4).

---

## Pattern T2: MISSING_STATE_VERIFICATION

**What it is**: Test calls a mutating method (add, update, remove, toggle, save) but only
asserts the return value, not that the state actually changed.

**Bad**:

```python
def test_remove_rule(self, rule_manager, sample_rule):
    rule_manager.add_rule("default", sample_rule)
    result = rule_manager.remove_rule("default", "pdf_to_archive")
    assert result is True  # but what if get_rule still returns the rule?
```

**Good**:

```python
def test_remove_rule(self, rule_manager, sample_rule):
    rule_manager.add_rule("default", sample_rule)
    result = rule_manager.remove_rule("default", "pdf_to_archive")
    assert result is True
    assert rule_manager.get_rule("default", "pdf_to_archive") is None  # state verified
```

**Pre-generation check**: For every mutating method call, ask: "How do I prove the mutation happened?"

---

## Pattern T3: MOCK_CALL_COUNT_WITHOUT_PAYLOAD

**What it is**: Mock verified with `call_count >= 1` or `assert_called()` without checking
what arguments it was called with. The test passes even if the mock was called with garbage.

**Bad**:

```python
def test_sends_notification(self, mock_notifier):
    service.process(item)
    assert mock_notifier.notify.call_count >= 1  # called, but with what?
```

**Good**:

```python
def test_sends_notification(self, mock_notifier):
    service.process(item)
    mock_notifier.notify.assert_called_once_with(
        recipient="user@example.com",
        subject="Processing complete",
    )
```

### Mechanical sub-rail (T3 narrow): `assert <mock>.called` is banned

The full T3 surface (call-count comparisons, payload-free `assert_called()`)
has many legitimate uses (logger spies, control-flow probes), so a strict
rail would be too noisy. But the **`assert <mock>.<attr>.called` attribute-
lookup form** has zero legitimate uses — the canonical mock-library
equivalent `<mock>.<attr>.assert_called()` is one extra character, more
discoverable in IDEs (it's a documented method, not a flag attribute),
and consistent with the rest of the test suite's assertion style.

**Bad**:

```python
assert mock_console.print.called
assert mock.method.called is True
assert mock.method.called == True
```

**Good**:

```python
mock_console.print.assert_called()
mock.method.assert_called()
```

**Mechanical rail**: `scripts/check_called_attribute_assertion.py` —
regex detector with `# noqa: T3` opt-out for the rare case where the
attribute access is intentional (e.g. testing the mock library itself).
Wider T3 (call-count, payload-free `_with`-less calls) remains
code-review-enforced.

---

## Pattern T4: TAUTOLOGICAL_DISJUNCTION

**What it is**: `assert X is None or isinstance(X, T)` — always passes for `None` or any
value of type `T`. Equivalent to asserting nothing meaningful about the non-None case.

**Bad**:

```python
result = rule_manager.toggle_rule("default", "ghost")
assert result is None or isinstance(result, bool)  # always True for None or bool
```

**Good**:

```python
result = rule_manager.toggle_rule("default", "ghost")
assert result is None  # nonexistent rule returns None specifically
```

---

## Pattern T5: IMPORTSKIP_SCOPE

**What it is**: `pytest.importorskip("pkg")` at module level in a file that mixes classes
with and without the optional dependency. All classes in the file are skipped when the
package is absent, including ones that don't need it.

**Bad**:

```python
# test_security_bm25_decorators.py
pytest.importorskip("rank_bm25")  # skips security + decorator tests too!

class TestPluginSecurityPolicy: ...   # doesn't use rank_bm25
class TestBM25Index: ...              # does use rank_bm25
```

**Good**:

```python
# test_security_bm25_decorators.py
class TestPluginSecurityPolicy: ...   # no importorskip needed

class TestBM25Index:
    @pytest.fixture(autouse=True)
    def _require_rank_bm25(self) -> None:
        pytest.importorskip("rank_bm25")
```

---

## Pattern T6: WEAK_CONFIDENCE_LEVEL_ASSERTION

**What it is**: `get_confidence_level` returns one of four specific strings. Asserting
`isinstance(level, str)` passes even if the function returns `"garbage"`.

**Bad**:

```python
def test_high_confidence(self, conf_engine):
    level = conf_engine.get_confidence_level(0.9)
    assert isinstance(level, str)
```

**Good**:

```python
def test_high_confidence(self, conf_engine):
    level = conf_engine.get_confidence_level(0.9)
    assert level == "high"  # 0.9 >= HIGH_CONFIDENCE_THRESHOLD (0.75)
```

**Known level boundaries** (from `ConfidenceEngine`):
- `>= 0.75` → `"high"`
- `>= 0.50` → `"medium"`
- `>= 0.25` → `"low"`
- `< 0.25` → `"very_low"`

---

## Pattern T7: UNVERIFIED_EXCEPTION_GUARD

**What it is**: `try/except SomeError: pass` wrapping a function call. If the production
code never raises that exception (e.g. returns an empty dict instead), the guard silently
swallows any real errors the test should have caught.

**Bad**:

```python
try:
    result = deduplicator.find_duplicates([f1, f2], min_text_length=10)
    assert isinstance(result, dict)
except ValueError:
    pass  # "sklearn may raise on small corpora" — but it doesn't
```

**Good**:

```python
result = deduplicator.find_duplicates([f1, f2], min_text_length=10)
assert isinstance(result, dict)
```

**Pre-generation check**: Before adding a try/except in a test, verify the production
code actually raises that exception. If it returns a sentinel value instead, remove the guard.

---

## Pattern T8: MISSING_IMPORT_GUARD

**What it is**: Test file imports an optional dependency at module level (top-level `import`
or `from X import Y`) without a `pytest.importorskip` guard. When the package is absent the
entire file fails with `ImportError` at collection time — silencing all tests in the file,
including ones that don't need the optional dep.

Distinct from T5 (IMPORTSKIP_SCOPE) which covers a *misplaced* guard; T8 covers a *missing*
guard entirely.

**Bad**:

```python
from rank_bm25 import BM25Okapi  # crashes entire file if rank_bm25 not installed

class TestBM25Search:
    def test_basic_search(self): ...

class TestFileValidator:      # doesn't use rank_bm25, but also silenced
    def test_validates_pdf(self): ...
```

**Good**:

```python
class TestBM25Search:
    @pytest.fixture(autouse=True)
    def _require_rank_bm25(self) -> None:
        pytest.importorskip("rank_bm25")

    def test_basic_search(self): ...

class TestFileValidator:      # unaffected; runs even without rank_bm25
    def test_validates_pdf(self): ...
```

**Optional dependencies in this project** (require guards — not installed unless
the corresponding extra is present). Matrix regenerated against `pyproject.toml`
on 2026-04-22 as part of E5:

| Import name | Extra(s) | Notes |
|-------------|----------|-------|
| `rank_bm25` | `search` | BM25 index |
| `sklearn` | `search`, `dedup-text` | Scikit-learn; transitively pulled by both extras |
| `llama_cpp` | `llama` | llama.cpp bindings |
| `mlx_lm` | `mlx` | Apple Silicon MLX |
| `anthropic` | `claude` | Anthropic SDK |
| `faster_whisper` | `media` | Speech-to-text; transitively pulls `torch` |
| `torch` | `media` (transitive), `dedup-image` | Guard even when not imported directly — collection fails when absent |
| `cv2` | `media` (via `opencv-python`) | OpenCV (video scene detection) |
| `scenedetect` | `media` | PySceneDetect |
| `imagededup` | `dedup-image` | Image deduplication |
| `h5py` | `scientific` | HDF5 |
| `netCDF4` | `scientific` | NetCDF |
| `ezdxf` | `cad` | DXF/DWG |

**Core deps — no guard needed**: `fitz` (PyMuPDF), `docx` (python-docx),
`openpyxl`, `pptx` (python-pptx), `ebooklib`, `bs4` (beautifulsoup4), `py7zr`,
`pypdf`, `rarfile` are in the base install.

**CI enforcement**: `tests/ci/test_optional_dep_guards.py` scans changed test
files for optional-dep imports that lack a `pytest.importorskip` guard. The
guardrail is diff-scoped; full-suite promotion is tracked in G4.

**Pre-generation check**: Before writing any test that imports from the optional list,
add a class-level `@pytest.fixture(autouse=True)` that calls
`pytest.importorskip("<package>")`. Never use a module-level guard in mixed files.

---

## Pattern T9: VACUOUS_TAUTOLOGY_ASSERTION

**What it is**: An assertion that is always true by mathematical definition — passes even
when the code is completely broken. Common forms:

- `assert len(results) >= 0` — `len()` is always ≥ 0 by definition
- `assert count >= 0` — counts are non-negative by definition
- `assert duration >= 0` — elapsed time is non-negative
- `assert total_size >= 0` — sizes are non-negative

The assertion provides zero signal: it passes whether `results` is empty, full, or even
`None` (which would have raised before the assertion).

**Bad**:

```python
results = searcher.find(query)
assert len(results) >= 0   # always True — len() is never negative

count = tracker.get_count()
assert count >= 0          # always True — counts are non-negative

duration = timer.elapsed()
assert duration >= 0       # always True — elapsed time is non-negative
```

**Good**:

```python
results = searcher.find(query)
assert len(results) >= 1   # lower bound > 0 is meaningful
assert len(results) == 3   # exact count is strongest

count = tracker.get_count()
assert count == expected_count   # assert the actual expected value

duration = timer.elapsed()
assert duration < 5.0      # upper bound is meaningful (not lower bound)
```

**Pre-generation check**: Before writing `assert X >= 0`, ask: *"Is X a length, count,
size, or duration? If yes — this assertion always passes. Assert a meaningful bound
(>= 1, == expected, < max) instead."*

**CI enforcement**: `test_changed_tests_have_no_vacuous_len_gte_zero_assertions` in
`tests/ci/test_test_quality_guardrails.py` — full-suite enforcement
(iterates every `*.py` under `tests/`, not just the diff). Detects both
`len(x) >= 0` and `0 <= len(x)` forms.

---

## Pattern T10: PREDICATE_MISSING_NEGATIVE_CASE

**What it is**: A predicate function (`_is_X`, `_has_X`, `_find_X`) in detector/guardrail
code is tested only with positive inputs — cases that *should* match. No test verifies
that nodes with the same surface shape but wrong context are correctly rejected. False
positives in guardrail code are silent: the rule fires on correct code, confusing authors
and eroding trust in the guardrail.

The canonical example: `_is_resolve_path_call` checked `node.func.attr == "resolve_path"`
without verifying the receiver was a known alias. Any `obj.resolve_path()` would match.
The bug survived because tests only covered `resolve_path(x)` (bare call) and
`my_alias(x)` (imported alias) — never `unrelated_service.resolve_path(x)`.

**Bad**:

```python
# Only positive cases — does not catch overly-permissive matching
def test_is_resolve_path_call_bare():
    src = "resolve_path(request.path)"
    ...
    assert _is_resolve_path_call(call, {"resolve_path"})

def test_is_resolve_path_call_alias():
    src = "rp(request.path)"
    ...
    assert _is_resolve_path_call(call, {"resolve_path", "rp"})
```

**Good**:

```python
# Positive cases as above, PLUS a false-positive rejection case
def test_is_resolve_path_call_does_not_match_arbitrary_receiver():
    # same method name, wrong receiver — must NOT match
    src = "some_service.resolve_path(request.path)"
    tree = ast.parse(src)
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    assert not _is_resolve_path_call(call, {"resolve_path"})
```

**Pre-generation check**: For every `_is_X` / `_has_X` predicate, ask:
- *"What node looks like a match but isn't?"* → write a test that asserts `False` for it.
- Specifically: if the predicate checks `node.func.attr == "X"`, test a node where
  the receiver is an unrelated object that happens to have a method named `X`.

**Applies to**: Any predicate used in AST-walking detector code, not just security detectors.

---

## Pattern T11: PRAGMA_ON_TESTED_BRANCH

**What it is**: `# pragma: no cover` added to a branch that is already exercised by a
dedicated test. The branch still executes (and counts toward runtime coverage), but the
pragma hides it from the coverage report — which means a future refactor can drop the
test without the coverage metric noticing.

**Bad** (from PR #140 `src/methodologies/johnny_decimal/categories.py`):

```python
def __eq__(self, other: object) -> bool:
    if not isinstance(other, JohnnyDecimalNumber):  # pragma: no cover
        return NotImplemented
    return self.area == other.area and ...
```

But `test_categories.py::test_jd_number_eq_not_implemented` explicitly calls
`num.__eq__("not a jd number")` and asserts `NotImplemented`. The pragma is wrong:
the branch is tested.

**Good**:

```python
def __eq__(self, other: object) -> bool:
    if not isinstance(other, JohnnyDecimalNumber):
        return NotImplemented   # no pragma — this branch is exercised by tests
    return self.area == other.area and ...
```

**Pre-generation check**: Before adding `# pragma: no cover`, search test files for the
enclosing function/method name. If any test references it, the pragma is wrong — either
the branch is tested, or the test is broken (fix the test, don't hide the branch).

```bash
# Quick check for the enclosing function
rg "def <function_name>\(" tests/
```

Legitimate uses of `# pragma: no cover`: truly unreachable defensive branches
(e.g. `if TYPE_CHECKING:`), platform-specific code blocks not exercised in CI, and
`@overload` stubs. When in doubt, write the test instead of adding the pragma.

---

## Pattern T12: FIXTURE_STATE_LEAK

**What it is**: A test snapshots shared singleton/module state with the intent to
restore it, but either (a) never uses the snapshot in teardown, (b) restores only the
top-level key leaving sub-modules patched, or (c) silently swallows setup failures
and still yields. Leaks cross-test state under xdist and produces order-dependent
failures.

This is distinct from xdist-safe-patterns Pattern 2 (which covers _missing_ restoration
of `sys.modules` sub-modules) — T12 adds the "snapshot-but-never-used" and "restoration
that partially succeeds" variants.

**Bad — snapshot never used** (from PR #61):

```python
@pytest.fixture
def clean_registry():
    original_providers = _registry.registered_providers  # snapshotted
    yield
    _registry._reset_for_testing()
    _registry._register_builtins()
    # original_providers never restored — any custom registrations at import time
    # are silently lost for the rest of the session
```

**Bad — partial restoration** (from PR #76):

```python
saved_sklearn = sys.modules.get("sklearn")
# ... mutate sys.modules["sklearn"] and sklearn.feature_extraction ...
if saved_sklearn is not None:
    sys.modules["sklearn"] = saved_sklearn
    # sys.modules["sklearn.feature_extraction"] still points at the mock —
    # later tests in this worker will load the stale mock
```

**Bad — swallow-and-yield** (from PR #76):

```python
@pytest.fixture
def ensure_nltk():
    try:
        nltk.download("punkt_tab", quiet=True)
    except Exception:
        pass  # silent failure — test still yields, assertion later fails opaquely
    yield
```

**Good**:

```python
# Use patch.dict for sys.modules — atomic restore on exit, including sub-modules
with patch.dict(sys.modules, {
    "sklearn": mock_sklearn,
    "sklearn.feature_extraction": mock_sklearn.feature_extraction,
    "sklearn.feature_extraction.text": mock_sklearn.feature_extraction.text,
}):
    yield mock_sklearn

# For singletons: restore the actual snapshot, not just reset
@pytest.fixture
def clean_registry():
    original = dict(_registry.registered_providers)
    try:
        yield
    finally:
        _registry._reset_for_testing()
        for name, provider in original.items():
            _registry.register(name, provider)
```

**Pre-generation check**: For every `<var> = sys.modules.get(...)` or
`<var> = <singleton>.<field>` in a test, ensure `<var>` is actually referenced in the
teardown. If it isn't, the snapshot is a no-op.

**Cross-reference**: `xdist-safe-patterns.md` Pattern 2 (sys.modules restoration) and
Pattern 3 (shared singletons).

---

## Pattern T13: HARDCODED_TEST_DATA_PATHS

**What it is**: Hardcoded `/tmp/`, `/dev/null`, or other absolute path literals inside
test _data_ (dataclass fields, fixture dicts, parametrize values). Ruff `S108` catches
`tempfile.mktemp` and direct `open()` on hardcoded temp paths, but misses string
literals passed to constructors. The G1 pre-commit hook greps diff for
`/tmp/|/Users/|/home/` but only scans _added_ lines — older pre-existing literals in a
file being edited are not re-checked.

Impact: Windows-unportable tests, xdist file collisions, and tests that pass locally
then fail in clean CI containers.

**Bad** (from PR #61):

```python
def test_update_patterns(self, tmp_path: Path) -> None:
    entry = FeedbackEntry(file_path="/tmp/f.txt")   # S108 misses this
    tracker.add(entry)

def test_null_output(self):
    with open("/dev/null", "w") as f:               # not portable to Windows CI
        process_file(f)
```

**Good**:

```python
def test_update_patterns(self, tmp_path: Path) -> None:
    entry = FeedbackEntry(file_path=str(tmp_path / "f.txt"))
    tracker.add(entry)

def test_null_output(self, tmp_path: Path):
    sink = tmp_path / "sink.txt"
    with sink.open("w") as f:
        process_file(f)
```

**Pre-generation check**: Any time you write a path string in a test, ask: *"Could
this be `tmp_path / 'X'` instead?"* If yes — use `tmp_path`. The only acceptable
hardcoded paths in tests are:
- `/` root-based fixtures that are explicitly path-traversal test inputs
  (e.g. `"/etc/passwd"` as an _input_ to a validator, never as an output target)
- Path-validation test constants clearly marked as adversarial inputs

---

## Pattern T14: GENERATOR_THROW_FALSE_RAISE

**What it is**: `(_ for _ in ()).throw(SomeError(...))` used as a mock side-effect.

Technically the expression _does_ raise — `generator.throw(exc)` on a fresh
generator-expression starts it and propagates the exception back to the caller.
The pattern is banned anyway because:

1. **It reads as a no-op.** The five-token idiom is easily misread as "create a
   generator and configure it to throw later" — which is exactly why reviewers
   repeatedly flag it as a bug (and why the original PR-review comment for this
   pattern was itself wrong about the runtime behavior).
2. **Clearer alternatives exist.** `MagicMock(side_effect=exc)` or a named
   `def _raise(): raise exc` helper conveys intent in one read.
3. **It silently couples test correctness to a Python-implementation detail** —
   `generator.throw` on an unstarted generator is "raises into the first yield
   point, which is the start of the body"; if that ever changes, every use of
   this idiom breaks at once.

**Bad** (from PR #82, scientific reader tests):

```python
sci_module.netCDF4.Dataset = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
```

**Good**:

```python
def _raise_oserror(*a, **k):
    raise OSError("boom")
sci_module.netCDF4.Dataset = _raise_oserror

# Or with Mock:
from unittest.mock import MagicMock
sci_module.netCDF4.Dataset = MagicMock(side_effect=OSError("boom"))
```

**Pre-generation check**: The literal pattern `(_ for _ in ()).throw` is always wrong
in a test mock. If you want a callable that raises, write a named function or use
`MagicMock(side_effect=...)`.

Automatable: a grep guardrail bans this pattern outright (see `tests/ci/test_test_quality_guardrails.py`).

---

## Pattern T15: MOCK_ASSERTION_AFTER_RAISE_IN_PYTEST_RAISES

**What it is**: A mock assertion (`mock.X.assert_called*(...)` or
`assert mock.X.called`) placed AFTER a top-level `raise` inside a
`with pytest.raises(...):` block. The `raise` terminates control flow,
so the mock assertion is unreachable — but reads as a real check.

PR-A's PT012 catches multi-statement `pytest.raises` blocks generally,
but is silenced inside the 11 sites that carry `# noqa: PT012` for
legitimately multi-statement bodies (transaction rollback,
context-manager exit semantics, `try/except` subclass-non-catching,
etc.). T15 closes that hole with a targeted AST rail.

**Bad**:

```python
with pytest.raises(ValueError):  # noqa: PT012 — context-manager exit semantics
    with manager() as m:
        do_setup()
        raise ValueError("boom")
        m.cleanup.assert_called_once()  # UNREACHABLE — never executes
```

**Good**:

```python
# Move the mock assertion AFTER the with-block exits.
with pytest.raises(ValueError):  # noqa: PT012 — context-manager exit semantics
    with manager() as m:
        do_setup()
        raise ValueError("boom")
m.cleanup.assert_called_once()  # exit-on-exception verified here
```

**Pre-generation check**: After writing a `pytest.raises` block, ask:
*"Is anything below the `raise` inside this block? If yes — those statements
won't run. Move them outside the `with` (where they execute after the
exception propagates) or delete them."*

**Mechanical rail**: `scripts/check_pytest_raises_hygiene.py` — AST visitor
that flags any mock assertion (recognised method names + bare
`assert <mock>.called`) appearing after a top-level `raise` inside a
`with pytest.raises(...):` block. Conservative: only top-level `raise`
counts as terminating, so conditional / nested raises don't false-flag.

---

## Rule of Thumb

For every assertion, ask:
1. **T1**: "Is isinstance the *only* assertion? If yes — use the Fix-by-type table above."
2. **T2**: "Did I call a mutating method? Did I verify the state changed?"
3. **T3**: "Did I verify mock call args, not just call count?"
4. **T4**: "Is this an `X or isinstance(X, T)` disjunction? Flatten to a specific check."
5. **T5**: "Is importorskip at module level in a mixed file? Move to class-level autouse."
6. **T7**: "Does this try/except guard an exception the production code actually raises?"
7. **T8**: "Does this test file import an optional dep at module level without a guard?"
8. **T9**: "Is `assert X >= 0` where X is a length/count/duration? Replace with a meaningful bound."
9. **T10**: "Is this a predicate in detector code? Add a negative case with the same surface shape but wrong context."
10. **T11**: "About to add `# pragma: no cover`? Grep tests for the enclosing function — if any hits, the pragma is wrong."
11. **T12**: "Snapshotted shared state? Make sure teardown actually restores the snapshot (including sub-module keys)."
12. **T13**: "Hardcoded path string in test data? Use `tmp_path` instead."
13. **T14**: "`(_ for _ in ()).throw(...)` — never. Use a named function or `MagicMock(side_effect=...)`."
14. **T15**: "Anything after a top-level `raise` inside a `with pytest.raises(...):`? Move it outside the block — it's unreachable."

**Last audited PR**: #140 (PR review cycle 2026-03-21 to 2026-04-21)
