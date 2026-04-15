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
