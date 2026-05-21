# PR Comments Triage — PRs 271–321

**Scope**: 132 unresolved review threads across 25 merged PRs (post-#271).
**Source**: harvested via `bash .claude/scripts/harvest-pr-comments.sh --min 271`
(raw `pr-comments-271-plus.jsonl` is regeneratable; not committed because the
1.2 MB harvest exceeds the 500 KB `check-added-large-files` cap).

**Process**: 5 parallel Explore agents read each thread body, re-checked the
referenced `path:line` in the current tree, and classified.

## Triage Summary

| Verdict | Count | Notes |
|---|---|---|
| `ADDRESSED` (resolved in subsequent commit) | 88 | Includes the PR3g alias-resolution revert (20 threads superseded) |
| `PR_SPECIFIC_STALE` (transient diff state) | 11 | Doc audit threads, removed lines |
| `NITPICK_LOW` (style, docstring) | 9 | Not worth filing |
| **`STILL_VALID`** | **24** | Filed below |
| Total | 132 | |

---

## STILL_VALID Findings → 7 MECE Issue Clusters

### Cluster 1 — Watcher SafeDir hardening incomplete (PR #314 follow-up)

7 findings; subsystem: `src/watcher/`, `src/pipeline/stages/`.

| # | Path:Line | Concern |
|---|---|---|
| 1.1 | `src/watcher/handler.py:99` | Constructor accepts `safe_dir` and `watch_root` as independent `Optional`s; no validation that both are provided together. |
| 1.2 | `src/watcher/handler.py:208` | SafeDir gating disabled in production — `FileMonitor` constructs `FileEventHandler(self.config, self.queue)` without `safe_dir`/`watch_root` (see `monitor.py:57`). |
| 1.3 | `src/watcher/handler.py:256` | `path.resolve()` outside try/except. Symlink loops raise `RuntimeError`; the `SymlinkRejected` handler starts at line 277. |
| 1.4 | `src/watcher/handler.py:260` | `path.resolve()` follows symlinks before `.relative_to(watch_root)`, so a direct-child symlink bypasses `O_NOFOLLOW`. |
| 1.5 | `src/watcher/handler.py:275` | Nested paths (`len(rel.parts) != 1`) return `True` without further SafeDir check; downstream preprocessor uses `path.stat()` without `O_NOFOLLOW`. |
| 1.6 | `src/pipeline/stages/postprocessor.py:57` | SafeDir `SymlinkRejected` falls back to path-based `mkdir` — security guarantee weakened. Should fail closed. |
| 1.7 | `src/pipeline/stages/writer.py:87` | `shutil.copystat(context.file_path, destination)` called *after* `os.close(dst_fd)` on line 83 — opens TOCTOU window for symlink swap on metadata. |

### Cluster 2 — Dedupe TOCTOU + SafeDir `ValueError` handling (PR #307 follow-up)

6 findings; subsystem: `src/services/deduplication/`, `src/cli/dedupe_v2.py`.

| # | Path:Line | Concern |
|---|---|---|
| 2.1 | `src/services/deduplication/hasher.py:136` | `FileHasher.pin_inode()` does blocking `open_for_reader()` — a FIFO swapped in before resolve hangs the entire dedupe pass. Use `O_NONBLOCK` or pre-lstat for special files. |
| 2.2 | `src/cli/dedupe_v2.py:212` | `SafeDir.ValueError` (backslash in filename component) not caught; only `SymlinkRejected`/`OSError` are handled. Crashes `fo dedupe resolve` instead of per-file skip. |
| 2.3 | `src/cli/dedupe_v2.py:239` | `pin_inode()` captured at *resolve* time, not *scan* time. Same-name regular-file swap after scan still passes check; replacement gets deleted. Need scan-time `InodePin` carried in dedupe metadata. |
| 2.4 | `src/services/deduplication/backup.py:57` | Same `ValueError` gap as 2.2 for backup cleanup. |
| 2.5 | `src/services/deduplication/backup.py:69` | Backup unlink only checks `S_ISREG` from `lstat()` and unlinks by name. Different regular file renamed in slot between calls gets deleted. |
| 2.6 | `src/services/deduplication/extractor.py:33` | `defusedxml` fallback to stdlib `xml.etree.ElementTree` on ImportError silently re-enables entity-bomb vulnerability. Fail closed instead. |

### Cluster 3 — Undo subsystem TOCTOU + EXDEV redo invariant

2 findings; subsystem: `src/undo/rollback.py`.

| # | Path:Line | Concern |
|---|---|---|
| 3.1 | `src/undo/rollback.py:182` | Verify-then-move pattern. Inode swap after `lstat()` but before `_move()` still possible. Bind verification to the move via pinned descriptor. |
| 3.2 | `src/undo/rollback.py:237` | After EXDEV-redo (cross-device), inode baseline is not refreshed. Breaks `undo→redo→undo` cycle for cross-device moves. |

### Cluster 4 — Anchored-traversal migration incomplete

2 findings; subsystem: `src/services/`.

| # | Path:Line | Concern |
|---|---|---|
| 4.1 | `src/services/deduplication/extractor.py:91` | Extractor receives `file_path` directly; no component-wise `open_subdir` traversal for nested paths. Symlink-swapped intermediate dir not detected. |
| 4.2 | `src/services/search/hybrid_retriever.py:72` | Still uses parent-rooted `SafeDir.open_root(path.parent)`. `text_processor.py:171` uses the anchored variant — apply the same pattern here. |

### Cluster 5 — Reader robustness (memory caps, version pins, coverage)

4 findings; subsystem: `src/utils/readers/`, `src/utils/epub_enhanced.py`.

| # | Path:Line | Concern |
|---|---|---|
| 5.1 | `src/utils/readers/cad.py:369` | STEP reader `f.readline()` in loop has no byte cap; only line-count cap. Adversarial single-line file → unbounded memory. Mirror same fix in fileobj branch. |
| 5.2 | `src/utils/readers/ebook.py:38` | Project pins `ebooklib>=0.18,<1`. EbookLib 0.18 fails on file-object input. Bump floor to a version that accepts fileobj, or fall back to path. |
| 5.3 | `src/utils/readers/__init__.py:191` | Dispatcher only tested for `.zip` and `.tar.gz`. A dropped mapping for `.7z`, `.rar`, plain `.tar`, or TAR aliases silently returns `None` and falls back to legacy path reader. |
| 5.4 | `src/utils/epub_enhanced.py:254` | No regression test exercises `EnhancedEPUBReader` with a symlinked EPUB. Only low-level SafeDir and path-based EPUB tests exist. |

### Cluster 6 — Test reliability (fixture isolation + inode reuse)

2 findings; subsystem: `tests/`.

| # | Path:Line | Concern |
|---|---|---|
| 6.1 | `tests/ci/test_symlink_safety_lints.py:25` | Tests don't opt out of the `cli.organize._check_setup_completed` autouse patch. Run before any `cli.organize` import (or in isolation) errors in the conftest fixture. |
| 6.2 | `tests/security/test_symlink_safety.py:529` | Delete-recreate assumes fresh inode; inode reuse on some CI/tmpfs filesystems flakes intermittently. |

### Cluster 7 — Documentation scope cleanup

1 finding; subsystem: `docs/`.

| # | Path:Line | Concern |
|---|---|---|
| 7.1 | `docs/superpowers/specs/2026-04-22-hardening-roadmap-design.md:475` | SafeDir marked "tracked separately; not part of A–G roadmap" but line 475 reads as if wired through epic-a-cli. Contradiction. |

---

## Recurring Patterns → Hook Improvement Candidates

| Pattern | Frequency | Hook idea |
|---|---|---|
| `try:` block with SafeDir call where `except` omits `ValueError` (raised by name validation) | 2× (2.2, 2.4); likely more in unaudited files | AST: detect `safe_dir.*(…)` / `safedir_image_open(…)` etc. inside a `try` whose `except` clause set doesn't include `ValueError`. |
| `path.resolve()` before `.relative_to(root)` on user input | 1× (1.4) | AST: detect `Path.resolve().relative_to(X)` where the path came from a user-input parameter; prefer `os.path.commonpath` or anchored SafeDir traversal. |
| `path.resolve()` unguarded against `RuntimeError` | 1× (1.3) | Already covered by `check_resolve_runtime_error.py` (F11) — but `src/watcher/handler.py:256` slips through because resolve is far from the except. Widen the rail. |
| `defusedxml` → stdlib fallback on `ImportError` | 1× (2.6) | AST: detect `try: import defusedxml…except ImportError:` paired with `from xml.etree…import` for the same alias. Fail closed instead. |
| TextIOWrapper around caller-owned `fileobj` without `.detach()` | ADDRESSED in PR #276 but no rail — could regress | AST: in any function with a `fileobj` parameter, every `io.TextIOWrapper(fileobj_param)` must call `.detach()` before return. |
| `pin_inode()` / `lstat()` immediately before `unlink`/`rename` | 2× (2.3, 2.5, 3.1) — three different files | Hard to express AST-only; better captured as a CodeRabbit rule. |
| Inline lazy `import` inside function body (F9 antipattern) | already flagged by review, no rail | AST: enforce — already partially exists in test_optional_dep_guards but only for tests. Extend to `src/`. |

The four most enforceable patterns for new hooks:

1. **`check_safedir_valueerror.py`** — SafeDir call inside `try` whose `except` doesn't include `ValueError`.
2. **`check_resolve_relative_to.py`** — `Path.resolve().relative_to(...)` on user input.
3. **`check_defusedxml_fallback.py`** — stdlib XML fallback on ImportError of defusedxml.
4. **`check_textiowrapper_detach.py`** — TextIOWrapper around `fileobj=` parameter must detach before return.

These will be implemented as the second deliverable.
