# Install Size Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the default `fo-core` install from 206 MB to 181 MB by removing NLTK and numpy from default dependencies, adding lightweight format libraries (RTF, 7Z/RAR, audio metadata) to default, and restructuring optional extras.

**Architecture:** Prerequisites first (guard numpy-dependent `__init__.py` imports, capture golden fixtures), then NLTK replacement and pyproject.toml changes in tandem, then test/CI/docs cleanup. Each task is self-contained and commits on completion.

**Tech Stack:** `snowballstemmer` (replaces NLTK stemming), `striprtf` (RTF reader), stdlib `re` + `collections.Counter` (replaces NLTK tokenization + FreqDist), `pytest` subprocess fixture for numpy isolation smoke test.

**Spec:** `docs/superpowers/specs/2026-04-19-install-size-epic-design.md`

---

## Task 1: Capture golden fixtures for NLTK-affected functions

Run this **before any code changes**. Records current NLTK output so the behavioral
difference between NLTK and Snowball is explicit and reviewable.

**Files:**
- Create: `tests/utils/golden_nltk_output.py` (one-off capture script, deleted after use)
- Modify: `tests/unit/utils/test_text_processing.py` (add golden fixture constants)

- [ ] **Step 1: Run the capture script**

```bash
cd /path/to/fo-core
python - <<'EOF'
import sys
sys.path.insert(0, "src")
from utils.text_processing import get_unwanted_words, clean_text, extract_keywords

# Must have NLTK data available; if not, run: python -c "import nltk; nltk.download('stopwords'); nltk.download('punkt'); nltk.download('wordnet')"
print("=== get_unwanted_words() ===")
print(repr(sorted(get_unwanted_words())))

print("\n=== clean_text('Studies in running and analysis') ===")
print(repr(clean_text("Studies in running and analysis")))

print("\n=== extract_keywords('The quick brown fox jumps over the lazy dog') ===")
print(repr(extract_keywords("The quick brown fox jumps over the lazy dog")))
EOF
```

- [ ] **Step 2: Record outputs as OLD_* constants**

Open `tests/unit/utils/test_text_processing.py` and add near the top:

```python
# Golden outputs captured under NLTK implementation — kept as acknowledgement
# artifact showing the behavioral difference vs Snowball replacement.
_OLD_CLEAN_TEXT = "<paste actual output from Step 1>"
_OLD_EXTRACT_KEYWORDS = <paste actual output from Step 1>
```

- [ ] **Step 3: Write failing test stubs with placeholder values**

In `tests/unit/utils/test_text_processing.py` add:

```python
# OLD_* captured under NLTK — acknowledgement artifact only.
_OLD_CLEAN_TEXT = "<captured in Step 1>"
_OLD_EXTRACT_KEYWORDS = "<captured in Step 1>"

# NEW_* = expected Snowball output. Fill in after Task 4 is implemented:
# run the two functions and paste the repr() output here.
_NEW_CLEAN_TEXT = "FILL_AFTER_TASK_4"
_NEW_EXTRACT_KEYWORDS: list[str] = []


def test_clean_text_golden_snowball() -> None:
    assert clean_text("Studies in running and analysis") == _NEW_CLEAN_TEXT


def test_extract_keywords_golden_snowball() -> None:
    assert extract_keywords("The quick brown fox jumps over the lazy dog") == _NEW_EXTRACT_KEYWORDS
```

- [ ] **Step 4: Run the new tests (should FAIL — NLTK still active)**

```bash
pytest tests/unit/utils/test_text_processing.py::test_clean_text_golden_snowball \
       tests/unit/utils/test_text_processing.py::test_extract_keywords_golden_snowball -v
```

Expected: FAIL

> **Note:** `_NEW_CLEAN_TEXT` and `_NEW_EXTRACT_KEYWORDS` are filled in during Task 4
> Step 8 (after the Snowball implementation is in place). The test stubs are written
> now so git tracks the intent; actual values are captured from the working implementation.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/utils/test_text_processing.py
git commit -m "test(text): add Snowball golden fixture stubs (values filled in Task 4)"
```

---

## Task 2: Guard numpy-dependent imports in services/deduplication/__init__.py

**Prerequisite for removing numpy from pyproject.toml.** Without this guard, importing
`cli.dedupe_hash` (which imports `services.deduplication.detector`, which triggers
`__init__.py`) fails with `ModuleNotFoundError: numpy` on a default install.

**Files:**
- Modify: `src/services/deduplication/__init__.py`

- [ ] **Step 1: Write the failing import-boundary test**

```bash
python - <<'EOF'
import sys
sys.modules["numpy"] = None  # simulate numpy absent
try:
    from services.deduplication.detector import DuplicateDetector
    print("PASS - no numpy import triggered")
