# Hardening Roadmap — Design Spec

**Date:** 2026-04-22
**Status:** Draft v6 — polish pass; awaiting user approval before implementation planning
**Scope:** Tighten and harden the fo-core codebase across all quality axes; produce 15 focused PRs across 7 tracking epics.

**Changelog:**
- v6 (2026-04-22): Synced B PR-split count (16→17 B1a sites) with Appendix A.2. Rewrote the hidden-file example — removed the incorrect `fo analyze` reference (analyze is a single-file command with no walker flag) and clarified that `include_hidden=True` is reserved for future walker commands, with no current opt-in. Renamed Appendix A.1 utilities rows from "find subcommand" to "`fo search` walker" to match the registered command name. Mechanical grep sweep for stale names/counts — clean.
- v5 (2026-04-22): Added `embedder.py:271` (`save_model()` pickled vectorizer) to B1a inventory — same binary-truncate pattern as `:314` and covered by the v4 `atomic_write_with` helper. Added `compare_path` (`--compare` baseline JSON) to the `fo benchmark run` row in Appendix A.4. Added **G5** — pre-commit rail blocking future pytest `-n auto` invocations without `--dist=loadgroup` — to own the drift-prevention rail C2 introduced but left unassigned. Fixed helper-name drift: G3 now consistently references `validate_within_roots()` (plural). Bumped A.2 counts to 17 B1a / 45 total.
- v4 (2026-04-22): Fixed stale command names — `daemon process-once` is `daemon process`; `doctor` is a top-level command not a sub-app; `benchmark compare` is `benchmark run --compare`; Appendix A.4 rebuilt row-by-row against each sub-app module. Expanded D1 blocker scan from 4 patterns to 6, adding `from cli import (autotag|dedupe)` module-alias form and `patch("cli.(autotag|dedupe).…")` string-based mock targets (38+ hits would have broken the suite at runtime). Completed C2 xdist inventory — 8 invocations across 4 files (`ci.yml:185,223`, `ci-full.yml:44,99`, `pr-integration.yml:70`, `run-local-ci.sh:241,269`, `run-xdist-audit.sh:23`); added a pre-commit rail to prevent future drift. Tightened E1 psutil range from `<8` to `<7` to match the stated conservative rationale. Fixed E3 inventory to 18 pins (9 keep-as-is + 9 cap candidates); added `scenedetect[opencv]` and `mkdocstrings[python]`. Extended B1 helpers to cover the embedder's binary pickle cache (`atomic_write_bytes` + callback-form `atomic_write_with`).
- v3 (2026-04-22): Widened D1 blocker scan for bare `from cli.(autotag|dedupe) import`. Rebuilt A.4 with real command names. Added embedder.py to B1 inventory; reclassified suggestion_feedback:389 as export. Named actual xdist invocations in C2. Re-pinned E1 with rationale. Fixed E3 count. Re-expressed F7 EXDEV fallback as durable+idempotent. Split A→3 PRs, B→3 PRs; total 15 PRs.
- v2 (2026-04-22): Added §2.5 path model. Expanded A1 from 4 to 18 call sites. Retargeted A2 at live CLI, made it depend on D1. Split B1 into B1a/B1b. Widened D1 to cover `cli/__init__.py` exports and test migration. Respecified E2 allowlist. Expanded F7 to all four rollback movers. Reordered: `epic-d-cleanup` before `epic-a-*`. Added Appendix A.

---

## 1. Goal

Close concrete gaps identified across six hardening axes (security, correctness, tests, simplification, dependencies, operational health) plus a cross-cutting axis (enforcement rails). Each gap is grounded in a file:line anchor; the roadmap is MECE at the axis level and sized so each PR can be meaningfully reviewed.

**Non-goals:** new features, UX work, perf optimization not tied to a correctness fix, documentation refresh beyond doc-drift caught by existing pre-commit rules, GUI / desktop app changes.

---

## 2. Axis taxonomy (MECE)

Every finding belongs to exactly one epic. Ambiguous cases resolved by the rules below.

| Epic | Membership rule |
|------|-----------------|
| **A — Security Surface** | Static surface: input validation, secret handling, least-privilege, boundary defense. |
| **B — Correctness & Resilience** | Runtime error paths, resource safety, atomicity of writes, docstring↔code truth. |
| **C — Test Quality & Coverage** | Assertion strength (T1/T9), import guards (T5/T8), predicate negative cases (T10), xdist safety, integration coverage of large modules. |
| **D — Code Simplification** | Oversized modules, F8 boundary violations, dead v1/v2 duplication. |
| **E — Dependency Hygiene** | Pins, upper-bound caps, supply-chain CI gates, extras↔import-guard matrix. |
| **F — Operational Health** | User-visible lifecycle state machines that must survive crash/restart — daemon, watcher, undo, history, config migration. |
| **G — Cross-cutting & Meta** | Prevention rails (new pre-commit hooks, full-suite guardrail promotion post-backlog), plus the tracking epic itself. |

Boundary-resolution rules applied:
- Atomic-write bugs → B (resilience), not A, unless they protect a secret-bearing file.
- Undo rollback atomicity → F (user-facing state machine), not B, because the blast radius is user data integrity.
- Full-suite T1/T9 guardrail promotion → G, not C, because it is *prevention* not *remediation*.

---

## 2.5. Path model (prerequisite for A2, A1, G3)

fo-core has no single "organize root" in `AppConfig`. CLI commands take ad-hoc input/output directories per invocation. This section fixes the vocabulary the roadmap depends on.

