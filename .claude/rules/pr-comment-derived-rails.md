# PR-Comment-Derived Rails

**Purpose**: Patterns extracted from PR review comments (PRs 271–321) that
recurred often enough to warrant an AST/pre-commit rail instead of relying on
reviewer vigilance. Each rail follows the project convention: land advisory,
ratchet via a baseline CI test, promote to enforcing when the baseline hits
zero.

**Status**: Three rails active (advisory). See `tasks/pr-findings-classified.md`
for the source comment audit.

---

## Rail 1 — `safedir-valueerror`

**What it catches**: a `try:` block calling a SafeDir method
(`open_for_reader`, `open_root`, `open_subdir`, `open_child`, `pin_inode`,
`rename_into`) or helper (`safedir_image_open`, `read_file_via_safedir*`)
whose `except` clause omits `ValueError`. SafeDir's name validation raises
`ValueError` for legal POSIX filenames containing characters it rejects
(e.g. backslash); uncaught, that crashes the entire command.

**Detector**: `scripts/check_safedir_valueerror.py`
**Baseline test**: `tests/ci/test_safedir_valueerror_rail.py`
**Mode**: advisory (`--advisory` in `.pre-commit-config.yaml`)
**Baseline**: 13 sites at filing time (issue #323)

**Opt-out**: `# safedir-valueerror: ok — <reason>` on the SafeDir call line.

**Bad**:

```python
try:
    fobj = safe_dir.open_for_reader(name)
    ...
except (SymlinkRejected, OSError):
    ...  # ValueError from name validation escapes here
```

**Good**:

```python
try:
    fobj = safe_dir.open_for_reader(name)
    ...
except (SymlinkRejected, OSError, ValueError):
    ...
```

---

## Rail 2 — `defusedxml-fallback`

**What it catches**: `try: import defusedxml.X` paired with an
`except ImportError:` whose handler doesn't `raise` and either re-imports
from `xml.*` or assigns from a module-level stdlib XML import. Silent
fallback re-enables billion-laughs / XXE / external-DTD attacks on every
caller, because `defusedxml` is the only thing in this codebase that
disables expat entity expansion.

**Detector**: `scripts/check_defusedxml_fallback.py`
**Baseline test**: `tests/ci/test_defusedxml_fallback_rail.py`
**Mode**: advisory
**Baseline**: 1 site (`src/services/deduplication/extractor.py:31`,
tracked in issue #323).

**Opt-out**: `# defusedxml-fallback: ok — <reason>` on the defusedxml import line.

**Bad**:

```python
import xml.etree.ElementTree as _stdlib_ET
try:
    import defusedxml.ElementTree as _ET
except ImportError:
    _ET = _stdlib_ET  # silent XXE re-enable
```

**Good** (fail closed):

```python
try:
    import defusedxml.ElementTree as _ET
except ImportError as exc:
    raise RuntimeError(
        "defusedxml is required for safe XML parsing"
    ) from exc
```

---

## Rail 3 — `textiowrapper-detach`

**What it catches**: a function whose signature includes `fileobj` (or
`stream`) constructs `io.TextIOWrapper(fileobj, ...)` but doesn't call
`.detach()` on the wrapper anywhere in the function body. The wrapper
takes close-ownership of the underlying binary stream by default; on
garbage-collection it closes the caller's stream, surprising callers and
breaking the public fileobj-accepting reader contract introduced in
PR3a–PR3i.

**Detector**: `scripts/check_textiowrapper_detach.py`
**Baseline test**: `tests/ci/test_textiowrapper_detach_rail.py`
**Mode**: advisory
**Baseline**: 0 sites at filing time (PR #276 fixed both known sites).
**Scope**: `src/utils/readers/` + `src/utils/epub_enhanced.py`

**Opt-out**: `# textiowrapper-detach: ok — <reason>` on the wrapper-assignment line.

**Bad**:

```python
def read_step_file(fileobj: BinaryIO) -> str:
    text_stream = io.TextIOWrapper(fileobj, encoding="utf-8")
    return text_stream.read()  # closes fileobj on GC
```

**Good**:

```python
def read_step_file(fileobj: BinaryIO) -> str:
    text_stream = io.TextIOWrapper(fileobj, encoding="utf-8")
    try:
        return text_stream.read()
    finally:
        text_stream.detach()  # severs close-ownership
```

---

## Promotion to Enforcing

Each rail moves through three states:

1. **Advisory** — `--advisory` flag passed in pre-commit; baseline test pins
   the current count. New regressions fail the baseline test even though the
   hook itself exits 0.
2. **Per-file enforcing** — files added to the script's `_ENFORCING_FILES` set
   start failing pre-commit on violation. Same mechanism as
   `_READ_OPEN_ENFORCED_DIRS` in the SafeDir rail (PR #287).
3. **Globally enforcing** — flip `_ENFORCING = True`. Drop the `--advisory`
   flag from `.pre-commit-config.yaml`.

The promotion path for rails 1 and 2 is bounded by issue #323. Rail 3 is
already at baseline=0; it can go enforcing as soon as the reviewer is
satisfied with the synthetic-input test coverage.

---

## Source

Audit derived from PR comments 271–321. To regenerate the raw harvest:

```bash
bash .claude/scripts/harvest-pr-comments.sh --min 271 > /tmp/audit.jsonl
```

Classification (committed): `tasks/pr-findings-classified.md`.
Issues filed: #322–#328.

**Last Updated**: 2026-05-20