except Exception as e:
    print(f"FAIL: {e}")
EOF
```
Run from repo root with `PYTHONPATH=src`. Expected: FAIL (numpy import triggered).

- [ ] **Step 2: Apply the guard**

Open `src/services/deduplication/__init__.py`. The three unconditional numpy-dependent
imports are at lines 20–21 and 35:

```python
from .document_dedup import DocumentDeduplicator   # line 20
from .embedder import DocumentEmbedder             # line 21
from .semantic import SemanticAnalyzer             # line 35
```

Replace those three lines (keeping all other imports unchanged) with:

```python
# DocumentEmbedder, SemanticAnalyzer, DocumentDeduplicator require numpy
# (via sklearn/sentence-transformers). Guard so a default install without
# the dedup-text extra can still import the hash-based dedup path.
try:
    from .document_dedup import DocumentDeduplicator
    from .embedder import DocumentEmbedder
    from .semantic import SemanticAnalyzer
except ImportError:
    pass  # numpy not available; hash-based dedup still works
```

- [ ] **Step 3: Add a scope comment to document_dedup.py**

Open `src/services/deduplication/document_dedup.py` and add after the class definition
line of `DocumentDeduplicator`:

```python
# DocumentDeduplicator: requires dedup-text extra; CLI integration tracked separately
```

- [ ] **Step 4: Verify the guard works**

```bash
python - <<'EOF'
import sys
sys.modules["numpy"] = None
sys.path.insert(0, "src")
from services.deduplication.detector import DuplicateDetector
print("PASS")
EOF
```

Expected: `PASS`

- [ ] **Step 5: Run existing dedup tests to confirm no regression**

```bash
pytest tests/ -k "dedup" -x -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add src/services/deduplication/__init__.py src/services/deduplication/document_dedup.py
git commit -m "fix(dedup): guard numpy-dependent __init__ imports behind ImportError"
```

---

## Task 3: Guard numpy-dependent imports in services/search/__init__.py

The import chain `search/__init__.py` → `HybridRetriever` → `VectorIndex` →
`from numpy.typing import NDArray` means `import services.search` fails without numpy.

**Files:**
- Modify: `src/services/search/__init__.py`

- [ ] **Step 1: Confirm the failure**

```bash
python - <<'EOF'
import sys
sys.modules["numpy"] = None
sys.path.insert(0, "src")
import services.search
print("PASS")
EOF
```

Expected: FAIL (numpy.typing import triggered).

- [ ] **Step 2: Apply the guard**

`src/services/search/__init__.py` currently contains:

```python
from services.search.hybrid_retriever import HybridRetriever, read_text_safe
```

Replace with:

```python
# HybridRetriever requires numpy (via VectorIndex). Guard so a default
# install without the search extra can still import this package.
try:
    from services.search.hybrid_retriever import HybridRetriever, read_text_safe
except ImportError:
    pass  # numpy not available; search subsystem disabled
```

- [ ] **Step 3: Verify the guard works**

```bash
python - <<'EOF'
import sys
sys.modules["numpy"] = None
sys.path.insert(0, "src")
import services.search
print("PASS")
EOF
```

Expected: `PASS`

- [ ] **Step 4: Run existing search tests**

```bash
pytest tests/ -k "search" -x -q
```

Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/services/search/__init__.py
git commit -m "fix(search): guard numpy-dependent HybridRetriever import behind ImportError"
```

---

## Task 4: Replace NLTK with snowballstemmer in utils/text_processing.py

**Files:**
- Modify: `src/utils/text_processing.py`

- [ ] **Step 1: Replace the import block and module-level flags**

Open `src/utils/text_processing.py`. Lines 1–20 currently contain:

```python
import re
...
try:
    import nltk
    from nltk.corpus import stopwords
    from nltk.stem import WordNetLemmatizer
    from nltk.tokenize import word_tokenize
    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False

_nltk_ready: bool = False
```

Replace the NLTK try/except block and the `_nltk_ready` / `NLTK_AVAILABLE` declarations
with:

```python
from collections import Counter
from snowballstemmer import stemmer as Stemmer
```

Keep `import re` and any other existing stdlib imports. Remove `NLTK_AVAILABLE = True/False`
and `_nltk_ready` entirely.

- [ ] **Step 2: Delete the ensure_nltk_data() function**

Delete lines 23–95 (the entire `ensure_nltk_data()` function, including its docstring).
Confirm nothing else in this file calls `ensure_nltk_data`.

- [ ] **Step 3: Add the _ENGLISH_STOPWORDS module-level constant**

Add this constant immediately after the import block (before `get_unwanted_words`):

