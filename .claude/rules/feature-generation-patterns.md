# Feature Generation Anti-Patterns

Reference ruleset for writing feature code that passes PR review without correction.
Sourced from CodeRabbit and Copilot review comments — fo-core is a **CLI-only** tool;
there are no web routes, no HTTP server, no plugin system.

---

## Pre-Generation Security Boundary Checklist

**Complete BEFORE writing any feature code touching paths, config, or external input:**

- [ ] User input used in file paths? → Validate against the configured organize root (see F4)
- [ ] Any secrets or API keys logged? → Audit all `logger.*` calls in new code
- [ ] Config read directly from `os.environ`? → Use `AppConfig` / `ConfigManager` instead
- [ ] Existing config system consulted? → Check `ConfigManager` before hardcoding any path
- [ ] Writing search/index code? → Apply search-specific checklist (see `search-generation-patterns.md`)

---

## Pattern F1: MISSING_ERROR_HANDLING — 53 findings

**What it is**: Error paths not implemented — exception raised by a dependency propagates
unhandled to the caller, with no wrapping or user-facing message.

**Bad**:

```python
def process_file(path: Path) -> ProcessResult:
    content = self.reader.read(path)  # raises FileNotFoundError if missing
    return self._analyze(content)
```

**Good**:

```python
def process_file(path: Path) -> ProcessResult:
    try:
        content = self.reader.read(path)
    except FileNotFoundError:
        logger.warning("File not found: %s", path)
        return ProcessResult(success=False, error=f"File not found: {path}")
    except PermissionError as e:
        logger.error("Permission denied reading %s: %s", path, e)
        raise ProcessingError(f"Cannot read {path}") from e
    return self._analyze(content)
```

**Pre-generation check**: For every external call (file I/O, DB, network, subprocess),
ask: *"What exception can this raise, and does my code handle it?"*

---

## Pattern F2: TYPE_ANNOTATION — 63 findings

**What it is**: Missing or incorrect type hints; `Any` used where a concrete type is
known; return type not declared. Mypy strict mode rejects these.

**Bad**:

```python
def get_metadata(file_path, config=None):
    result = self.processor.analyze(file_path)
    return result
```

**Good**:

```python
def get_metadata(
    file_path: Path,
    config: Optional[AppConfig] = None,
) -> FileMetadata:
    result: FileMetadata = self.processor.analyze(file_path)
    return result
```

**Pre-generation check**: Every function signature needs `->` return type. Every
parameter needs a type annotation. `Any` is only acceptable at external system
boundaries (e.g., JSON deserialization before validation).

---

## Pattern F3: THREAD_SAFETY — 64 findings

**What it is**: Race conditions, unprotected shared state, missing locks,
non-atomic read-modify-write on shared data.

**Bad**:

```python
# BAD — 'w' truncates file before flock; race window between truncate and lock
with open(cache_file, 'w') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    json.dump(data, f)

# BAD — non-atomic read-modify-write on shared counter
self.count += 1
```

**Good**:

```python
# GOOD — atomic write via temp file + rename
import tempfile, os
with tempfile.NamedTemporaryFile(
    mode='w', dir=cache_file.parent, delete=False, suffix='.tmp'
) as f:
    json.dump(data, f)
    tmp_path = f.name
os.replace(tmp_path, cache_file)  # atomic on POSIX

# GOOD — threading lock for shared counter
self._lock = threading.Lock()
with self._lock:
    self.count += 1
```

**Pre-generation checklist for shared state**:
- [ ] Multiple threads access this variable? → Lock required
- [ ] Read-modify-write sequence? → Must be atomic (lock or atomic op)
- [ ] File write? → Use temp file + `os.replace()` pattern. **This applies to
      every persistent file you write: cache files, embedding indexes, coverage
      baselines, module-floor snapshots, history databases, and one-off scripts
      that produce a file another tool consumes.** A mid-write crash or a
      concurrent CI job truncates the file; downstream tooling then reads garbage.
- [ ] `@lru_cache` on a function reading env vars? → Remove cache

