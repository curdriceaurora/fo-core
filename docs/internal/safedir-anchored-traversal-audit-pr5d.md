# PR5d — Anchored-Traversal Sweep Audit (#286)

**Branch:** `epic/safedir-undo-pr5-5d`
**Issue:** #286 (folded into PR5 epic, #269)
**Date:** 2026-05-20

---

## Scope

Files originally listed in issue #286:

| File | Location |
|------|----------|
| `extractor.py` | `src/services/deduplication/extractor.py` |
| `epub_enhanced.py` | `src/utils/epub_enhanced.py` |
| `hybrid_retriever.py` | not found in tree (likely renamed/removed) |
| `organizer/` | `src/core/organizer.py`, `src/services/{audio,video}/organizer.py` |
| `undo/` | `src/undo/*.py` |

---

## Findings

### In-scope files — all clear

| File | `rglob`/`os.walk` sites | Verdict |
|------|------------------------|---------|
| `src/services/deduplication/extractor.py` | none | ✅ Clean |
| `src/utils/epub_enhanced.py` | none | ✅ Clean |
| `hybrid_retriever.py` | not present | ✅ N/A |
| `src/core/organizer.py` | none | ✅ Clean |
| `src/services/audio/organizer.py` | none | ✅ Clean |
| `src/services/video/organizer.py` | none | ✅ Clean |
| `src/undo/validator.py:545` | `self.trash_dir.rglob(filename)` | ✅ See note below |
| All other `src/undo/*.py` | none | ✅ Clean |

### `src/undo/validator.py:545` — system-managed root

```python
# safedir: ok — system-managed trash dir (not user-supplied root);
# rglob target is self.trash_dir, always under the app state dir.
for item in self.trash_dir.rglob(filename):  # noqa: safedir
```

`self.trash_dir` is constructed from the application's state directory
(`~/.local/share/fo/trash/` or equivalent via `platformdirs`). It is never
set from user-supplied CLI input — callers set it via `OperationValidator(trash_dir=…)`
only in tests, always passing `tmp_path`-based paths.

The rglob search string (`filename`) is `operation.source_path.name` — a bare
filename with no path separators, so it cannot escape the trash root via `..`
traversal.

Verdict: **opted-out with documented rationale**; no code change required beyond
the `# safedir: ok` comment added in this PR.

---

### Out-of-scope sites (noted for completeness)

Two sites outside the #286 scope also use raw walkers:

| File | Site | Note |
|------|------|------|
| `src/core/file_ops.py:47` | `os.walk(path)` in `collect_files` | User-supplied root; hidden files filtered but symlinks not. Out of PR5d scope — tracked as separate hardening work. |
| `src/config/path_migration.py:63` | `self.legacy_path.rglob("*")` | System-managed legacy config path. Low risk; not in scope. |
| `src/methodologies/para/ai/file_mover.py:295,360` | `self._rglob_provider(directory, "*")` | Injected seam; real callers pass user-supplied dirs. Out of scope. |

`src/core/file_ops.py:47` is the most significant — it walks user-supplied input
without symlink filtering. This should be replaced with `safe_walk` in a follow-up
hardening PR (after PR6, which addresses the organize-path symlink vector).

---

## Conclusion

All files in the #286 scope are confirmed safe or opted out with documented rationale.
No functional code change was required beyond:

1. `# safedir: ok` comment on `validator.py:545`

Issue #286 can be closed.