```python
_ENGLISH_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for",
    "from", "has", "he", "in", "is", "it", "its", "of", "on", "or",
    "that", "the", "to", "was", "will", "with", "i", "you", "we",
    "they", "she", "him", "her", "me", "us", "can", "could", "would",
    "should", "do", "does", "did", "have", "having", "not", "no",
    "nor", "so", "than", "too", "very", "just", "own", "same",
})
```

- [ ] **Step 4: Update get_unwanted_words()**

Find the NLTK block near the end of `get_unwanted_words()` (around line 237–244):

```python
# Add NLTK stopwords if available
if NLTK_AVAILABLE:
    try:
        unwanted.update(stopwords.words("english"))
    except LookupError:
        logger.warning("NLTK stopwords not available")
```

Replace with:

```python
unwanted.update(_ENGLISH_STOPWORDS)
```

- [ ] **Step 5: Update clean_text() — tokenization**

Find the tokenization block (around lines 275–286):

```python
# Tokenize
if NLTK_AVAILABLE:
    try:
        words = word_tokenize(text.lower())
    except LookupError:
        # Fallback if NLTK data not available
        words = text.lower().split()
else:
    words = text.lower().split()

# Filter alpha-only words
words = [word for word in words if word.isalpha()]
```

Replace with:

```python
# Tokenize — ASCII words only; unicode letters are excluded (accepted trade-off
# vs NLTK's unicode-aware word_tokenize; fo-core filenames are ASCII-dominated)
words = re.findall(r'\b[a-z]+\b', text.lower())
```

(The `re.findall` already yields only alpha tokens, so the `word.isalpha()` filter
line that followed is also deleted.)

- [ ] **Step 6: Update clean_text() — lemmatization**

Find the lemmatization block (around lines 288–294):

```python
# Lemmatize if available
if lemmatize and NLTK_AVAILABLE:
    try:
        lemmatizer = WordNetLemmatizer()
        words = [lemmatizer.lemmatize(word) for word in words]
    except (LookupError, ValueError, OSError) as e:
        logger.debug(f"Lemmatization failed: {e}")
```

Replace with:

```python
if lemmatize:
    _stemmer = Stemmer("english")
    words = [_stemmer.stemWord(word) for word in words]
```

Update the docstring parameter description for `lemmatize` from
`"Whether to lemmatize words"` to `"Whether to stem words (Snowball stemmer)"`.

- [ ] **Step 7: Rewrite extract_keywords()**

The current function has an early NLTK_AVAILABLE branch and uses `FreqDist`. Replace
the entire function body with:

```python
def extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """Extract the most frequent meaningful words from input text.

    Parameters:
        text (str): Text to analyze for keyword extraction.
        top_n (int): Number of top keywords to return.

    Returns:
        list[str]: Top `top_n` keywords ordered by frequency; returns an empty
        list if extraction fails or no keywords are found.
    """
    try:
        words = re.findall(r'\b[a-z]+\b', text.lower())
        words = [w for w in words if len(w) > 3]
        unwanted = get_unwanted_words()
        words = [w for w in words if w not in unwanted]
        return [word for word, _ in Counter(words).most_common(top_n)]
    except Exception as e:
        logger.debug(f"Keyword extraction failed: {e}")
        return []
```

- [ ] **Step 8: Run the golden fixture tests (should now PASS)**

```bash
pytest tests/unit/utils/test_text_processing.py::test_clean_text_golden_snowball \
       tests/unit/utils/test_text_processing.py::test_extract_keywords_golden_snowball -v
```

Expected: PASS

- [ ] **Step 9: Run the full text_processing test suite**

```bash
pytest tests/unit/utils/test_text_processing.py tests/utils/test_text_processing.py -x -v
```

Some existing mock-based tests will fail — that's expected. They are fixed in Task 9.
The golden fixture tests must pass.

- [ ] **Step 10: Commit**

```bash
git add src/utils/text_processing.py
git commit -m "feat(text): replace NLTK with snowballstemmer + re + Counter"
```

---

## Task 5: Remove ensure_nltk_data from services/text_processor.py

**Files:**
- Modify: `src/services/text_processor.py`

- [ ] **Step 1: Remove the import**

Find line 18:
```python
    ensure_nltk_data,
```
Delete it. Verify `ensure_nltk_data` does not appear anywhere else in the import block.

- [ ] **Step 2: Remove the call**

Find line 110:
```python
        # Ensure NLTK data is available
        ensure_nltk_data()
```
Delete both lines.

- [ ] **Step 3: Run text_processor tests**

```bash
pytest tests/services/test_text_processor.py tests/services/test_text_processor_logging.py -x -q
```