---

## Pattern F4: SECURITY_VULN — 74 findings (highest-frequency)

**What it is**: Unsanitized path inputs (directory traversal), API keys in logs,
config bypassed by reading `os.environ` directly instead of using the injected
`AppConfig` / `ConfigManager` instance.

**Bad**:

```python
# BAD — user-supplied path used directly → directory traversal
def organize(target_dir: str) -> None:
    for f in Path(target_dir).rglob("*"):  # attacker passes "../../etc"
        self._process(f)

# BAD — API key logged in plain text
logger.info("Calling provider with key: %s", config.openai_api_key)

# BAD — reads env directly, bypasses AppConfig and test injection
class TextProcessor:
    def __init__(self) -> None:
        self._model = os.environ.get("FO_TEXT_MODEL", "llama3")
```

**Good**:

```python
# GOOD — validate path against configured organize root
def organize(target_dir: str, config: AppConfig) -> None:
    requested = Path(target_dir).resolve()
    allowed = config.organize_path.resolve()
    if not str(requested).startswith(str(allowed)):
        raise ValueError(f"Path {target_dir!r} is outside configured root")
    for f in requested.rglob("*"):
        self._process(f)

# GOOD — log provider name, never the key
logger.debug("Calling provider: %s", config.provider_name)

# GOOD — receive AppConfig through constructor injection
class TextProcessor:
    def __init__(self, config: AppConfig) -> None:
        self._model = config.text_model
```

**Security boundary checklist** (run before every new file-touching function):
- [ ] Path parameter from user/CLI? → Validate against `config.organize_path` before ops
- [ ] Any `logger.*` call near a key/token/secret field? → Log name/type only, not value
- [ ] Reading config values via `os.environ.get`? → Use injected `AppConfig` instead
- [ ] User input in SQL (history DB)? → Parameterized query, never f-string
- [ ] Writing search/index code? → Apply `search-generation-patterns.md` checklist

---

## Pattern F5: HARDCODED_VALUE — 36 findings

**What it is**: Magic strings/numbers inline; paths hardcoded instead of using the
config system.

**Bad**:

```python
TRASH_DIR = Path("~/.config/fo/trash").expanduser()
MAX_RETRIES = 3  # scattered throughout codebase
model = OllamaModel("qwen2.5:3b-instruct-q4_K_M")
```

**Good**:

```python
from config import ConfigManager
trash_dir = ConfigManager.get_path("trash")
max_retries = config.max_retries  # from AppConfig
model = OllamaModel(config.text_model)
```

**Pre-generation check**: Before hardcoding any string/number, ask:
*"Does `ConfigManager` or `AppConfig` (`src/config/schema.py`) already own this value?"*

---

## Pattern F7: RESOURCE_NOT_CLOSED — ~10 findings

**What it is**: File handles, DB connections, or async generators not wrapped in
context managers; missing `finally` blocks.

**Bad**:

```python
conn = db.connect()
results = conn.execute(query)
conn.close()  # never called if execute() raises
```

**Good**:

```python
with db.connect() as conn:
    results = conn.execute(query)
```

---

## Pattern F8: WRONG_ABSTRACTION — ~8 findings

**What it is**: Mixed concerns — business logic in the CLI command layer instead of
the service layer; presentation in the data layer.

**Bad**:

```python
# BAD — CLI command doing AI calls + file I/O + display directly
@app.command()
def organize(path: str) -> None:
    files = list(Path(path).rglob("*"))
    for f in files:
        new_name = call_ollama(f)   # AI call in CLI layer
        f.rename(f.parent / new_name)
        console.print(f"Renamed {f.name} → {new_name}")
```

**Good**:

```python
# GOOD — CLI delegates; service owns logic; CLI owns display
@app.command()
def organize(path: str) -> None:
    organizer = FileOrganizer(config=load_config())
    result = organizer.organize(Path(path))
    _display_result(result)

# Service owns business logic
class FileOrganizer:
    def organize(self, path: Path) -> OrganizeResult:
        files = self._collect_files(path)
        ...
```

