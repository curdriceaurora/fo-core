# Install Size Reduction Epic — Design Spec

**Date**: 2026-04-19
**Status**: Approved (rev 6 — reviewer corrections applied 2026-04-19)
**Target**: Reduce default install from 206 MB → 181 MB while maximising out-of-the-box
format coverage.

---

## Problem

The default install is 206 MB for a privacy-first CLI file organiser. The two largest
avoidable costs are:

1. **NLTK** (18 MB package + ~50 MB runtime corpus download on first run) — used only
   for tokenisation, stopwords, and lemmatisation in `utils/text_processing.py`. All
   three use cases are replaceable with stdlib `re` + `snowballstemmer` (1.9 MB, no
   downloads). NLTK's runtime download behaviour is the root cause of 23 CI fix commits
   to date.

2. **numpy** (33 MB) — declared as an explicit default dep but consumed exclusively by
   `services/deduplication/`, `services/search/`, and `services/video/` — all optional
   subsystems. Removing it from the default dep list costs nothing for the organize pass.
   It re-enters only when an optional extra that needs it is installed.

Additionally, several lightweight format libraries (7Z/RAR, audio metadata) are gated
behind optional extras despite having negligible size and no heavy transitive deps.
Moving them to the default extends format coverage at minimal cost.

**Scope clarification**: RTF (`striprtf`) and `pypdf` are also moved to the default dep
list, but this does **not** automatically extend the `utils.readers` dispatch table —
those libs are currently only consumed by `services/deduplication/extractor.py`. Wiring
RTF into the reader dispatcher is an explicit sub-task of this epic (see Section 2d).
pypdf reader dispatch remains out of scope (see Section 5).

---

## Decision: Option B

All **lightweight** non-scientific, non-CAD formats included in the default: document
formats (RTF via striprtf), archives (7Z, RAR), and audio metadata (mutagen, tinytag).
`pypdf` is also moved to default but only improves the dedup extractor path — the
organize reader still routes PDFs through PyMuPDF and full pypdf reader dispatch is
out of scope (see Section 5).
CAD stays optional because `ezdxf` hard-requires numpy (+74 MB total), which would negate
the numpy removal gain. Scientific stays optional (h5py + netCDF4 + scipy = 201 MB, niche
use case). Audio/video **transcription and processing** (faster-whisper, torch, pydub,
opencv, scenedetect) remain under the `media` extra — these are explicitly not in the
default despite "audio" metadata being default.

---

## Measured Install Sizes

**Methodology**: `pip install --target <tmpdir> <packages> -q`, then `du -sh <tmpdir>`.
Platform: macOS Darwin 25.4.0, Python 3.12.13, pip 24.x, no pre-cached wheels.
All sizes are post-install on-disk (not download size). Transitive deps included.

| Configuration | Installed size | Packages |
|---------------|---------------|----------|
| Current default | 206 MB | ~55 direct / ~409 files |
| **New default (this epic)** | **181 MB** | ~59 direct / ~400 files |
| `cad` addon | +74 MB | ezdxf + numpy |
| `dedup-text` addon | +179 MB | scikit-learn |
| `dedup-image` addon | +~600 MB on top of dedup-text | torch + imagededup |
| `media` addon | +409 MB | faster-whisper + torch + pydub + opencv + scenedetect |
| `scientific` addon | +201 MB | h5py + netCDF4 + scipy |
| `cloud` / `claude` / `llama` / `mlx` | +13 / +8 / +33 / +4.5 MB | (unchanged) |

CI enforces the ≤185 MB floor via an install-size check step added to `ci.yml`
(see Section 4).

---

## Section 1: Dependency Changes (`pyproject.toml`)

### Remove from `[project.dependencies]`

```toml
"nltk~=3.8"     # replaced by stdlib re + snowballstemmer
"numpy~=1.24"   # only used by optional subsystems (dedup/search/video)
```

### Add to `[project.dependencies]`

```toml
"snowballstemmer>=2.2.0"  # replaces NLTK lemmatiser; no corpus download
"striprtf>=0.0.26"        # RTF text extraction; used by dedup extractor + new reader
"pypdf~=6.10"             # PDF text extraction fallback; used by dedup extractor
"py7zr>=0.20.0"           # 7Z archive support (was in archive extra)
"rarfile~=4.1"            # RAR archive support (was in archive extra)
"mutagen~=1.47"           # audio metadata — MP3/FLAC/MP4 tags (was in audio extra)
"tinytag~=2.2"            # lightweight audio metadata fallback (was in audio extra)
```

### Extra restructuring