**Model:** per-invocation allowed-root set.
- Every path-taking CLI command owns a set of **allowed roots** derived from its arguments (e.g. `fo organize INPUT_DIR OUTPUT_DIR` → `{INPUT_DIR.resolve(), OUTPUT_DIR.resolve()}`).
- System paths configured in `AppConfig` (trash dir, history DB, cache dir, watch dir when daemon is running) are added to the set for commands that legitimately touch them.
- **No global `AppConfig.organize_path` field** is introduced. Each command's allowed-root set is local to the invocation.

**Helper introduced in A2:** `src/core/path_guard.py`

```python
def validate_within_roots(path: Path, allowed_roots: Iterable[Path]) -> Path:
    """Resolve `path` and assert it lives inside one of the resolved roots;
    return the resolved path. Raises PathTraversalError otherwise."""
```

**Scope of enforcement:**
- CLI entry points that accept a path argument: `organize`, `preview`, `dedupe` (scan/resolve/report), `autotag` (suggest/apply/batch), `analyze`, `analytics`, `search`, `suggest`, `rules` (preview/export/import), `daemon` (start/watch/process), `benchmark run`, `doctor`, `profile` (import/export). Full surface with line anchors in Appendix A.4.
- In-scope: user-supplied path arguments and any derived path (rglob output, move targets) before a filesystem mutation or read from a file.
- Out-of-scope: paths already inside `AppConfig`-configured system locations (trash, history, cache) — they pre-exist the user's current invocation and are trusted by construction.

**Symlink handling inside allowed roots:** even within an allowed root, symlinks may point outside it. A1 therefore filters symlinks at `rglob` collection time (before any read). Following symlinks is never the default for the walkers enumerated in Appendix A.1.

**This section must land as the first commit of A2** (the helper and unit tests), before the commits that wire it into the CLI commands.

---

## 3. Workstream catalog

All file:line anchors below came from parallel audits of the codebase on 2026-04-22. Effort estimates: `s` ≤ 1 day, `m` 2–5 days, `l` > 1 week.

### Epic A — Security Surface
- **A1** Filter symlinks and hidden files at every walker that traverses a *user-supplied* root. Not a blanket "every `rglob`"; system-path walkers (migration, trash) are explicitly out of scope and listed separately in Appendix A.1. Provide `path_guard.safe_walk(root, *, follow_symlinks=False, include_hidden=False)` helper used uniformly. *m*
  - Full list: see Appendix A.1, section *"User-input walkers (in scope)"*. 18 call sites across `src/cli/`, `src/services/`, `src/methodologies/`, `src/core/`.
  - `include_hidden=True` is reserved for future walker commands that legitimately need dotfile traversal; no current command opts in, so every call site uses the `False` default. If a future command needs hidden-file inclusion, it adds an explicit `--include-hidden` flag and passes the value through.
- **A2** Route every path-taking CLI entry point through `validate_within_roots()` (defined in §2.5) before any filesystem mutation. Depends on `epic-d-cleanup` having merged so that legacy `dedupe.py` / `autotag.py` modules are gone — A2 only hardens *live* commands. *m*
  - Full CLI surface in Appendix A.4. Note: Python modules are suffixed `_v2` but the registered command names are plain (`fo autotag`, `fo dedupe`, etc.) — not `fo autotag-v2`.
  - Commands requiring wiring (verified against `src/cli/main.py` and each sub-app module): `organize`, `preview`, `analyze`, `analytics`, `search`, `autotag <sub>`, `dedupe scan|resolve|report`, `rules preview|export|import`, `daemon start|watch|process`, `suggest <sub>`, `benchmark run --compare`, `doctor`. Effectively every typer command whose signature contains a `Path` argument.
  - First commit ships `src/core/path_guard.py` (helper + tests); subsequent commits wire each command group.
- **A3** Audit and redact API keys / credentials across logging and error paths. *s*
  - `src/config/provider_env.py:174,248`
  - `ModelConfig.api_key` sinks across `src/integrations/` and `src/services/`
  - Add a redacting log filter (`src/utils/log_redact.py`) that masks any key matching `(api_key|token|secret|password)=<value>` in log records.

**PR split for Epic A** (v3 revision — single PR was infeasible after v2 scope expansion):
- `hardening/epic-a-foundation` — §2.5 path model + `src/core/path_guard.py` (`validate_within_roots`, `safe_walk`, `PathTraversalError`) with unit tests. A1's 18 walker rewrites ride along (mechanical two-line substitutions per site). *small, foundational*
- `hardening/epic-a-cli` — wire every CLI entry point in Appendix A.4 through `validate_within_roots()`. Depends on `epic-a-foundation`. *medium*
- `hardening/epic-a-creds` — A3 credential redaction (log filter + `ModelConfig.api_key` sinks). Independent of the path-guard chain. *small*