---

## Pattern F9: DYNAMIC_IMPORT_ANTIPATTERN — 14 findings

**What it is**: Imports placed somewhere other than the top of the module — either
`__import__()` used inline in factory lambdas, or `from X import Y` / `import X`
statements deferred inside a function/method body. Breaks static analysis (mypy can't
see the binding), hides dependency graphs from tools like `deptry`, and forces the
import to re-execute on every call.

**Bad — `__import__()` inline**:

```python
@dataclass
class Config:
    dirs: Any = field(default_factory=lambda: __import__("platformdirs").user_data_dir("app"))
```

**Bad — function-scoped import** (from PR #48 transcriber/embedder/bm25):

```python
class Transcriber:
    def _load_model(self) -> None:
        from faster_whisper import WhisperModel   # deferred — mypy loses the type,
        self._model = WhisperModel(...)           # deptry can't see the dep

class BM25Index:
    def build(self, files: list[Path]) -> None:
        from rank_bm25 import BM25Okapi           # re-imports on every build call
        self._index = BM25Okapi(...)
```

**Good**:

```python
# Top-level import with an explicit optional-dependency guard
try:
    import platformdirs
    HAS_PLATFORMDIRS = True
except ImportError:
    platformdirs = None  # type: ignore[assignment]
    HAS_PLATFORMDIRS = False

@dataclass
class Config:
    dirs: str = field(default_factory=lambda: platformdirs.user_data_dir("app"))

# For optional deps that genuinely require lazy loading (large models, CLI startup
# time), wrap the import in a module-level helper so it executes at most once and the
# rationale is documented:
_WhisperModel = None  # type: ignore[assignment]

def _get_whisper_model_class():
    """Lazy import: faster_whisper pulls in torch (~1 GB) and delays CLI startup."""
    global _WhisperModel
    if _WhisperModel is None:
        from faster_whisper import WhisperModel  # documented lazy loader
        _WhisperModel = WhisperModel
    return _WhisperModel
```

**Pre-generation check**:
- Never use `__import__()` inline.
- Never write `from X import Y` or `import X` inside a function body unless you are
  implementing a _documented_ lazy loader (expensive import, CLI startup cost, or
  unavoidable circular dependency). If you do, wrap it in a module-level helper with
  a one-line comment explaining why the top-level import was rejected.
- For optional deps, the correct pattern is a top-level `try/except ImportError` with
  an `HAS_X` flag — not a deferred in-function import.

---

## Pattern F10: DOCSTRING_DRIFT

**What it is**: Docstring describes old behavior after the implementation changed.

**Bad**:

```python
def _init_text_processor(self) -> None:
    """On failure (Ollama unavailable), resets to None."""
    try:
        ...
    except Exception as e:  # catches ValueError, ImportError, etc. too
        self.text_processor = None
```

**Good**:

```python
def _init_text_processor(self) -> None:
    """On any initialization failure (Ollama unavailable, config errors,
    import errors), resets to None and falls back to no-text mode."""
    try:
        ...
    except Exception as e:
        self.text_processor = None
```

**Pre-generation check**: When changing an `except` clause, return type, or control
flow, re-read the docstring and update it to match.

---

## Rule of Thumb

For every new feature:

1. **F4**: Path from user input? Config bypassed? API key near a logger? → Security checklist
2. **F3**: Shared state? → Lock required; file write (cache/index/baseline/script output)? → temp + `os.replace()`
3. **F1**: Every external call → what exception, is it handled? Does the `except` leave any symbol undefined for downstream use?
4. **F2**: Every function signature → concrete `->` return type
5. **F5**: `ConfigManager` / `AppConfig` already own this value?
6. **F9**: `__import__()` inline OR `from X import Y` inside a function body? → Top-level import (or documented lazy-loader helper)
7. **F10**: Changed `except` / return type / control flow? → Update docstring
8. **F4+**: Writing search/index code? → Apply `search-generation-patterns.md`

**Last audited PR**: #140 (PR review cycle 2026-03-21 to 2026-04-21)