| Old extra | New extra | Change |
|-----------|-----------|--------|
| `audio` + `video` | `media` | Merged: faster-whisper, torch, pydub, opencv-python, scenedetect |
| `dedup` | `dedup-text` | sklearn only (imagededup removed — it requires torch) |
| *(new)* | `dedup-image` | torch + imagededup (~600 MB); declares `fo-core[dedup-text]` as a pip dep so sklearn comes in automatically |
| `archive` | *(removed)* | py7zr + rarfile absorbed into default |
| `cad` | `cad` | Unchanged: ezdxf + numpy (74 MB total) |
| `scientific` | `scientific` | Unchanged: h5py + netCDF4 + scipy |
| `all` | `all` | Updated to reference new extra names |

### Updated `all` meta-extra

```toml
all = [
    "fo-core[dev,cloud,llama,mlx,claude,media,dedup-text,dedup-image,scientific,cad,build,search]",
]
```

---

## Section 2: Code Changes

### 2a. `src/utils/text_processing.py` — NLTK replacement

This is a **behavior-changing substitution**, not a drop-in equivalence swap:

- `word_tokenize` (unicode-aware, handles punctuation) → `re.findall(r'\b[a-z]+\b', text.lower())` — ASCII-only, drops unicode letters
- `WordNetLemmatizer` (true lemmatisation: `studies` → `study`) → `Stemmer("english").stemWord()` (Snowball stemming: `studies` → `studi`)

Accepted trade-offs: stemming is sufficient for filename/folder name generation; the
ASCII regex is acceptable because fo-core operates on filenames which are typically
ASCII-dominated. **Golden-output fixtures must be written before the replacement**
(see Section 4 — behavioral regression tests) to define and freeze the accepted output
difference.

| NLTK call | Replacement |
|-----------|-------------|
| `import nltk` + corpus imports | `import re`, `from collections import Counter`, `from snowballstemmer import stemmer as Stemmer` |
| `word_tokenize(text)` | `re.findall(r'\b[a-z]+\b', text.lower())` |
| `stopwords.words("english")` | Inline `frozenset` (~55 words, ported from `tests/conftest.py` `mock_nltk_stopwords`) |
| `WordNetLemmatizer().lemmatize(word)` | `Stemmer("english").stemWord(word)` |
| `FreqDist(words)` | `Counter(words)` |
| `ensure_nltk_data()` | Delete entirely |
| `NLTK_AVAILABLE` flag | Delete entirely |
| `_nltk_ready` guard | Delete entirely |

### 2b. `src/services/text_processor.py`

Remove any NLTK import or `ensure_nltk_data()` call. Verify no remaining NLTK references.

### 2c. numpy — `services/deduplication/__init__.py` must be guarded first

**This is a prerequisite for dropping numpy from defaults.** The current `__init__.py`
unconditionally imports `DocumentEmbedder`, `SemanticAnalyzer`, and `DocumentDeduplicator`
at module load time. All three import numpy. Because `cli/dedupe_hash.py` imports
`services.deduplication.detector`, which triggers `__init__.py`, a default install
without numpy fails on import before any optional dedup-text feature is reached.

Required change: make the heavy numpy-dependent exports lazy or guarded in
`services/deduplication/__init__.py`:

```python
# Heavy optional exports — only available when dedup-text extra is installed
try:
    from .document_dedup import DocumentDeduplicator
    from .embedder import DocumentEmbedder
    from .semantic import SemanticAnalyzer
except ImportError:
    pass  # numpy not available; hash-based dedup still works
```

The same guard is **required** for `services/search/` before dropping numpy from
`pyproject.toml`. The current import chain is unconditional:
`services/search/__init__.py` → `HybridRetriever` → `VectorIndex` →
`from numpy.typing import NDArray`. Apply the same `try/except ImportError` pattern
to `services/search/__init__.py`:

```python
# Heavy optional exports — only available when search extra is installed
try:
    from .hybrid_retriever import HybridRetriever, read_text_safe
except ImportError:
    pass  # numpy/sklearn not available; search subsystem disabled
```

Apply the same audit to `services/video/` if its `__init__.py` has unconditional
numpy imports — guard before dropping numpy from `pyproject.toml`.

### 2d. RTF reader wiring in `utils/readers`

`striprtf` is moved to the default dep list, but the reader dispatch table in
`utils/readers/__init__.py` does not currently route `.rtf` files. Add:

1. A `read_rtf_file()` function in `utils/readers/documents.py` using `striprtf`
2. A `.rtf` entry in the dispatch table in `utils/readers/__init__.py`

This is the specific surface where "RTF in the default install" becomes real for
the organise pass.

### 2e. `DocumentDeduplicator` — scope boundary

`services/deduplication/document_dedup.py` exports `DocumentDeduplicator` but it is
never called from any CLI command or the organizer. This epic does **not** wire it up.
After the `__init__.py` guard is added (Section 2c), add a comment:

