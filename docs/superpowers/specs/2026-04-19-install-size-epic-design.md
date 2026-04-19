# Install Size Reduction Epic — Design Spec

**Date**: 2026-04-19
**Status**: Approved
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

Additionally, several lightweight format libraries (RTF, 7Z/RAR, audio metadata) are
gated behind optional extras despite having negligible size and no heavy transitive deps.
Moving them to the default gives users complete non-scientific format coverage immediately.

---

## Decision: Option B

All non-scientific, non-CAD formats included in the default. CAD stays optional because
`ezdxf` hard-requires numpy (+74 MB total), which would negate the numpy removal gain.
Scientific stays optional (h5py + netCDF4 + scipy = 201 MB, niche use case).

---

## Measured Install Sizes

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
"striprtf>=0.0.26"        # RTF format support (was in dedup extra)
"pypdf~=6.10"             # PDF text extraction fallback (was in dedup extra)
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

Replace every NLTK call with stdlib or snowballstemmer equivalents:

| NLTK call | Replacement |
|-----------|-------------|
| `import nltk` + corpus imports | `import re`, `from collections import Counter`, `from snowballstemmer import stemmer` |
| `word_tokenize(text)` | `re.findall(r'\b[a-z]+\b', text.lower())` |
| `stopwords.words("english")` | Inline frozen set (~55 words, ported from `tests/conftest.py` `mock_nltk_stopwords`) |
| `WordNetLemmatizer().lemmatize(word)` | `Stemmer("english").stemWord(word)` |
| `FreqDist(words)` | `Counter(words)` |
| `ensure_nltk_data()` | Delete entirely |
| `NLTK_AVAILABLE` flag | Delete entirely |
| `_nltk_ready` guard | Delete entirely |

### 2b. `src/services/text_processor.py`

Remove any NLTK import or `ensure_nltk_data()` call. Verify no remaining NLTK references.

### 2c. numpy — no code change required

All numpy imports in `dedup/`, `search/`, and `video/` are already scoped to those
optional modules. Removing numpy from `pyproject.toml` default deps is the only change.
No import guards need to be added.

### 2d. `DocumentDeduplicator` — scope boundary

`services/deduplication/document_dedup.py` exports `DocumentDeduplicator` but it is
never called from any CLI command or the organizer. This epic does **not** wire it up.
Add a comment at the `__init__.py` export:

```python
# DocumentDeduplicator: wired in dedup-text extra; CLI integration tracked separately
```

### 2e. `tqdm` — no change

Used only in `cli/dedupe_hash.py`. At 200 KB it is not worth conditionalising.

---

## Section 3: Documentation Updates

All documentation changes ship in the same PR as the code changes. No deferred doc PRs.

| File | Required change |
|------|----------------|
| `README.md` | Update extras table: add `media`, rename `dedup`→`dedup-text`/`dedup-image`, remove `archive`, add CAD note; remove NLTK first-run download callout |
| `docs/getting-started.md` | Update quick install commands and extras reference |
| `docs/CONFIGURATION.md` | Remove any NLTK corpus path or `ensure_nltk_data` references |
| `docs/setup/` | Remove `nltk.download(...)` setup step if present |
| `docs/reference/` | Update dependencies/extras reference page if present |
| `CONTRIBUTING.md` | Remove NLTK download step from dev setup; update extras names |
| `pyproject.toml` | Update inline comments on moved/removed deps |
| `.claude/rules/ci-generation-patterns.md` | Remove NLTK-specific CI patterns if any |
| `tests/conftest.py` | Remove `mock_nltk_tokenizer`, `mock_nltk_stopwords`, `mock_nltk_lemmatizer`, `mock_nltk_freqdist`, `mock_nltk_ensure_data_no_op`, `isolated_nltk_environment` fixtures |

---

## Section 4: Testing Strategy

### NLTK replacement tests

Existing `tests/utils/test_text_processing.py` tests that currently use `mock_nltk_*`
fixtures are migrated to run against real snowballstemmer. No mocks needed — the
dependency is now always present and deterministic.

### Format libs now guaranteed present

Remove `pytest.importorskip` guards on striprtf, pypdf, py7zr, rarfile, mutagen, and
tinytag in any test files that currently guard them. They are now default deps.

### Extras validation workflow

Update `.github/workflows/extras-validation.yml`:

- Remove `audio` and `video` matrix entries
- Add `media` matrix entry with smoke canary
- Rename `dedup` entry to `dedup-text`
- Add `dedup-image` entry

### CI matrix

No changes to the OS/Python matrix. Windows and macOS both benefit from numpy removal
(fewer native lib build complications on those platforms).

### Coverage

No coverage floor changes expected — the NLTK replacement is a functional equivalence
swap, not a feature removal. If coverage shifts, adjust the floor after measuring.

---

## Section 5: What This Does Not Include

- **Wiring `DocumentDeduplicator` into the organize pass** — separate epic
- **CAD in default** — ezdxf's numpy dep makes this a net regression; revisit if ezdxf
  drops the numpy requirement in a future release
- **Further splitting of PyMuPDF or python-pptx into optional extras** — these are
  core to the organize-pass file reading; deferring until a tiered-format epic is scoped
- **ML near-duplicate detection on every organize pass** — separate epic; requires
  design work on threshold config, performance budget, and user-facing output

---

## Exit Gates

- [ ] `pytest tests/ -m "ci or smoke"` passes on Linux, macOS, Windows
- [ ] `pip install fo-core` installs in ≤185 MB (measured in CI)
- [ ] `pip install "fo-core[media]"` installs and canary imports pass
- [ ] `pip install "fo-core[dedup-text]"` installs and canary imports pass
- [ ] `pip install "fo-core[dedup-image]"` installs and canary imports pass
- [ ] `pymarkdown scan docs/` passes (zero violations)
- [ ] No remaining `nltk` or `ensure_nltk_data` references in `src/`
- [ ] No remaining `import numpy` in non-optional `src/` modules
- [ ] All updated doc files verified against source (D1 rule)
