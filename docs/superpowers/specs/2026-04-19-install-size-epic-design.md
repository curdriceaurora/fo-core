# Install Size Reduction Epic — Design Spec

**Date**: 2026-04-19
**Status**: Approved (rev 2 — reviewer corrections applied 2026-04-19)
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
RTF and pypdf into the reader dispatcher is an explicit sub-task of this epic (see
Section 2c).

---

## Decision: Option B

All non-scientific, non-CAD formats included in the default (with reader-dispatch wiring
tasks included). CAD stays optional because `ezdxf` hard-requires numpy (+74 MB total),
which would negate the numpy removal gain. Scientific stays optional (h5py + netCDF4 +
scipy = 201 MB, niche use case).

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

The same pattern applies to `services/search/` and `services/video/` if their
`__init__.py` files have unconditional numpy imports — audit and guard all of them
before dropping numpy from `pyproject.toml`.

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

The following files contain NLTK references outside `src/` and must all be updated.
This list is exhaustive (verified by `grep -r nltk tests/ docs/ CONTRIBUTING.md`):

**Test files** (remove fixtures, update or delete NLTK-specific tests):

- `tests/conftest.py` — remove `mock_nltk_tokenizer`, `mock_nltk_stopwords`, `mock_nltk_lemmatizer`, `mock_nltk_freqdist`, `mock_nltk_ensure_data_no_op`, `isolated_nltk_environment`
- `tests/integration/conftest.py` — remove `stub_nltk`, `ensure_nltk_available` fixtures
- `tests/utils/test_text_processing.py` — migrate from mock-based to real snowballstemmer
- `tests/utils/test_text_processing_hermeticity.py` — remove or rewrite
- `tests/unit/utils/test_text_processing.py` — migrate from mock-based to real snowballstemmer
- `tests/services/test_text_processor.py` — remove `ensure_nltk_data` patches
- `tests/services/test_text_processor_logging.py` — remove `ensure_nltk_data` patches
- `tests/integration/test_coverage_gap_supplements.py` — remove NLTK-specific coverage gaps
- All other integration tests patching `services.text_processor.ensure_nltk_data` — remove patches

**Documentation files**:

| File | Required change |
|------|----------------|
| `README.md` | Update extras table: add `media`, rename `dedup`→`dedup-text`/`dedup-image`, remove `archive`, remove NLTK first-run download callout |
| `docs/getting-started.md` | Update quick install commands and extras reference |
| `docs/developer/testing.md` | Remove `stub_nltk` fixture reference (line 166+) |
| `docs/CONFIGURATION.md` | Remove any NLTK corpus path or `ensure_nltk_data` references |
| `docs/setup/` | Remove `nltk.download(...)` setup step if present |
| `docs/reference/` | Update dependencies/extras reference page if present |
| `CONTRIBUTING.md` | Remove NLTK download step from dev setup (line 341+); update extras names |
| `pyproject.toml` | Update inline comments on moved/removed deps; update `deptry` package map if needed |
| `.github/workflows/ci-extras.yml` | Remove `audio`, `video`, `archive`, `dedup` matrix entries; add `media`, `dedup-text`, `dedup-image` entries |

---

## Section 4: Testing Strategy

### Behavioral regression fixtures (prerequisite)

Before removing NLTK, capture golden output for the three affected functions in
`utils/text_processing.py` under the **current** NLTK implementation:

- `clean_text_for_name("Studies in running and analysis")` → frozen expected string
- `extract_keywords("The quick brown fox jumps...")` → frozen expected list

These fixtures define the accepted behavioral difference between NLTK and the
snowballstemmer replacement. Tests pass if the new output matches the new fixture,
not the old NLTK output.

### Import-boundary smoke test (new exit gate)

Add a test (or CI step) that verifies the default install does not accidentally pull
in numpy-dependent code at import time:

```python
# tests/smoke/test_default_import_boundary.py
import sys
sys.modules["numpy"] = None  # block numpy

import core.organizer        # must not raise
import cli.dedupe_hash       # must not raise (hash-based dedup path)
from services.deduplication.detector import DuplicateDetector  # must not raise
```

This test must run in the `ci` marker set.

### Format libs now guaranteed present

Remove `pytest.importorskip` guards on py7zr, rarfile, mutagen, and tinytag in any
test files that currently guard them. They are now default deps. `striprtf` and `pypdf`
guards similarly removed.

### Extras validation workflow

Update `.github/workflows/ci-extras.yml` (not `extras-validation.yml` — that file does
not exist):

- Remove `audio`, `video`, `archive`, `dedup` matrix entries
- Add `media`, `dedup-text`, `dedup-image` entries with smoke canaries

### Install-size CI gate

Add a step to `ci.yml` on main-push that measures and enforces the install size floor:

```bash
pip install --target /tmp/fo_size_check "fo-core" -q
SIZE_MB=$(du -sm /tmp/fo_size_check | cut -f1)
echo "Default install: ${SIZE_MB} MB"
[ "$SIZE_MB" -le 185 ] || (echo "Install size ${SIZE_MB} MB exceeds 185 MB cap" && exit 1)
```

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
- [ ] `pip install fo-core` installs in ≤185 MB (enforced by CI size gate)
- [ ] `tests/smoke/test_default_import_boundary.py` passes with numpy blocked
- [ ] `pip install "fo-core[media]"` installs and canary imports pass
- [ ] `pip install "fo-core[dedup-text]"` installs and canary imports pass
- [ ] `pip install "fo-core[dedup-image]"` installs and canary imports pass
- [ ] `pymarkdown scan docs/` passes (zero violations)
- [ ] No remaining `nltk` or `ensure_nltk_data` references in `src/`
- [ ] No remaining unconditional `import numpy` reachable from a default install
- [ ] `.rtf` files routed through `utils/readers` dispatch table
- [ ] All updated doc files verified against source (D1 rule)