These tests patch `ensure_nltk_data` — they will fail until Task 9 removes those patches.
If they fail only on `ensure_nltk_data` patches, that is expected. Fix is in Task 9.

- [ ] **Step 4: Commit**

```bash
git add src/services/text_processor.py
git commit -m "fix(text_processor): remove ensure_nltk_data call — NLTK no longer required"
```

---

## Task 6: Update pyproject.toml — remove nltk/numpy, add default libs, restructure extras

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Remove from [project.dependencies]**

Find and delete these two lines from the `[project.dependencies]` list:
```toml
"nltk~=3.8",
"numpy~=1.24",
```

- [ ] **Step 2: Add new default dependencies**

Add to `[project.dependencies]` (alphabetical order within the list):
```toml
"mutagen~=1.47",
"py7zr>=0.20.0",
"pypdf~=6.10",
"rarfile~=4.1",
"snowballstemmer>=2.2.0",
"striprtf>=0.0.26",
"tinytag~=2.2",
```

- [ ] **Step 3: Restructure optional-dependencies**

In `[project.optional-dependencies]`:

**Remove** the `audio`, `video`, `archive`, and `dedup` extras entirely.

**Add** the following new/restructured extras:

```toml
media = [
    "faster-whisper~=1.0",
    "torch~=2.1",
    "pydub>=0.25.0",
    "opencv-python~=4.8",
    "scenedetect[opencv]>=0.6.0",
]
dedup-text = [
    "scikit-learn>=1.4,<1.9",
]
dedup-image = [
    "fo-core[dedup-text]",
    "imagededup>=0.3.0",
    "torch~=2.1",
]
```

**Update** the `all` meta-extra to reference new names:
```toml
all = [
    "fo-core[dev,cloud,llama,mlx,claude,media,dedup-text,dedup-image,scientific,cad,build,search]",
]
```

- [ ] **Step 4: Verify pyproject.toml parses cleanly**

```bash
python -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))"
```

Expected: no output (no parse errors).

- [ ] **Step 5: Verify default install resolves**

```bash
pip install --dry-run . 2>&1 | head -20
```

Expected: no errors about missing packages.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml
git commit -m "feat(deps): remove nltk+numpy from default; add snowballstemmer, striprtf, pypdf, py7zr, rarfile, mutagen, tinytag; restructure extras"
```

---

## Task 7: Add RTF reader to utils/readers/

**Files:**
- Modify: `src/utils/readers/documents.py`
- Modify: `src/utils/readers/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# In tests/utils/test_readers.py (or appropriate existing test file)
def test_read_rtf_file_returns_text(tmp_path: Path) -> None:
    rtf_file = tmp_path / "sample.rtf"
    rtf_file.write_bytes(
        rb"{\rtf1\ansi{\fonttbl\f0\fswiss Helvetica;}\f0\pard Hello RTF\par}"
    )
    from utils.readers import read_file
    result = read_file(rtf_file)
    assert "Hello RTF" in result
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/utils/test_readers.py::test_read_rtf_file_returns_text -v
```

Expected: FAIL (`.rtf` not in dispatch table)

- [ ] **Step 3: Add read_rtf_file() to documents.py**

Open `src/utils/readers/documents.py`. The existing reader functions follow this pattern:

```python
def read_<format>_file(file_path: str | Path, **kwargs: Any) -> str:
    file_path = Path(file_path)
    _check_file_size(file_path)
    try:
        # read and return str
    except Exception as exc:
        raise FileReadError(f"Failed to read {file_path.name}: {exc}") from exc
```

Add this function after the existing document readers (before any spreadsheet readers):

```python
def read_rtf_file(file_path: str | Path, max_chars: int = 50_000, **kwargs: Any) -> str:
    """Extract plain text from an RTF file using striprtf."""
    from striprtf.striprtf import rtf_to_text

    file_path = Path(file_path)
    _check_file_size(file_path)
    try:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        text = rtf_to_text(raw)
        return text[:max_chars] if len(text) > max_chars else text
    except Exception as exc:
        raise FileReadError(f"Failed to read RTF {file_path.name}: {exc}") from exc
```

- [ ] **Step 4: Add .rtf to the dispatch table in __init__.py**

Open `src/utils/readers/__init__.py`. Find the `readers` dict (around line 127). Add:

```python
    (".rtf",): read_rtf_file,
```

Also add `read_rtf_file` to the import from `documents.py` at the top of `__init__.py`.

- [ ] **Step 5: Run the test**

```bash
pytest tests/utils/test_readers.py::test_read_rtf_file_returns_text -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/utils/readers/documents.py src/utils/readers/__init__.py tests/utils/test_readers.py
git commit -m "feat(readers): add RTF reader using striprtf; wire .rtf into dispatch table"
```

---

## Task 8: Remove NLTK download steps from CI workflow files

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/ci-full.yml`
- Modify: `.github/workflows/pr-integration.yml`

