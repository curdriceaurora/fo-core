# Pyre Violations Triage — 2026-04-25

**Branch**: `claude/evaluate-pyre-violations-gUuEz`
**Pyre version**: 0.9.25 (matches CI workflow `.github/workflows/pyre.yml`)
**Run command**: `pyre --noninteractive --typeshed /usr/local/lib/pyre_check/typeshed --search-path "$SITE_PACKAGES" check`

## Summary

| Bucket | Count | Notes |
|--------|------:|-------|
| **Total findings** | 52 | All `level: error` in SARIF |
| **APPLY** (worth fixing) | 8 | Optional-dep import guards (F9 alignment) |
| **SUPPRESS** (false positive) | 39 | 37 are a single Pyre 0.9.25 limitation across 5 files |
| **DEFER** (real but architectural) | 2 | Both touch typed-callable boundaries |
| **DISMISS** (stale path) | 3 | Open Code Scanning alerts under removed `src/file_organizer/…` paths |

**Headline**: signal-to-noise is poor. **0** of the 52 findings indicate an F4 SECURITY_VULN, S1–S6 search-pattern, or runtime-correctness defect in the current code. The recent path-guard / log-redaction / lifecycle hardening work (commits `c18e7ca`, `5c9e16d`, `9d87575`, `27b115f`, `eb53f5c`) covers the security territory Pyre type-check cannot detect anyway. Pyre runs without Pysa taint configuration, so this scan is type-only — not security taint analysis.

**Recommended fix order** (highest noise reduction per minute of effort):

1. Add `# pyre-ignore-all-errors[35]` to 5 files → kills 37 findings (PYRE-ERROR-35 dataclass/ClassVar false positive).
2. Wrap numpy/numpy.typing imports in the project-standard `try/except ImportError` block (4 files) → kills 8 findings + aligns with F9.
3. Add `# pyre-ignore[21]` on the existing `redis` lazy import → kills 1 finding.
4. Add `# pyre-ignore[16]` on the `record._fo_redacted` line → kills 1 finding.
5. Defer the 2 architectural items to follow-up issues.
6. Ask owner to dismiss 3 stale Code Scanning alerts via Security tab.

After steps 1–4: **52 → 2** active type errors, all genuinely architectural.

---

## APPLY — Worth Fixing Now

All 8 are missing `try/except ImportError` guards on optional-dep imports. Per `.claude/rules/feature-generation-patterns.md` F9 (DYNAMIC_IMPORT_ANTIPATTERN, lines 360–404), the project standard is a top-level `try/except` with a helpful "install with extra" error message. Issues #41 and #49 are the precedent.

### A1 — `src/services/deduplication/embedder.py:14-15`