### Epic B — Correctness & Resilience
- **B1a** Atomic replacement for *state files* that are truncated-then-written. Temp + `os.replace()` pattern everywhere a crash mid-write would corrupt runtime state. *m*
  - State files in scope (full list in Appendix A.2): `src/config/manager.py:144,205`, `src/config/path_migration.py:91`, `src/services/suggestion_feedback.py:368`, `src/services/copilot/rules/rule_manager.py:99`, `src/methodologies/para/migration_manager.py:395,514`, `src/methodologies/para/config.py:270`, `src/methodologies/johnny_decimal/config.py:194`, `src/methodologies/johnny_decimal/migrator.py:326`, `src/methodologies/johnny_decimal/system.py:410`, `src/services/intelligence/folder_learner.py:303`, `src/services/auto_tagging/tag_learning.py:452`, `src/events/discovery.py:137`, `src/methodologies/para/ai/feedback.py:307`, **`src/services/deduplication/embedder.py:271`** (`save_model()` — pickled vectorizer, same binary-truncate pattern), **`src/services/deduplication/embedder.py:314`** (`_save_cache()` — embedding cache; both flagged by rule S6).
  - Daemon PID file (`src/daemon/pid.py:50`) is explicitly handled under F2 (lockfile redesign), not here — tagged to prevent double-patching.
  - Out-of-scope for B1a (one-shot user artifacts, not state): `src/history/export.py` (*.csv/json user exports), `src/services/analytics/analytics_service.py` (reports), `src/services/deduplication/reporter.py` (reports), `src/services/video/scene_detector.py` (output), `src/integrations/{workflow,obsidian}.py` (install artifacts).
  - Provide `src/utils/atomic_write.py` with three helpers covering the write modes we see in-repo:
    - `atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None` — for the 15 text state files.
    - `atomic_write_bytes(path: Path, content: bytes) -> None` — for the embedder pickle cache (`embedder.py:314` writes binary via `pickle.dump(..., f)` with `"wb"` mode).
    - `atomic_write_with(path: Path, writer: Callable[[IO[bytes]], None], *, mode: str = "wb") -> None` — callback form for writers that stream into the file handle (e.g. `pickle.dump(obj, f)`); used by the embedder site so we don't buffer the entire pickle in memory.
  - All three share one temp-file-plus-`os.replace()` implementation; only the surface API differs.
- **B1b** Append-durability for *log-style* files where order matters and truncate-replace is wrong. Pattern: open append, `write`, `flush`, `os.fsync(fileno)`, then close. *s*
  - `src/events/audit.py:246`, `src/integrations/vscode.py:76-77` (VS Code JSONL command stream).
  - Provide `src/utils/atomic_write.py::append_durable(path, line)` helper alongside the atomic writers.
- **B2** Error-boundary hygiene — replace bare excepts and silent fallbacks with typed/categorized handling; log exception type and category. *m*
  - `src/services/vision_processor.py:208`
  - `src/services/text_processor.py:260-272,349-351`
  - `src/models/model_manager.py:178-179`
- **B3** Wrap undo DB mutations in the `transaction()` context manager. *s*
  - `src/undo/undo_manager.py:102-118`

**PR split for Epic B** (v3 revision):
- `hardening/epic-b-atomic` — B1a + B1b helpers in `src/utils/atomic_write.py` + the full inventory of state-file rewrites (17 B1a sites + 2 B1b sites; see Appendix A.2). *medium*
- `hardening/epic-b-errors` — B2 error-boundary cleanup (vision / text processors, model manager). *medium*
- `hardening/epic-b-undo-db` — B3 undo DB transaction wrap. *small*