- [ ] **Step 1: Audit all NLTK references**

```bash
rg 'nltk' .github/workflows/
```

Expected: lines in ci.yml (4 blocks), ci-full.yml (3 blocks), pr-integration.yml (1 block).

- [ ] **Step 2: Remove from ci.yml**

For each of the following blocks (appears 4 times in ci.yml), delete both the cache
step and the download step:

```yaml
      - name: Cache NLTK data
        uses: actions/cache@v4
        with:
          path: ~/nltk_data
          key: nltk-${{ runner.os }}-${{ hashFiles('**/pyproject.toml') }}
          restore-keys: nltk-${{ runner.os }}-
      - name: Download NLTK data
        run: python -c "import nltk; nltk.download('stopwords', quiet=True); nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True); nltk.download('wordnet', quiet=True)"
```

Delete all four pairs.

- [ ] **Step 3: Remove from ci-full.yml**

Same pattern — delete all three cache + download pairs.

- [ ] **Step 4: Remove from pr-integration.yml**

Same pattern — delete the one cache + download pair.

- [ ] **Step 5: Verify no NLTK references remain in workflows**

```bash
rg 'nltk' .github/workflows/
```

Expected: zero hits.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/ci-full.yml .github/workflows/pr-integration.yml
git commit -m "ci: remove NLTK data cache and download steps from all workflow files"
```

---

## Task 9: Remove NLTK fixtures from tests/conftest.py

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Delete the six NLTK fixture functions**

Open `tests/conftest.py`. The NLTK fixtures start after line 166
(`# NLTK test fixtures for hermeticity`). Delete:
- The comment block header
- `mock_nltk_tokenizer()` fixture
- `mock_nltk_stopwords()` fixture
- `mock_nltk_lemmatizer()` fixture
- `mock_nltk_freqdist()` fixture
- `mock_nltk_ensure_data_no_op()` fixture
- `isolated_nltk_environment()` fixture

Also delete the `from unittest.mock import MagicMock, patch` import if `MagicMock` and
`patch` are no longer used elsewhere in the file (check before deleting).

- [ ] **Step 2: Verify conftest imports still resolve**

```bash
python -c "import tests.conftest" 2>&1 || python -m pytest tests/conftest.py --collect-only -q 2>&1 | head -5
```

Expected: no import errors.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: remove NLTK mock fixtures from root conftest"
```

---

## Task 10: Remove NLTK fixtures from tests/integration/conftest.py

**Files:**
- Modify: `tests/integration/conftest.py`

- [ ] **Step 1: Delete stub_nltk and ensure_nltk_available**

Open `tests/integration/conftest.py`. Delete:
- `stub_nltk` fixture (line 157–161)
- `ensure_nltk_available` fixture (line 164–200, including the skip logic and download calls)

- [ ] **Step 2: Remove imports no longer needed**

If `nltk` is imported at the top of this file, delete that import.

- [ ] **Step 3: Verify collection succeeds**

```bash
pytest tests/integration/ --collect-only -q 2>&1 | tail -5
```

Expected: no `fixture 'stub_nltk' not found` or similar errors. (Tests that requested
`stub_nltk` as a parameter are fixed in Task 11.)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test: remove stub_nltk and ensure_nltk_available from integration conftest"
```

---

## Task 11: Remove all remaining NLTK fixture consumers and patches

**Files:** (discovered by rg — do not skip this audit)

- [ ] **Step 1: Find all remaining NLTK test consumers**

```bash
rg 'stub_nltk|ensure_nltk_available|ensure_nltk_data|NLTK_AVAILABLE|mock_nltk' tests/
```

For each file in the results:

- [ ] **Step 2: For each test file — remove fixture parameters**

Files that request `stub_nltk: None` as a parameter: remove the parameter from the
function signature. Example:

```python
# Before
def test_organize_creates_folders(
    tmp_path: Path,
    stub_nltk: None,
    stub_models: None,
) -> None:

# After
def test_organize_creates_folders(
    tmp_path: Path,
    stub_models: None,
) -> None:
```

- [ ] **Step 3: For each test file — remove ensure_nltk_data patches**

Lines like `with patch("services.text_processor.ensure_nltk_data"):` — delete the
`with` block and de-indent the body. Lines like
`mocker.patch("utils.text_processing.ensure_nltk_data")` — delete the statement.

- [ ] **Step 4: For tests/utils/test_text_processing_hermeticity.py**

This file tests NLTK-specific hermetic behavior. Delete it entirely — its purpose no
longer exists.