```python
# DocumentDeduplicator: requires dedup-text extra; CLI integration tracked separately
```

### 2f. `tqdm` — no change

Used only in `cli/dedupe_hash.py`. At 200 KB it is not worth conditionalising.

---

## Section 3: Documentation Updates

All documentation changes ship in the same PR as the code changes. No deferred doc PRs.

### NLTK reference audit

Use `rg` to find every NLTK reference outside `src/` — do not rely on a static list,
as new fixture consumers are regularly added:

**Step 1 — find all NLTK test consumers (must all be updated):**

```bash
rg 'stub_nltk|ensure_nltk_available|ensure_nltk_data|NLTK_AVAILABLE|mock_nltk' tests/
```

For each hit:
- Fixture definitions (`conftest.py`) → remove the fixture
- Fixture parameters (`stub_nltk: None`, `mock_nltk_tokenizer`) → remove the parameter and any mock-dependent assertions
- Direct patches (`patch("...ensure_nltk_data")`) → remove the patch and rewrite the test against real snowballstemmer output

**Step 2 — find all NLTK CI workflow steps (must all be removed):**

```bash
rg 'nltk' .github/workflows/
```

Remove every `cache: nltk_data` step and every `python -c "import nltk; nltk.download(...)"` step from
`ci.yml`, `ci-full.yml`, and `pr-integration.yml`. These steps fail once `nltk` is no
longer in the default dependencies.

**Step 3 — documentation files:**

| File | Required change |
|------|----------------|
| `README.md` | Update extras table: add `media`, rename `dedup`→`dedup-text`/`dedup-image`, remove `archive`, remove NLTK first-run download callout |
| `docs/getting-started.md` | Update quick install commands and extras reference |
| `docs/USER_GUIDE.md` | Update `[audio]`, `[video]`, `[dedup]`, `[archive]` references to new extra names |
| `docs/troubleshooting.md` | Update stale install commands that reference removed/renamed extras |
| `docs/developer/testing.md` | Remove `stub_nltk` fixture reference (line 166+) |
| `docs/CONFIGURATION.md` | Remove any NLTK corpus path or `ensure_nltk_data` references |
| `docs/setup/` | Remove `nltk.download(...)` setup step if present |
| `docs/reference/` | Update dependencies/extras reference page if present |
| `CONTRIBUTING.md` | Remove NLTK download step from dev setup (line 341+); update extras names |
| `pyproject.toml` | Update inline comments on moved/removed deps; update `deptry` package map if needed |
| `.github/workflows/ci-extras.yml` | Remove `audio`, `video`, `archive`, `dedup` matrix entries; add `media`, `dedup-text`, `dedup-image` entries (see canary file note below) |

**Step 4 — stale extra-name audit across user-facing docs:**

```bash
rg '\[audio\]|\[video\]|\[archive\]|\[dedup\]' \
  --glob '!docs/superpowers/specs/**' \
  docs/ README.md CONTRIBUTING.md
```

Every hit must be updated in this PR. The spec file itself is excluded because it
intentionally contains these names in the migration table.

---

## Section 4: Testing Strategy

### Behavioral regression fixtures (prerequisite)

Before removing NLTK, run the three affected public functions under the **current**
NLTK implementation and record both the old (NLTK) output and the expected new
(Snowball) output:

- `get_unwanted_words()` — record the NLTK stopwords set as `OLD_STOPWORDS`; the
  new output is the inline `frozenset` defined in Section 2a (deterministic, no
  NLTK needed).
- `clean_text("Studies in running and analysis")` — record both the NLTK output
  (`OLD_CLEAN`) and the expected Snowball output (`NEW_CLEAN`).
- `extract_keywords("The quick brown fox jumps over the lazy dog")` — record both
  `OLD_KEYWORDS` and `NEW_KEYWORDS`.

The test suite then contains **two** fixtures per function:
1. A snapshot test asserting the post-replacement output equals `NEW_*` (regression
   guard for the Snowball path).
2. A comment or removed test showing `OLD_*` as an acknowledgement artifact, so the
   behavioral difference is explicit and reviewable.

Tests pass when the new implementation matches `NEW_*`, not `OLD_*`.

### Import-boundary smoke test (new exit gate)

Add a test that verifies the default install does not accidentally pull in
numpy-dependent code at import time. The numpy block must run in a **subprocess**
so it does not poison `sys.modules` for other tests in the same pytest worker:

```python
# tests/smoke/test_default_import_boundary.py
import subprocess
import sys
import pytest

pytestmark = [pytest.mark.ci, pytest.mark.smoke]


def test_default_imports_without_numpy() -> None:
    code = """
import sys
sys.modules["numpy"] = None  # block numpy

import core.organizer        # must not raise
import cli.dedupe_hash       # must not raise (hash-based dedup path)
from services.deduplication.detector import DuplicateDetector  # must not raise
import services.search        # must not raise (HybridRetriever guard required)
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
```