### Epic C — Test Quality & Coverage
- **C1** T1 residual-backlog cleanup — phased commits by directory (`services/`, `cli/`, `methodologies/`, `integration/`); strengthen sole-isinstance assertions to value-checking assertions per `.claude/rules/test-generation-patterns.md` Fix-by-type table. *l*
- **C2** xdist-safety sweep — three coordinated changes: *m*
  1. Replace hardcoded `/tmp/*` paths with `tmp_path` fixture.
     - Known sites: `tests/services/audio/test_classifier.py:60`, `test_audio_integration.py:58`, `test_content_analyzer.py:49`, `test_organizer.py:59`, `test_audio_transcriber_service.py:205,210`, plus ~25 additional `/tmp` hits flagged by the pre-commit rail G2.
  2. Add `@pytest.mark.xdist_group(name=...)` to every test class that mutates a module-level singleton (provider registry, preference DB, pattern learner). Discovery criterion: tests that touch `src/services/intelligence/*_store.py`, `src/services/pattern_analyzer.py`, or set module-level attributes.
  3. Add `--dist=loadgroup` to every pytest invocation that passes `-n` (without it, step 2's markers are inert). Complete inventory from repo grep on 2026-04-22:
     - `.github/workflows/ci.yml:185` — PR suite (`-n=auto`).
     - `.github/workflows/ci.yml:223` — test-full job, push-only matrix (`-n auto`).
     - `.github/workflows/ci-full.yml:44` — Linux full suite (`-n auto`).
     - `.github/workflows/ci-full.yml:99` — ci-or-smoke job (`-n=auto`).
     - `.github/workflows/pr-integration.yml:70` — PR integration (`-n=auto`).
     - `scripts/run-local-ci.sh:241` and `:269` — local CI harness (`-n=auto` both sites).
     - `scripts/ci/run-xdist-audit.sh:23` — xdist audit harness (`-n auto`).
     - Out of scope: `scripts/run-local-ci.sh:327` (integration run, no `-n`) and `.github/workflows/ci.yml:312,412` (benchmark / integration runs without xdist).
     - v2 incorrectly claimed the `coverage-gate` job needed `--dist=loadgroup`; it does not use `-n` and is removed from this list.
     - Prevent future drift: add a pre-commit rail — tracked as **G5** (see §3 G) — that fails if any tracked YAML or shell file introduces `-n\s*[=]?\s*auto` without `--dist=loadgroup` on the same invocation.
- **C3** T10 predicate negative-case backfill for `_is_*` / `_has_*` in detector code. *m*
  - `src/methodologies/para/detection/heuristics.py`
  - `src/services/misplacement_detector.py`
  - `src/services/intelligence/*`
- **C4** Fix one-line T9 vacuous assertion. *s*
  - `tests/test_cli_config_integration.py:62`
- **C5** Add integration tests for large low-branch-coverage modules, starting with heuristics. *m*
  - `src/methodologies/para/detection/heuristics.py` (905 LOC)

### Epic D — Code Simplification
- **D1** Delete v1 dead code — `src/cli/autotag.py`, `src/cli/dedupe.py`. This is a prerequisite for A2 (which hardens only live commands). Scope: *m* (not `s` as initially claimed — the compatibility surface is wider than `main.py`).
  - Remove direct `main.py` references (already v2-only).
  - Remove lazy re-exports from `src/cli/__init__.py` for the legacy module names.
  - Migrate every test that imports from `src.cli.autotag` or `src.cli.dedupe` to use the v2 modules; delete tests whose only purpose was to exercise v1-only helpers that have no v2 equivalent.
  - Decision: **no deprecation shim**. Project is pre-1.0 (2.0.0-alpha.3 per CHANGELOG); we remove cleanly and note the breakage in CHANGELOG.
  - **Blocker scan** (must return zero hits before merge): run these six independent patterns across `src/`, `tests/`, `docs/`, `examples/`, `scripts/`:
    - `rg "from cli\.(autotag|dedupe) import"` — bare `from cli.x import` form (89 hits in tests alone)
    - `rg "from cli import (autotag|dedupe)(\b|[^_])"` — module-alias form, e.g. `from cli import dedupe as dedupe_mod` (1+ hit in tests)
    - `rg "from fo\.cli\.(autotag|dedupe) import"` — namespaced form
    - `rg "from src\.cli\.(autotag|dedupe) import"` — src-prefixed form
    - `rg "import cli\.(autotag|dedupe)(\b|[^_])"` — module-import form, excluding `cli.autotag_v2` / `cli.dedupe_v2`
    - `rg 'patch\(["'"'"']cli\.(autotag|dedupe)\.'` — **string-based patch targets** (38+ hits in tests — `mock.patch("cli.dedupe.console")`, `patch("cli.autotag.AutoTaggingService")` etc. This was missed in v2 and v3 and would leave the suite broken at runtime even with imports clean.)
  - For each hit: migrate to the v2 module, delete the test if it exercises v1-only helpers with no v2 equivalent, or move remaining coverage to the extracted modules before deletion.
- **D2** Split `src/pipeline/orchestrator.py` (881 LOC) — extract `ResourceAwareExecutor` owning prefetch, memory limiting, buffer rebalancing; orchestrator becomes a thin router. *m*
- **D3** Decouple `src/methodologies/para/detection/heuristics.py` (905 LOC) from Ollama — inject `AIInferenceAdapter` interface; heuristics never imports `ollama` directly. *m*
- **D4** Dedup viewer — extract `Renderer` interface with Rich / JSON / plain implementations from `src/services/deduplication/viewer.py` (608 LOC). *m*
- **D5** Preference tracker — extract `PreferenceStorage` interface from `src/services/intelligence/preference_tracker.py` (620 LOC). *m*
- **D6** Audio `src/services/audio/content_analyzer.py` (691 LOC) — externalize ~420 LOC of sentiment/keyword lexicons to config JSON loaded by a `SentimentLexicon` helper. *s*

### Epic E — Dependency Hygiene
- **E1** Bump `psutil~=5.9` → `>=6.2,<7`; validate across full Linux/macOS/Windows × py3.11–3.12 matrix. *s*
  - `pyproject.toml:76`.
  - PyPI latest is **psutil 7.2.2** (Jan 2026). This change intentionally does **not** adopt 7.x: PR #127 resolved a Windows-specific regression using `psutil.pid_exists()` behavior that shipped in the 6.x line, and the 7.x API changes haven't been exercised against fo-core's Windows matrix.
  - `<7` enforces the conservative stance; v3 initially proposed `<8` but that would have resolved to 7.x on fresh installs, contradicting the rationale.
  - Follow-up: a separate, future PR (not in this roadmap) revisits the upper bound once Windows CI is green on 7.x. F2 (daemon lockfile redesign) is a natural place to gate that revisit.
- **E2** Remove `continue-on-error: true` from the pip-audit job and replace with an enforced allowlist. *s*
  - `.github/workflows/security.yml:32`
  - **Mechanism:** `pip-audit` does not natively consume a YAML allowlist. Implementation:
    1. Add `.github/accepted-risks.yml` with schema `{ advisory_id, package, version_spec, reason, expires_on }`.
    2. Add `scripts/pip_audit_gate.py` that (a) runs `pip-audit --format=json`, (b) loads the allowlist, (c) fails iff the report contains any advisory not in the allowlist *and* matching the installed `(package, version)`, (d) fails on any allowlist entry that no longer matches an installed package/version (so stale entries can't linger).
    3. CI calls the wrapper instead of `pip-audit` directly; `continue-on-error` removed.
  - Seed allowlist entries: `ecdsa` (GHSA-wj6h-64fc-37mp, HS256-only), `diskcache` (GHSA-w8v5-vhqr-4h9v, unused). Each entry has `expires_on: 2026-10-22` (6 months) forcing re-review.
- **E3** Add upper-bound caps to pre-1.0 `>=` pins that lack them, respecting explicit "keep as-is" policy comments. *s*
  - Actual inventory from `pyproject.toml` (**18** total `>=0.x` pins — regenerated via `rg "^\s*\"[a-zA-Z][a-zA-Z0-9_.-]*(\[[^\]]+\])?>=0\." pyproject.toml`):
    - **Keep as-is (9 — already tagged `# 0.x — unstable API, keep >=`):** `ollama` (30), `ruff` (96), `pymarkdownlnt` (104), `deptry` (105), `llama-cpp-python` (117), `anthropic` (125), `mkdocs-minify-plugin` (165), `mkdocstrings[python]` (166), `rank-bm25` (171). These stay uncapped by design.
    - **Candidates for `<1` caps (9):** `python-pptx>=0.6.0` (37), `ebooklib>=0.18` (38), `striprtf>=0.0.26` (45), `py7zr>=0.20.0` (47), `loguru>=0.7.0` (79), `mlx-lm>=0.0.19` (121), `pydub>=0.25.0` (131), `scenedetect[opencv]>=0.6.0` (133), `imagededup>=0.3.0` (143).
  - Update `check_pypi_versions.py` pre-commit hook to warn on any pre-1.0 pin lacking either a cap *or* the exact marker comment `# 0.x — unstable API, keep >=` (exact string so the detector cannot rot).
  - **Implementation note:** regenerate the pin list from `pyproject.toml` at PR-open time rather than copying from this spec — versions and extras may drift between now and when E lands.
- **E4** Align and document the coverage-gate ladder (unit 95 / PR-diff 80 / main-push 93 / integration 71.9 / docstring 95) — one source of truth referenced from CONTRIBUTING.md, CI, and pre-commit rules. *s*
- **E5** Media-extra import-guard matrix — `pytest.importorskip` for `torch`, `cv2`, and transitive deps; document extra↔import map in `.claude/rules/test-generation-patterns.md` T8 table. *s*

### Epic F — Operational Health
**F.1 — Daemon / Watcher lifecycle:**
- **F1** Watcher queue backpressure + dropped-event metric instead of silent `deque(maxlen=...)` drop. *m*
  - `src/watcher/queue.py:63`
- **F2** Daemon PID-reuse race — add lock file (POSIX `fcntl.flock`) or inode/ctime validation. *m*
  - `src/daemon/pid.py:100-122`
- **F3** Debounce-dict TTL eviction to cap long-running memory growth. *s*
  - `src/watcher/handler.py:56-57,167-190`
- **F4** Signal-pipe overflow — log OSError, pre-drain in run loop. *s*
  - `src/daemon/service.py:397-401`

**F.2 — Data-at-rest integrity:**
- **F5** History-DB `PRAGMA integrity_check` on init + automated recovery prompt on corruption. *m*
  - `src/history/database.py:104-122`
- **F6** Config-schema migration path with explicit version bump handling and loud warning on unknown version. *m*
  - `src/config/schema.py:77`
  - `src/config/manager.py:94-108`
- **F7** Undo rollback durability — replace every `shutil.move()` in the rollback path with a move helper that is **atomic on same-device** and **durable + idempotent on cross-device (EXDEV)**. *m*
  - All four `rollback.py` movers: `src/undo/rollback.py:110` (undo move), `:169` (trash restore), `:257` (redo move), `:467` (delete-to-trash).
  - Provide `src/undo/durable_move.py::durable_move(src, dst, *, journal: Path)` with this contract:
    - **Same device:** `os.replace(src, dst)` — truly atomic on POSIX and Windows.
    - **Cross device (`EXDEV`):** copy-fsync-replace writes a journal entry (`{op, src, dst, state: started}`) *before* the copy begins, upgrades to `state: copied` after fsync, then unlinks source and marks `state: done`. A crash anywhere in the sequence leaves the journal in a recoverable state; the destination is either absent (crash before copy fsync) or complete (crash after).
    - **Recovery sweep:** at CLI startup, `durable_move.sweep(journal)` scans unfinished entries and completes or rolls back each one (delete the orphaned destination if state is `started`, unlink the orphaned source if state is `copied`).
  - **Not "atomic"** for EXDEV — the window between `replace` and source `unlink` briefly leaves both paths on disk. The journal + sweep make this observable and recoverable, not invisible.
  - Out-of-scope for F7 (intentionally different follow-up epics; see Appendix A.3): `src/services/audio/organizer.py:370`, `src/services/copilot/executor.py:168`, `src/methodologies/para/migration_manager.py:261`, `src/methodologies/para/ai/file_mover.py:213`, `src/updater/installer.py:242,265,288`.
  - Pair with F8: the trash-GC race is the concrete reason `:169` (trash restore) needs the `durable_move` helper.
- **F8** Trash GC race protection during concurrent `fo organize`. *m*
  - `src/undo/validator.py:512-533`

### Epic G — Cross-cutting & Meta
- **G1** Tracking roadmap meta-issue — links all epic meta-issues; closes when every child closes. *s*
- **G2** Pre-commit rail — block hardcoded `/tmp`, `~/`, `/Users/` in test files. *s*
- **G3** Pre-commit rail — AST check that CLI commands with a path argument route through the `validate_within_roots()` helper introduced in §2.5. *m*
- **G4** Promote T1 and T9 guardrails from diff-scoped to full-suite after C1 and C4 land. *s*
- **G5** Pre-commit rail — reject any YAML/shell file that introduces `-n\s*[=]?\s*auto` without `--dist=loadgroup` on the same pytest invocation. Prevents regression of the C2 inventory (see §3 C2 for the 8 invocations enumerated today). *s*

---

## 4. PR structure — 15 PRs across 7 epics

Strict "1 epic = 1 PR" was relaxed in v2 for C, D, F. v3 further splits A and B after the scope expansions from the first review round made single-PR review infeasible. Each epic remains one meta-issue; child PRs link to it.

| Epic | PR count | Child PRs | Rationale |
|------|---------|-----------|-----------|
| A | 3 | `hardening/epic-a-foundation` (§2.5 + `path_guard.py` + A1 walker sweep) <br> `hardening/epic-a-cli` (A2 CLI wiring — Appendix A.4) <br> `hardening/epic-a-creds` (A3 credential redaction) | Path-guard foundation is a tiny, surgical module that everything else consumes; CLI wiring is the largest chunk and benefits from being its own review; credential redaction is independent. |
| B | 3 | `hardening/epic-b-atomic` (B1a + B1b helpers and rewrites) <br> `hardening/epic-b-errors` (B2) <br> `hardening/epic-b-undo-db` (B3) | Atomic-write helpers land as a coherent utility + inventory. Error-boundary hygiene and DB-transaction work are independent concerns. |
| C | 2 | `hardening/epic-c-mechanical` (C1, C4) <br> `hardening/epic-c-design` (C2, C3, C5) | Separates mechanical cleanup from creative test-design work; review modes are incompatible when bundled. |
| D | 3 | `hardening/epic-d-cleanup` (D1, D6) — **merges early, before A** <br> `hardening/epic-d-pipeline` (D2, D3) <br> `hardening/epic-d-storage` (D4, D5) | Each architectural refactor needs its own "does this boundary make sense?" review frame. Cleanup moves early because A2 depends on legacy CLI being gone. |
| E | 1 | `hardening/epic-e-deps` | Five small CI/pin changes; coherent as a set. |
| F | 2 | `hardening/epic-f-lifecycle` (F1–F4) <br> `hardening/epic-f-integrity` (F5–F8) | Natural fault line between daemon/watcher runtime and disk-state integrity; F5/F7 are data-loss-class and deserve laser focus. |
| G | 1 | `hardening/epic-g-rails` (G2, G3, G4, G5) | G1 is filed as the roadmap tracking issue before any PR and is not part of this PR. |

**Branch naming:** `hardening/<epic-slug>[-<sub>]`
**PR title format:** `hardening(<axis>): <one-line summary>`
**Commit structure inside each PR:** one commit per finding, ordered as in Section 3; commit subject line references the finding ID (`A1: …`, `D2: …`, etc.).

---

## 5. Sequencing and dependencies

```text
G1 (tracking issue, no PR — created first)
  └─► E                      deps + CI gates (foundation, low risk)
       └─► D.cleanup         remove v1 CLI so A can harden only live commands
            └─► A.foundation §2.5 path model + path_guard.py + A1 walker sweep
                 └─► A.cli   validate_within_roots wired into Appendix A.4 commands
                 └─► A.creds credential redaction (parallel-safe with A.cli)
            └─► B.atomic     atomic_write helpers + B1a/B1b rewrites
                 └─► B.errors B2 error-boundary hygiene
                 └─► B.undo-db B3 transaction wrap
                      └─► C.mechanical    T1 + T9 drain
                           └─► C.design    xdist, T10, integration coverage
                                └─► epic-g-rails   G2/G3/G4/G5 (G4 requires C.mechanical)
                                     └─► D.pipeline
                                          └─► D.storage
                                               └─► F.lifecycle
                                                    └─► F.integrity  (reuses B.atomic helpers, adds F7 durable_move)
```

Rationale:
- Tighten CI/deps gates first (E) so regressions surface early.
- D.cleanup moves to slot 2 because A must harden *live* CLI, not legacy modules slated for deletion.
- A.foundation ships helper + mechanical walker rewrites together; A.cli and A.creds fan out in parallel after foundation merges.
- B.atomic ships helpers + all state-file rewrites so follow-on PRs (F.integrity for F7's `durable_move`) can reuse the utility module.
- Land remaining simplification (D.pipeline, D.storage) before ops work so F touches already-trimmed modules.
- G promotes guardrails only after the backlog drains (G4 after C.mechanical).

**Critical dependencies (enforced by branch-merge order):**
- A.foundation requires D.cleanup merged (legacy CLI modules removed from `main.py`, `cli/__init__.py`, tests).
- A.cli requires A.foundation merged (consumes `validate_within_roots`).
- A.creds is parallel-safe with A.cli after A.foundation.
- G3 (pre-commit rail checking CLI path validation) requires A.cli merged (so the AST check has real targets to verify).
- B.errors and B.undo-db are parallel-safe with each other after B.atomic.
- G4 requires C.mechanical merged.
- F.integrity's F7 reuses the `atomic_write` module introduced in B.atomic; merging after B.atomic is required.
- F.integrity's F8 (trash GC race) and F7's trash-restore site (`rollback.py:169`) share the same `durable_move` helper — both land in `epic-f-integrity`.
- D.pipeline's D3 (heuristics decoupling) benefits from C.design's C3 (predicate negative cases in heuristics) already landed so tests protect the refactor.

---

## 6. Per-PR quality gates

Every PR must pass before merge (codified in `.claude/rules/pr-workflow-master.md`):
1. `bash .claude/scripts/pre-commit-validation.sh`
2. `/code-reviewer`
3. `/simplify` — when touching shared utilities or extractable helpers
4. `/audit` — required for Epics A, B, F; optional elsewhere
5. CI green on all required checks (unit 95% line, PR-diff 80%, integration 71.9% line+branch)
6. All CodeRabbit / Copilot threads resolved per `.claude/rules/pr-review-response-protocol.md`

---

## 7. Explicit out-of-scope

Not part of any epic in this roadmap:
- New features, new CLI commands, new providers.
- UX or ergonomics work on existing commands.
- Performance optimization not tied to a correctness fix.
- Documentation refresh beyond doc-drift caught by existing `pymarkdown` and pre-commit rules.
- GUI / desktop-app changes.
- Issue-level work discovered mid-epic that is not on the finding list — new issues only, not scope creep.

---

## 8. Success criteria

The roadmap is complete when:
- All 15 PRs are merged.
- All 7 epic meta-issues and the G1 tracking issue are closed.
- G4's full-suite T1 and T9 guardrails are green on main.
- No regression in the five CI coverage gates.
- `pip-audit` CI job is enforcing (no `continue-on-error`) and green modulo the seeded accepted-risk allowlist.
- Post-roadmap retrospective issue filed — what the next hardening cycle should target (e.g., perf, UX, observability).

---

## 9. Out-of-band artifacts (created before PRs land)

- **G1 tracking issue** — created on GitHub before any PR. Links to this spec and to each epic meta-issue as they're opened.
- **One epic meta-issue per epic (7 total)** — created as the epic's PR author opens the first child PR. Links back to G1 and to this spec.
- **Accepted-risk allowlist file** — `.github/accepted-risks.yml` seeded in Epic E as part of E2.

---

## Appendix A — Call-site inventories

Generated 2026-04-22. Each subsection lists every matching site in `src/` so epic scope stays bounded to concrete code rather than "every X".

### A.1 `rglob` walkers — 20 total

**User-input walkers (18 sites — in A1 scope: filter symlinks + hidden by default):**

| File | Line | Caller context |
|------|------|----------------|
| `src/cli/suggest.py` | 43 | `suggest` command over user directory |
| `src/cli/benchmark.py` | 978 | Benchmark input discovery |
| `src/cli/doctor.py` | 173 | Directory scan in doctor checks |
| `src/cli/utilities.py` | 262 | `fo search` walker (recursive) |
| `src/cli/utilities.py` | 311 | `fo search` walker (query pattern) |
| `src/cli/utilities.py` | 313 | `fo search` walker (recursive fallback) |
| `src/services/analytics/storage_analyzer.py` | 130 | Analytics scan |
| `src/services/analytics/storage_analyzer.py` | 172 | Analytics second pass |
| `src/services/misplacement_detector.py` | 128 | Misplacement scan |
| `src/services/pattern_analyzer.py` | 236 | Pattern-analyzer scan |
| `src/services/copilot/executor.py` | 235 | Copilot search |
| `src/services/copilot/executor.py` | 325 | Copilot search (second site) |
| `src/services/copilot/rules/preview.py` | 111 | Rule preview scan |
| `src/services/deduplication/detector.py` | 112 | Dedup detector scan |
| `src/core/file_ops.py` | 200 | Empty-directory cleanup recursion |
| `src/methodologies/johnny_decimal/system.py` | 74 | JD catalog scan |
| `src/methodologies/para/ai/file_mover.py` | 258 | PARA mover scan |
| `src/methodologies/para/ai/file_mover.py` | 323 | PARA mover second scan |

**System-path walkers (2 sites — explicitly out of A1 scope):**

| File | Line | Why out of scope |
|------|------|------------------|
| `src/config/path_migration.py` | 61 | Walks legacy config dir owned by the tool — not user-supplied. |
| `src/undo/validator.py` | 529 | Walks configured trash dir — system path. |

### A.2 Direct file writes — 45 total

**Atomic-replace candidates (B1a scope, 17 sites):**

State files that would corrupt on crash mid-write.

| File | Line | Kind |
|------|------|------|
| `src/config/manager.py` | 144 | Active config |
| `src/config/manager.py` | 205 | Profile config |
| `src/config/path_migration.py` | 91 | Migration audit log (state) |
| `src/services/suggestion_feedback.py` | 368 | Feedback state |
| `src/services/copilot/rules/rule_manager.py` | 99 | Rule file |
| `src/methodologies/para/migration_manager.py` | 395 | Manifest |
| `src/methodologies/para/migration_manager.py` | 514 | Manifest (second site) |
| `src/methodologies/para/config.py` | 270 | PARA config |
| `src/methodologies/johnny_decimal/config.py` | 194 | JD config |
| `src/methodologies/johnny_decimal/migrator.py` | 326 | Rollback state |
| `src/methodologies/johnny_decimal/system.py` | 410 | JD state |
| `src/services/intelligence/folder_learner.py` | 303 | Learner state |
| `src/services/auto_tagging/tag_learning.py` | 452 | Tag-learner state |
| `src/events/discovery.py` | 137 | Event registry |
| `src/methodologies/para/ai/feedback.py` | 307 | Feedback state |
| `src/services/deduplication/embedder.py` | 271 | Vectorizer model (pickle, `"wb"`) — `save_model()` |
| `src/services/deduplication/embedder.py` | 314 | Embedding cache (pickle, `"wb"`) — `_save_cache()`; S6 rule |

**Append-durability candidates (B1b scope, 2 sites):**

| File | Line | Why append, not replace |
|------|------|-------------------------|
| `src/events/audit.py` | 246 | Audit log — order matters, truncate would lose history |
| `src/integrations/vscode.py` | 76–77 | VS Code JSONL command stream — append by protocol |

**Already using temp + replace (9 sites — no change needed, kept as reference pattern):**

`src/services/intelligence/profile_exporter.py:80,170`, `preference_store.py:285`, `profile_manager.py:199,268`, `profile_migrator.py:276`, `src/parallel/checkpoint.py:153`, `src/parallel/persistence.py:73`, `src/updater/state.py:83`.

**Out of B1 scope — one-shot user artifacts, not state (16 sites):**

`src/history/export.py:118,204,269,339` (user exports), `src/services/analytics/analytics_service.py:361,365` (reports), `src/services/deduplication/reporter.py:102,144` (reports), `src/services/video/scene_detector.py:356` (output), `src/services/intelligence/preference_store.py:485` (export), `src/services/suggestion_feedback.py:389` (export — reviewer flagged: takes `output_file` parameter, user-controlled target), `src/integrations/workflow.py:86,89` (Alfred/Raycast install artifacts), `src/integrations/obsidian.py:77` (user note), `src/cli/rules.py:220` (rule export), `src/cli/autotag.py:275` (deleted by D1).

**Special handling (F2, not B1):**

`src/daemon/pid.py:50` — PID file. Redesigned as part of F2 lockfile work, not covered by B1's generic atomic-replace helper.

### A.3 `shutil.move` call sites — 11 total

**F7 scope (4 sites, all in `src/undo/rollback.py`):**

| Line | Purpose |
|------|---------|
| 110 | Undo move (destination → source) |
| 169 | Trash restore (trash → original) |
| 257 | Redo move (source → destination) |
| 467 | Delete-to-trash (original → trash) |

**Out of F7 scope (7 sites — separate follow-up, not in this roadmap):**

| File | Line | Owner epic (future) |
|------|------|---------------------|
| `src/services/audio/organizer.py` | 370 | Follow-up: audio-pipeline atomicity |
| `src/services/copilot/executor.py` | 168 | Follow-up: copilot exec atomicity |
| `src/methodologies/para/migration_manager.py` | 261 | Follow-up: methodology migration atomicity |
| `src/methodologies/para/ai/file_mover.py` | 213 | Follow-up: AI mover atomicity |
| `src/updater/installer.py` | 242 | Follow-up: updater atomicity (separate risk model) |
| `src/updater/installer.py` | 265 | Follow-up: updater rollback |
| `src/updater/installer.py` | 288 | Follow-up: updater backup restore |

Rationale for deferral: these sit on different user-visible risk surfaces (audio organization, PARA migration, updater) and each deserves its own dedicated review. Bundling them with the undo-rollback work would re-create the blast-radius problem F was split to avoid. They'll be filed as a follow-up epic after the current roadmap lands.

### A.4 CLI entry points with path arguments (A2 scope)

Source of truth: `src/cli/main.py` registers each sub-app with a plain name (`app.add_typer(autotag_app, name="autotag")`), so the CLI command is `fo autotag` even though the Python module is `autotag_v2`. Every row below lists the **registered command name**, verified against each sub-app module (daemon subcommands against `daemon.py:34/83/115/139/170`, benchmark against `benchmark.py:922`, etc.).

| Module | Registered command | Path args | Line |
|--------|-------------------|-----------|------|
| `src/cli/organize.py` | `fo organize` | `INPUT_DIR`, `OUTPUT_DIR` | 76–77 |
| `src/cli/organize.py` | `fo preview` | `INPUT_DIR` | 144 |
| `src/cli/utilities.py` | `fo search` | `directory` (default `.`), `query` | 338–339 |
| `src/cli/utilities.py` | `fo analyze` | `file_path` (single file, not dir) | 372 |
| `src/cli/main.py` + `src/cli/analytics.py` | `fo analytics` | `directory` (optional) | main.py:220 |
| `src/cli/autotag_v2.py` | `fo autotag suggest` | `directory` | 36 |
| `src/cli/autotag_v2.py` | `fo autotag apply` | `file_path`, `tags` | 114–115 |
| `src/cli/autotag_v2.py` | `fo autotag batch` | `directory` | 197 |
| `src/cli/dedupe_v2.py` | `fo dedupe scan` | `directory` | 120 |
| `src/cli/dedupe_v2.py` | `fo dedupe resolve` | `directory` | 149 |
| `src/cli/dedupe_v2.py` | `fo dedupe report` | `directory` | 213 |
| `src/cli/rules.py` | `fo rules preview` | `directory`, `max_files` | 162 |
| `src/cli/rules.py` | `fo rules export` | `output` path option | 208 |
| `src/cli/rules.py` | `fo rules import` | `file` YAML path | 228 |
| `src/cli/daemon.py` | `fo daemon start` | `--watch-dir`, `--output-dir` (options) | 34–41 |
| `src/cli/daemon.py` | `fo daemon watch` | `watch_dir` (positional) | 139–141 |
| `src/cli/daemon.py` | `fo daemon process` | `input_dir`, `output_dir` (positional) | 170–173 |
| `src/cli/suggest.py` | `fo suggest <sub>` | `directory` (3 subcommands) | 53, 118, 179 |
| `src/cli/benchmark.py` | `fo benchmark run` (single subcommand) | `input_path` (positional), `--compare` → `compare_path: Path \| None` baseline JSON | 922–924, 956 |
| `src/cli/doctor.py` | `fo doctor` (single top-level command, not a sub-app) | `path` | 357 |
| `src/cli/profile.py` | `fo profile import` / `export` | path args (Click group, lazy-loaded) | deferred in main.py:245 |

Each of the above wires through `validate_within_roots()` as a commit inside `epic-a-cli`. Out of scope for A2: `fo copilot` (free-text `message`), `fo undo`/`redo`/`history` (operation IDs only), `fo version`/`hardware-info`/`config`/`model`/`setup`/`update`/`completion` (no path args).