- [ ] **Step 5: For tests/utils/test_text_processing.py and tests/unit/utils/test_text_processing.py**

Remove mock-based tests that patch NLTK internals. Rewrite any that test
`clean_text` or `extract_keywords` to call the functions directly and assert the
Snowball output (use `_NEW_CLEAN_TEXT` / `_NEW_EXTRACT_KEYWORDS` from Task 1).

- [ ] **Step 6: For tests/integration/test_coverage_gap_supplements.py**

Remove the NLTK-specific coverage gap tests (any test that imports or patches
`ensure_nltk_data` or `NLTK_AVAILABLE`).

- [ ] **Step 7: Run the full test suite**

```bash
pytest tests/ -x -q --ignore=tests/extras --ignore=tests/smoke
```

Expected: all passing. Fix any remaining `fixture not found` errors by repeating
Steps 2–6 for any files the rg in Step 1 may have missed.

- [ ] **Step 8: Commit**

```bash
git add -u tests/
git commit -m "test: remove all NLTK fixture consumers and patches from test suite"
```

---

## Task 12: Remove importorskip guards for newly-default libraries

`py7zr`, `rarfile`, `mutagen`, `tinytag`, `striprtf`, `pypdf` are now default deps.
Tests that guard against their absence with `pytest.importorskip` should be cleaned up.

**Files:** (discovered by rg)

- [ ] **Step 1: Find all importorskip guards for default-dep libs**

```bash
rg 'importorskip.*py7zr|importorskip.*rarfile|importorskip.*mutagen|importorskip.*tinytag|importorskip.*striprtf|importorskip.*pypdf' tests/
```

- [ ] **Step 2: For each hit — remove the guard**

Delete the `pytest.importorskip(...)` call. The import will now succeed unconditionally.
If the guard was in a `@pytest.fixture(autouse=True)` that exists solely for the skip,
delete the fixture too.

- [ ] **Step 3: Run affected tests**

```bash
pytest tests/ -x -q --ignore=tests/extras --ignore=tests/smoke
```

Expected: all passing.

- [ ] **Step 4: Commit**

```bash
git add -u tests/
git commit -m "test: remove importorskip guards for libs now in default install"
```

---

## Task 13: Add import-boundary smoke test

Verifies no numpy-dependent code is reachable from the default install at import time.
Uses subprocess isolation so the `sys.modules["numpy"] = None` block does not affect
other tests in the same worker.

**Files:**
- Create: `tests/smoke/test_default_import_boundary.py`

- [ ] **Step 1: Create the test file**

```python
"""Verify that default-install imports do not require numpy."""
from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [pytest.mark.ci, pytest.mark.smoke]


def test_default_imports_without_numpy() -> None:
    """No module reachable from the default install should import numpy."""
    code = """
import sys
sys.modules["numpy"] = None  # block numpy

import core.organizer        # must not raise
import cli.dedupe_hash       # must not raise (hash-based dedup path)
from services.deduplication.detector import DuplicateDetector  # must not raise
import services.search        # must not raise (HybridRetriever guard required)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src"},  # adjust if project uses a different path
    )
    assert result.returncode == 0, (
        f"Default import triggered numpy dependency:\n{result.stderr}"
    )
```

- [ ] **Step 2: Run to verify it passes**

```bash
pytest tests/smoke/test_default_import_boundary.py -v
```

Expected: PASS (Tasks 2 and 3 already guard the numpy imports).

- [ ] **Step 3: Commit**

```bash
git add tests/smoke/test_default_import_boundary.py
git commit -m "test(smoke): add subprocess-isolated import-boundary test for numpy-free default install"
```

---

## Task 14: Update ci-extras.yml — rename matrix entries and canary files

**Files:**
- Modify: `.github/workflows/ci-extras.yml`
- Create: `tests/extras/test_extras_media.py`
- Create: `tests/extras/test_extras_dedup_text.py`
- Create: `tests/extras/test_extras_dedup_image.py`
- Delete: `tests/extras/test_extras_audio.py`
- Delete: `tests/extras/test_extras_video.py`
- Delete: `tests/extras/test_extras_dedup.py`
- Delete: `tests/extras/test_extras_archive.py`

- [ ] **Step 1: Create test_extras_media.py (merges audio + video canaries)**