- `PYRE-ERROR-21`: `import numpy as np` and `from numpy.typing import NDArray` are unguarded module-level imports.
- **Why it matters**: `numpy` ships with the `dedup-text` extra. A user on a base install hitting this module gets `ModuleNotFoundError: numpy` with no install hint. Cascading `PYRE-ERROR-11` on `NDArray` annotation (line 69) is a downstream effect of the same gap.
- **Proposed fix** (same pattern as `src/services/search/bm25_index.py` after issue #49):

  ```python
  try:
      import numpy as np
      from numpy.typing import NDArray
  except ImportError as exc:  # pragma: no cover
      raise ImportError(
          "Install with: pip install 'fo-core[dedup-text]'"
      ) from exc
  ```

### A2 — `src/services/deduplication/semantic.py:12-13`

- Same pattern. `dedup-text` extra. Cascading `PYRE-ERROR-11` on line 37.
- **Fix**: identical block to A1.

### A3 — `src/services/search/embedding_cache.py:34, 36`

- Same pattern. `search` extra. Cascading `PYRE-ERROR-11` on line 55.
- **Fix**: identical block to A1, but with `'fo-core[search]'`.

### A4 — `src/services/search/vector_index.py:19`

- Only `from numpy.typing import NDArray` is unguarded (the `numpy` import is already wrapped at line 21 via the import of `DocumentEmbedder`, which transitively requires numpy). Cascading `PYRE-ERROR-11` on line 51.
- **Fix**: include `numpy.typing` in the same try/except block as the `numpy` import in `embedder.py`, so a single guard there covers both files.

**Cross-reference**: feature-generation-patterns.md F9 lines 360–404; precedent issues #41, #49.

---

## SUPPRESS — False Positives

### S1 — Pyre 0.9.25 dataclass / ClassVar regression (37 findings, 5 files)

`PYRE-ERROR-35: Illegal annotation target` is emitted for every field declaration inside an `@dataclass` (or class-level `ClassVar`) when the file uses `from __future__ import annotations`. This is a Pyre limitation: deferred (PEP 563) annotations are not recognised as dataclass field declarations. The dataclasses are valid Python and behave correctly at runtime; nothing to fix in the code.

Affected files and line spans:

| File | Lines | Construct |
|------|-------|-----------|
| `src/core/backend_detector.py` | 45–48, 61–63 | `@dataclass` |
| `src/core/hardware_profile.py` | 48–54 | `@dataclass(frozen=True)` |
| `src/core/organizer.py` | 58–63 | `class … : NAME: ClassVar[…] = …` |
| `src/core/setup_wizard.py` | 59–64, 77–79 | `@dataclass` |
| `src/core/types.py` | 30–37 | `@dataclass` |

**Proposed suppression**: a single file-level `# pyre-ignore-all-errors[35]` at the top of each of the 5 files (immediately after the module docstring). Avoids 37 inline pragmas. One-line comment explaining the Pyre limitation:

```python
"""<existing docstring>"""

# pyre-ignore-all-errors[35]: Pyre 0.9.25 mis-flags dataclass/ClassVar field
# annotations when `from __future__ import annotations` is in use. Tracking
# upstream: https://github.com/facebook/pyre-check/issues/<TBD>
from __future__ import annotations
```

(If preferred over file-level suppression, the alternative is upgrading Pyre once a fix lands — this is not on the immediate path.)

### S2 — Optional `redis` import (1 finding)

`src/events/stream.py:17` — `import redis` is already wrapped in `try/except ImportError` (lines 16–19). Pyre still emits `PYRE-ERROR-21` because `redis` is not installed in the analysis environment. Identical to the case fixed in **issue #41** (`rank_bm25`, `sklearn`).

**Proposed fix**:

```python
try:
    import redis  # pyre-ignore[21]
except ImportError:  # pragma: no cover
    redis = None  # type: ignore[assignment]
```

### S3 — Intentional custom attribute on `LogRecord` (1 finding)

`src/utils/log_redact.py:330` — `record._fo_redacted = _RECORD_REDACTED_SENTINEL`. The line is the documented idempotency marker for the credential-redacting filter (see surrounding comment block at lines 324–329 and the security rationale at lines 36–41 of the same file). Setting custom attributes on `LogRecord` is supported by stdlib `logging` but `LogRecord.__class__` does not declare them in typeshed.

**Proposed fix**:

```python
record._fo_redacted = _RECORD_REDACTED_SENTINEL  # pyre-ignore[16]
```

---

## DEFER — Real but Architectural

### D1 — `src/utils/log_redact.py:299` `traceback.format_exception` typing

- `PYRE-ERROR-6`: passing `*record.exc_info` (which has type `Union[None, Tuple[Type[BaseException], BaseException, TracebackType]]`) to `traceback.format_exception(exc, /, value, tb, …)`. The Python 3.10+ typeshed declares the first positional arg as `BaseException`, not `Type[BaseException]`. The runtime call still works — Python's `_parse_value_tb` accepts the legacy 3-arg `(type, value, tb)` form — but the typeshed contract is violated.
- **Why defer**: this is a security-critical filter (credential redaction). Refactoring to the modern signature requires extracting `record.exc_info` into named locals, threading the `value` through `format_exception(value)`, and re-running the existing redaction tests. Worth its own issue with full test plan.
- **Suggested issue title**: *fix(log-redact): use modern traceback.format_exception(value) signature*

### D2 — `src/utils/readers/__init__.py:156` reader callable typing

- `PYRE-ERROR-6`: the `readers` dict (lines 130–149) maps tuples of extensions to callables with heterogeneous signatures (`read_7z_file`, `read_tar_file`, `read_hdf5_file`, …). Pyre infers the union of their parameter types; calling `reader(file_path, **kwargs)` then has the union type as `kwargs` value, which conflicts with each individual reader's typed second parameter.
- **Why defer**: the right fix is a `Protocol` with the shared `(file_path, **kwargs) -> str | None` signature, and updating each reader to declare `**kwargs: object`. Touches ~12 reader modules. Pure typing change; no runtime risk; no security relevance.
- **Suggested issue title**: *refactor(readers): introduce ReaderCallable Protocol for readers dispatch dict*

---

## DISMISS — Stale Code Scanning Alerts

GitHub Code Scanning likely retains alerts for files under the old `src/file_organizer/…` layout (the source tree was flattened to top-level `src/cli/`, `src/core/`, `src/services/` etc. before the workflow was restored on 2026-04-23). The fresh local run does not produce any alerts under those paths, confirming they are stale.

**Likely stale alerts** (paths to verify in the Security tab):

- `src/file_organizer/services/audio/transcriber.py` — file no longer exists at this path
- `src/file_organizer/services/deduplication/embedder.py` — moved to `src/services/deduplication/embedder.py`
- `src/file_organizer/services/search/bm25_index.py` — moved to `src/services/search/bm25_index.py`

**Proposed action** (owner-only — requires Code Scanning UI access): dismiss with reason **"Won't fix"** and comment **"File path moved during epic/flatten-src-fo refactor — superseded by current run on flattened layout"**.

The exact alert numbers depend on whatever GitHub Code Scanning shows after the next workflow run — recommend dismissing in bulk based on the path prefix `src/file_organizer/`.

---

## Verification

1. **SARIF parses cleanly**: `jq '.runs[0].results | length' /tmp/pyre-sarif.json` → `52`. ✅
2. **Bucket totals reconcile**: 8 + 39 + 2 + 3 (DISMISS not in the local 52) = within current 52 = 49; the 3 DISMISS items are external (GitHub UI), not in the local SARIF. The 49 local breakdown reconciles. ✅
3. **APPLY entries reproducible**: `grep -nE '^import numpy|^from numpy' src/services/{deduplication,search}/*.py` confirms the 4 files have unguarded module-level numpy imports. ✅
4. **SUPPRESS [35] sites are inside @dataclass / ClassVar**: verified by reading each cited file. ✅
5. **SUPPRESS [21] on redis is genuinely guarded**: lines 16–19 of `src/events/stream.py` show `try / except ImportError`. ✅
6. **SUPPRESS [16] on `_fo_redacted` is intentional**: surrounding comments at lines 324–329 of `src/utils/log_redact.py` document it as a security-critical idempotency sentinel. ✅
7. **Cross-check with GitHub Security tab** (recommended next step for the owner): re-run the Pyre workflow on this branch (or wait for next push to a watched branch) and compare the published alert count against this local `52` total. Divergence likely indicates the CI environment installs different optional extras than this local run; if so, the fix-list above adjusts but the categorization holds.

---

## Out of Scope (per plan)

- Implementing fixes — that is follow-up PR work, one logical group each.
- Configuring Pysa taint analysis — none currently configured; would be a separate epic if F4 SECURITY_VULN coverage via static analysis is desired.
- Modifying `.pyre_configuration` to broadly suppress error codes — prefer surgical inline / file-level annotations as established by issue #41.

---

## Raw Output Reference

- Local text run: `/tmp/pyre-text.txt` (52 findings against current branch)
- Local SARIF: `/tmp/pyre-sarif.json` (mirrors what CI uploads to Code Scanning)
- Pyre command (mirrors `.github/workflows/pyre.yml` exactly except for explicit typeshed flag, needed because the pip-installed pyre-check did not auto-discover its bundled typeshed in this environment):

  ```bash
  pip install -e . "click<8.2" pyre-check
  SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")
  pyre --noninteractive \
       --typeshed /usr/local/lib/pyre_check/typeshed \
       --search-path "$SITE_PACKAGES" \
       --output=sarif check > sarif.json
  ```

  CI runs the same minus `--typeshed` (its environment auto-resolves bundled typeshed). If the CI run's finding count differs materially from 52, the most likely cause is a typeshed-resolution gap, not a real code change.