Running in a child process isolates the `sys.modules` mutation completely.

### Format libs now guaranteed present

Remove `pytest.importorskip` guards on py7zr, rarfile, mutagen, and tinytag in any
test files that currently guard them. They are now default deps. `striprtf` and `pypdf`
guards similarly removed.

### Extras validation workflow

Update `.github/workflows/ci-extras.yml` (not `extras-validation.yml` — that file does
not exist):

- Remove `audio`, `video`, `archive`, `dedup` matrix entries
- Add `media`, `dedup-text`, `dedup-image` entries with smoke canaries

**Canary file renaming is required.** The workflow resolves test files via
`tests/extras/test_extras_${{ matrix.extra }}.py`. Hyphenated extra names
(`dedup-text`, `dedup-image`) do not map to valid Python filenames. Rename as follows
and update the workflow's `test_file` field (or add an explicit mapping) accordingly:

| Old canary file | Old extra | New canary file | New extra |
|----------------|-----------|----------------|-----------|
| `tests/extras/test_extras_audio.py` | `audio` | `tests/extras/test_extras_media.py` | `media` |
| `tests/extras/test_extras_video.py` | `video` | *(merged into test_extras_media.py)* | `media` |
| `tests/extras/test_extras_dedup.py` | `dedup` | `tests/extras/test_extras_dedup_text.py` | `dedup-text` |
| *(new)* | — | `tests/extras/test_extras_dedup_image.py` | `dedup-image` |
| `tests/extras/test_extras_archive.py` | `archive` | *(delete — py7zr/rarfile now default)* | — |

### Install-size CI gate

Add a step to `ci.yml` on main-push that measures and enforces the install size floor.
The gate must install from the **local checkout** (not from PyPI) so the measured
artifact matches the branch under test:

```bash
# Run in a clean environment so dev/search extras installed by earlier CI
# steps do not undercount the real default install surface.
python -m venv /tmp/fo_size_venv
/tmp/fo_size_venv/bin/pip install --target /tmp/fo_size_check . -q
SIZE_MB=$(du -sm /tmp/fo_size_check | cut -f1)
echo "Default install: ${SIZE_MB} MB"
[ "$SIZE_MB" -le 185 ] || (echo "Install size ${SIZE_MB} MB exceeds 185 MB cap" && exit 1)
```

The workflow step must run after `actions/checkout` so `.` resolves to the checked-out
branch, not a published release. The dedicated venv prevents earlier CI pip installs
(dev extras, test deps) from satisfying transitive deps and masking the true size.

### Coverage

No coverage floor changes expected. If the NLTK removal causes a shift in line coverage
(the `ensure_nltk_data` download branches are deleted, not just unreachable), adjust the
floor after measuring on CI.

---

## Section 5: What This Does Not Include

- **Wiring `DocumentDeduplicator` into the organize pass** — separate epic
- **CAD in default** — ezdxf's numpy dep makes this a net regression; revisit if ezdxf drops the numpy requirement
- **pypdf in the reader dispatch** — pypdf is moved to default for the dedup extractor path only; full PDF reader fallback via pypdf is out of scope
- **Further splitting of PyMuPDF or python-pptx into optional extras** — deferred
- **ML near-duplicate detection on every organize pass** — separate epic

---

## Exit Gates

- [ ] `pytest tests/ -m "ci or smoke"` passes on Linux, macOS, Windows
- [ ] CI install-size gate passes (hermetic venv install from local checkout ≤185 MB; see Section 4)
- [ ] `tests/smoke/test_default_import_boundary.py` passes with numpy blocked
- [ ] `pip install "fo-core[media]"` installs and canary imports pass
- [ ] `pip install "fo-core[dedup-text]"` installs and canary imports pass
- [ ] `pip install "fo-core[dedup-image]"` installs and canary imports pass
- [ ] `pymarkdown scan docs/` passes (zero violations)
- [ ] No remaining `nltk` or `ensure_nltk_data` references in `src/`
- [ ] `rg 'nltk|ensure_nltk_data|stub_nltk|mock_nltk|NLTK_AVAILABLE' --glob '!docs/superpowers/specs/**' .github/ tests/ docs/ README.md CONTRIBUTING.md` returns zero hits
- [ ] No remaining unconditional `import numpy` reachable from a default install
- [ ] `.rtf` files routed through `utils/readers` dispatch table
- [ ] All updated doc files verified against source (D1 rule)
- [ ] `rg '\[audio\]|\[video\]|\[archive\]|\[dedup\]' --glob '!docs/superpowers/specs/**' docs/ README.md CONTRIBUTING.md` returns zero hits