```python
"""Smoke canary for the media extra (faster-whisper, torch, pydub, opencv, scenedetect)."""
from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def _require_media(tmp_path: Path) -> None:
    pytest.importorskip("faster_whisper")
    pytest.importorskip("cv2")
    pytest.importorskip("pydub")
    pytest.importorskip("scenedetect")


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(struct.pack("<" + "h" * 4410, *([0] * 4410)))


def test_pydub_reads_wav(tmp_path: Path) -> None:
    from pydub import AudioSegment

    wav_path = tmp_path / "clip.wav"
    _make_wav(wav_path)
    seg = AudioSegment.from_wav(str(wav_path))
    assert seg.frame_rate == 44100


def test_opencv_read_write_cycle(tmp_path: Path) -> None:
    import cv2
    import numpy as np

    video_path = str(tmp_path / "clip.mp4")
    out = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 64))
    for _ in range(5):
        out.write(np.zeros((64, 64, 3), dtype=np.uint8))
    out.release()

    cap = cv2.VideoCapture(video_path)
    frames = []
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)
    cap.release()
    assert len(frames) == 5


def test_faster_whisper_importable() -> None:
    from faster_whisper import WhisperModel  # noqa: F401
```

- [ ] **Step 2: Create test_extras_dedup_text.py**

```python
"""Smoke canary for the dedup-text extra (scikit-learn)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def _require_dedup_text() -> None:
    pytest.importorskip("sklearn")


def test_sklearn_importable() -> None:
    from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401


def test_document_embedder_importable() -> None:
    from services.deduplication.embedder import DocumentEmbedder  # noqa: F401


def test_semantic_analyzer_importable() -> None:
    from services.deduplication.semantic import SemanticAnalyzer  # noqa: F401
```

- [ ] **Step 3: Create test_extras_dedup_image.py**

```python
"""Smoke canary for the dedup-image extra (torch + imagededup)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def _require_dedup_image() -> None:
    pytest.importorskip("imagededup")
    pytest.importorskip("torch")


def test_imagededup_importable() -> None:
    from imagededup.methods import PHash  # noqa: F401


def test_image_deduplicator_importable() -> None:
    from services.deduplication.image_dedup import ImageDeduplicator  # noqa: F401
```

- [ ] **Step 4: Delete old canary files**

```bash
git rm tests/extras/test_extras_audio.py \
       tests/extras/test_extras_video.py \
       tests/extras/test_extras_dedup.py \
       tests/extras/test_extras_archive.py
```

- [ ] **Step 5: Update ci-extras.yml matrix**

In `.github/workflows/ci-extras.yml`, find the matrix `include` list. Remove entries for
`audio`, `video`, `archive`, and `dedup`. Add entries for the three new extras using the
same schema as existing entries. The `extra` field drives both the pip install and the
test file path — note that hyphenated names need a `test_file` override since Python
filenames cannot contain hyphens:

```yaml
        - extra: media
          key_import: "import faster_whisper; import cv2"
          smoke: "true"
          os: ubuntu-latest
          timeout_minutes: 30
        - extra: dedup-text
          key_import: "import sklearn"
          smoke: "true"
          test_file: tests/extras/test_extras_dedup_text.py
          os: ubuntu-latest
          timeout_minutes: 20
        - extra: dedup-image
          key_import: "import imagededup; import torch"
          smoke: "true"
          test_file: tests/extras/test_extras_dedup_image.py
          os: ubuntu-latest
          timeout_minutes: 40
```

If the workflow uses `tests/extras/test_extras_${{ matrix.extra }}.py` directly, add a
`test_file` field with a default expression so existing entries keep working:

```yaml
      - name: Run smoke canary
        if: matrix.smoke == 'true'
        run: |
          TEST_FILE="${{ matrix.test_file || format('tests/extras/test_extras_{0}.py', matrix.extra) }}"
          pytest "$TEST_FILE" -m "smoke" -x -v --override-ini="addopts="
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci-extras.yml \
        tests/extras/test_extras_media.py \
        tests/extras/test_extras_dedup_text.py \
        tests/extras/test_extras_dedup_image.py
git commit -m "ci: rename extras matrix entries and canary files for media/dedup-text/dedup-image"
```

---

## Task 15: Add install-size CI gate to ci.yml

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add the size-check job**

In `.github/workflows/ci.yml`, add a new job after the existing `test` job. The job
must run on both `pull_request` and `push` to main:

```yaml
  install-size-check:
    name: Default install size (≤185 MB)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Measure default install size (hermetic venv)
        run: |
          python -m venv /tmp/fo_size_venv
          /tmp/fo_size_venv/bin/pip install --target /tmp/fo_size_check . -q
          SIZE_MB=$(du -sm /tmp/fo_size_check | cut -f1)
          echo "Default install: ${SIZE_MB} MB"
          [ "$SIZE_MB" -le 185 ] || (echo "Install size ${SIZE_MB} MB exceeds 185 MB cap" && exit 1)
```

Confirm the `on:` trigger at the top of `ci.yml` includes both `pull_request` and
`push` (it almost certainly does — verify before adding).

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add hermetic install-size gate (≤185 MB) on pull_request and push"
```

---

## Task 16: Update user-facing documentation

**Files:**
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/USER_GUIDE.md`
- Modify: `docs/troubleshooting.md`
- Modify: `docs/developer/testing.md`
- Modify: `docs/CONFIGURATION.md` (if it mentions NLTK)
- Modify: `docs/setup/` files (if they mention nltk.download)

- [ ] **Step 1: Audit stale extra names in user-facing docs**

```bash
rg '\[audio\]|\[video\]|\[archive\]|\[dedup\]' \
  --glob '!docs/superpowers/specs/**' \
  docs/ README.md CONTRIBUTING.md
```

For each hit: replace with the new extra name (`[media]`, `[dedup-text]`, `[dedup-image]`).
Delete any `[archive]` install instructions (py7zr/rarfile are now default).

- [ ] **Step 2: Remove NLTK first-run download callout from README.md**

Find any mention of "NLTK downloads on first run", "nltk_data", or "corpus" and delete.
Update the extras table to show `media`, `dedup-text`, `dedup-image` instead of `audio`,
`video`, `dedup`.

- [ ] **Step 3: Update CONTRIBUTING.md dev setup**

Find the NLTK download step (around line 341). Delete:
```
python -c "import nltk; nltk.download('stopwords')..." 
```
or similar. Update any extras names in the dev setup instructions.

- [ ] **Step 4: Remove stub_nltk reference from docs/developer/testing.md**

Find any mention of `stub_nltk`, `ensure_nltk_available`, or NLTK fixture setup (line
166+). Delete or replace with a note that NLTK is no longer used.

- [ ] **Step 5: Run pymarkdown on all modified docs**

```bash
pymarkdown scan README.md CONTRIBUTING.md docs/getting-started.md \
  docs/USER_GUIDE.md docs/troubleshooting.md docs/developer/testing.md
```

Expected: zero violations. Fix any heading-level or blank-line violations before
committing.

- [ ] **Step 6: Commit**

```bash
git add README.md CONTRIBUTING.md docs/
git commit -m "docs: update extras names, remove NLTK references, update install instructions"
```

---

## Task 17: Run all exit gates

Confirm every exit condition from the spec is met.

- [ ] **Step 1: Full test suite**

```bash
pytest tests/ -m "ci or smoke" -x -q
```

Expected: all passing on Linux, macOS, Windows (CI will validate cross-platform).

- [ ] **Step 2: Import boundary smoke test**

```bash
pytest tests/smoke/test_default_import_boundary.py -v
```

Expected: PASS

- [ ] **Step 3: NLTK repo-wide audit**

```bash
rg -i 'nltk|ensure_nltk_data|stub_nltk|mock_nltk' \
  --glob '!docs/superpowers/specs/**' \
  src/ .github/ tests/ docs/ README.md CONTRIBUTING.md pyproject.toml
```

Expected: zero hits.

- [ ] **Step 4: Old extra name audit**

```bash
# Docs
rg '\[audio\]|\[video\]|\[archive\]|\[dedup\]' \
  --glob '!docs/superpowers/specs/**' \
  docs/ README.md CONTRIBUTING.md

# pyproject.toml extra definitions
rg '^\s*(audio|video|archive|dedup)\s*=' pyproject.toml

# CI matrix
rg '"audio"|"video"|"archive"|"dedup"' .github/workflows/ci-extras.yml

# Old canary files
ls tests/extras/test_extras_audio.py tests/extras/test_extras_video.py \
   tests/extras/test_extras_archive.py tests/extras/test_extras_dedup.py 2>&1
```

Expected: all four commands return zero hits / "No such file or directory".

- [ ] **Step 5: RTF dispatch check**

```bash
python -c "
import sys; sys.path.insert(0, 'src')
from utils.readers import read_file
from pathlib import Path
import tempfile, os
with tempfile.NamedTemporaryFile(suffix='.rtf', delete=False) as f:
    f.write(rb'{\rtf1\ansi Hello RTF\par}')
    name = f.name
result = read_file(Path(name))
os.unlink(name)
assert 'Hello RTF' in result, repr(result)
print('RTF dispatch: PASS')
"
```

- [ ] **Step 6: Unconditional numpy import check**

```bash
rg 'import numpy|from numpy' src/ --glob '!*.pyc' | rg -v 'try:|except ImportError'
```

Expected: any remaining hits should be inside `try:` blocks (optional extras code).
Inspect any hits outside try blocks.

- [ ] **Step 7: pymarkdown**

```bash
pymarkdown scan docs/
```

Expected: zero violations.

- [ ] **Step 8: Final commit if any cleanup needed**

```bash
git add -u
git commit -m "chore: final exit gate cleanup"
```
