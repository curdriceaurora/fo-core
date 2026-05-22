# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0-beta.3] - 2026-05-21

### Fixed

- **`_compat` module missing from installed wheel** — `fo version` crashed with
  `ModuleNotFoundError: No module named '_compat'` on a clean `pipx install`
  because `setuptools packages.find` only discovers packages (directories with
  `__init__.py`), not bare `.py` files at the `src/` root. All 14 import sites
  used only `StrEnum`, which is stdlib since Python 3.11 (our minimum). Replaced
  every `from _compat import StrEnum` with `from enum import StrEnum` and deleted
  `_compat.py` (#344).

## [2.0.0-beta.2] - 2026-05-21

### Security

- **SafeDir watcher TOCTOU hardening** — `FileEventHandler` now inode-pins the
  move destination before replaying the event; the move is rejected if the inode
  changes between the open and the stat (PR #282, issue #270).
- **Undo inode-pin replay verification** — `durable_move` captures
  `(st_dev, st_ino, st_size)` at move time; `rollback.py` re-reads the triple
  before any undo replay and aborts if it differs. Pre-beta.2 history records
  without inode metadata fall back to a size-only check (PR5 / issue #269).
- **Dedupe TOCTOU hardening** — inode-pin unlink and `defusedxml` fail-closed
  for ODT parsing (PR #335, issue #323).
- **Anchored-traversal migration** — `extractor` and `hybrid_retriever` now
  validate all paths against the configured undo root via `Path.is_relative_to()`
  (PR #334, issue #325).
- **3 new AST pre-commit rails** (PR #329, issue #321–#323):
  - `safedir-valueerror` — flags `try` blocks calling SafeDir methods whose
    `except` clause omits `ValueError` (name-validation raises it for legal POSIX
    filenames containing characters SafeDir rejects, e.g. backslash).
  - `defusedxml-fallback` — flags `try: import defusedxml.X` / `except ImportError`
    handlers that silently re-enable stdlib XML parsing, restoring XXE/billion-laughs
    attack surface.
  - `textiowrapper-detach` — flags `io.TextIOWrapper(fileobj, ...)` constructions
    that never call `.detach()`, which would silently close the caller's stream on GC.
- **pip-audit allowlist cleaned** — stale ollama, torch, transformers, and joblib
  advisories removed after the pip-audit DB no longer reported them (PR #317).

### Fixed

- **Windows: `os.O_NOFOLLOW` fallback** — `rollback.py` now uses
  `getattr(os, "O_NOFOLLOW", 0)` so the module imports cleanly on Windows, which
  does not expose `O_NOFOLLOW` (PR #340, issue #339).
- **Windows CI: 3 pre-existing failures resolved** (PR #342, issue #341):
  - `_vision_helpers.py` — hardcoded `_EXTENSION_MIME` dict replaces
    `mimetypes.add_type` / `mimetypes.guess_type` for portable MIME resolution
    (Windows registry lacks `image/webp`).
  - `test_atomic_write.py` — concurrent-writers test skipped on Windows; POSIX
    `rename(2)` atomicity guarantee does not apply there.
  - `test_durable_move.py` — hardcoded `/a`/`/b` path literals replaced with
    `tmp_path`-relative paths.
- **pytest-timeout re-pinned** — `>=2.2.0,<2.4.0` avoids a Windows teardown crash
  introduced in 2.4.0 (PR #330).

### Changed

- **Coverage hardening** — 7 critical modules lifted to ≥90% line coverage:
  `rollback`, `durable_move`, `undo_manager`, `safedir`, `handler`, `monitor`,
  `backup` (PR #319, issue #318).

## [2.0.0-beta.1] - 2026-05-01

### Changed

- **Development status promoted from Alpha to Beta** — all five alpha→beta acceptance
  criteria are now met:
  1. ✅ `AudioModel` wired to the existing transcriber; `fo benchmark --suite audio` passes.
  2. ✅ Config schema version `1.0` frozen with a round-trip migration test.
  3. ✅ `--debug` flag, global setup gate, and hint-rich config validation errors.
  4. ✅ Integration coverage lifted to ≥70% on all 9 previously weak modules
     (measured via `scripts/coverage/integration_module_floor_baseline.json`,
     enforced by the `test-integration` CI job):
     search 38→95%, daemon 57→80%, dedup-init 60→95%, profile-migrator 60→70%,
     profile-merger 67→90%, JD adapters 67→90%, epub-enhanced 55→70%,
     hardware-profile 68→90%, JD numbering 68→70%.
  5. ✅ Beta criteria documented in `docs/release/beta-criteria.md`.
- Version bumped from `2.0.0-alpha.3` to `2.0.0-beta.1`.

## [Unreleased]

### Changed

- **Desktop app consolidated on pywebview** — removed the Tauri v2 / Rust / sidecar architecture
  in favour of a pure-Python approach: a single `fo-desktop` process starts uvicorn in
  a daemon thread and displays the web UI in a native OS window via pywebview. No Rust toolchain,
  no npm, no sidecar renaming steps required.
- **Build pipeline** — `python scripts/build.py --desktop` now produces a standalone pywebview
  desktop binary (`fo-desktop-{version}-{platform}-{arch}`) via PyInstaller, in
  addition to the existing CLI binary.
- **CI** — `build.yml` no longer requires the Rust toolchain or `cargo test`; the `test-rust`
  job has been removed and `release` now depends only on `build`.
- **Dependency hygiene (epic-e-deps, hardening roadmap #158, #161)** —
  - Bumped `psutil` from `~=5.9` to `>=6.2,<7`. Conservative 6.x-only bump; 7.x exists
    but hasn't been validated against the Windows matrix that PR #127 certified on 6.x.
  - Capped nine pre-1.0 dependency pins at `<1`
    (`python-pptx`, `ebooklib`, `striprtf`, `py7zr`, `loguru`, `mlx-lm`, `pydub`,
    `scenedetect[opencv]`, `imagededup`); preserved the nine `# 0.x — unstable API, keep >=`
    exceptions.
  - Extended `.claude/scripts/check_pypi_versions.py` to fail CI on any new pre-1.0 pin
    lacking either an upper-bound cap or the exact keep-as-is marker comment.

### Added

- `docs/developer/coverage-gates.md` — single source of truth for the five CI
  coverage gates (unit 95%, PR diff 80%, main push 93%, docstring 95%, integration
  71.9%) with the change protocol. Linked from `CONTRIBUTING.md`.

### Removed

- `desktop/src-tauri/` — Rust source, Cargo.toml, tauri.conf.json, capabilities, build.rs
- `desktop/package.json` — npm/Tauri dev scripts
- Sidecar copy steps from `scripts/build_linux.sh`, `scripts/build_macos.sh`,
  `scripts/build_windows.ps1`, and `scripts/build_windows.iss`
- `TAURI_SIGNING_*` environment variables from CI workflow

### Security

- **pip-audit is now enforcing (epic-e-deps)** — `.github/workflows/security.yml`
  no longer runs with `continue-on-error: true`. A new wrapper,
  `scripts/pip_audit_gate.py`, feeds pip-audit JSON through
  `.github/accepted-risks.yml` and fails the build on any unknown vulnerability,
  any expired/mismatched allowlist entry, or any allowlist entry whose package
  is no longer installed.
- **Allowlist ships empty.** The two previously-accepted risks were removed
  during epic-e-deps because both fall outside the base `pip install -e .`
  audit scope:
  - `ecdsa` (GHSA-wj6h-64fc-37mp): was transitive via `python-jose`, which
    is no longer a dependency of fo-core in any scope.
  - `diskcache` (GHSA-w8v5-vhqr-4h9v): only transitive via `llama-cpp-python`
    in the optional `[llama]` extra; tracked via the `[llama]`-specific audit
    path (future follow-up), not seeded in the base allowlist.

## [2.0.0-alpha.3] - 2026-03-26

### Quality & Stability Summary

This release contains **zero new user-facing features**—it is a pure quality and stability release covering 50 commits between March 9-26, 2026. The focus is on test infrastructure hardening, bug fixes, dependency updates, and security improvements that raise the project's reliability floor.

### Changed

- **Core Module Complexity Reduction** (#977): Refactored core modules to reduce cognitive complexity and improve maintainability
- **Test Parametrization** (#965, #966): Converted repetitive test cases to parametrized tests, reducing code duplication and improving coverage
- **Test Organization** (#964): Moved private helper tests to dedicated unit module for better test suite organization

### Fixed

- **Test Failures** (#969): Addressed 7 test failures across main test suite
- **pytest-timeout Compatibility** (#970): Pinned `pytest-timeout<2.4.0` to fix Windows CI crash
- **Path Keyword Matching** (f9ff398): Fixed feature extractor to match path keywords as exact components, not substrings
- **File Count Accuracy** (#937): Fixed deduplicated file counting in `OrganizationResult`
- **Threading Synchronization** (507cb35): Replaced busy-wait loop with `threading.Event().wait()` in test_warmup
- **Integration Test Stability** (#945): Fixed 5 failing integration tests on main branch
- **Flaky Assertions** (5b25653): Widened caplog scope to fix flaky cache-hit log assertion on Python 3.12

### Added

#### Testing & CI Improvements

- **Coverage Expansion**: Increased test coverage from 30% to 60% with ~4,500 new tests across integration, branch-coverage, and unit test suites
- **Branch Coverage** (#915): Enabled branch coverage tracking and established ratcheting coverage floors
- **Diff-Cover Gate** (#940): Added diff-cover gate to pre-commit validation to enforce coverage on changed lines
- **CI Guardrails**: Added 5 new guardrail categories:
  - T10 predicate negative-case guardrail (#939, #942)
  - MECE-hardened correctness, memory-lifecycle, and security guardrails (#935)
  - Search S1/S2 AST matching for corpus safety (#928, #929)
  - Phase 4 pre-commit hooks for threshold drift detection (#927)
  - isinstance assertion detection and enforcement (#926)
- **Integration Test Infrastructure** (#954): Added AsyncClient, CliRunner, and FakeTextModel fixtures
- **Integration Test Suites**:
  - 211 tests for methodologies, events, parallel processing (#963)
  - 4,112 integration tests ratcheting coverage 45%→60% (#961)
  - Web + plugins integration tests (#960)
  - API + web integration tests (#958)
  - CLI + models integration tests (#957)
  - 212 branch-coverage tests (#953, #949)
  - Branch-coverage tests for low-coverage modules (#947)

### Security

- **PyPDF2 Migration** (#848): Migrated PDF extraction from `PyPDF2` to `pypdf` (successor package) to resolve GHSA moderate vulnerability in `PyPDF2 3.0.1`
- **GitHub Actions Updates**: Bumped 6 GitHub Actions to latest versions:
  - `actions/upload-artifact` from 4 to 7 (#974)
  - `codecov/codecov-action` from 4.6.0 to 5.5.3 (#973)
  - `docker/metadata-action` from 5 to 6 (#975)
  - `docker/login-action` from 3 to 4 (#972)
  - `github/codeql-action` from 3 to 4 (#971)
  - `actions/checkout` from 5 to 6 (#875)
- **Rust Dependencies**: Updated 2 Rust dependencies:
  - `rustls-webpki` (#932)
  - `tar` (#923)
- **Risk Acceptance**:
  - Accepted risk for `ecdsa` (GHSA-wj6h-64fc-37mp, HIGH): transitive via `python-jose`; JWT algorithm is HS256 so `ecdsa` is never invoked
  - Accepted risk for `diskcache` (GHSA-w8v5-vhqr-4h9v, MODERATE): transitive via `llama-cpp-python`; never imported by application code

## [2.0.0-alpha.2] - 2026-03-09

### Added

- **Copilot Chat Interface** (#26): Natural-language AI assistant for file organisation
  - Interactive REPL and single-shot CLI modes
  - Intent parsing with 11 intent types (organize, move, rename, find, undo, redo, preview, suggest, status, help, chat)
  - Multi-turn conversation management with sliding-window context
  - TUI panel accessible via key `8`
- **Copilot Rules System** (#29): Automated file organisation rules
  - CRUD operations with YAML persistence
  - 8 condition types (extension, name pattern, size, content, date, path)
  - 7 action types (move, copy, rename, tag, categorize, archive, delete)
  - Preview engine for dry-run evaluation
  - CLI commands: list, sets, add, remove, toggle, preview, export, import
- **Auto-Update Mechanism** (#23): Self-updating from GitHub Releases
  - Version checking against GitHub Releases API
  - SHA256-verified downloads
  - Atomic binary replacement with backup/rollback
  - CLI commands: check, install, rollback
- **PyInstaller Build Pipeline** (#28): Cross-platform executable packaging
  - Build script with platform detection and spec generation
  - GitHub Actions CI for macOS (arm64/x86_64), Windows, Linux
- **macOS Packaging** (#14): DMG installer with optional code signing/notarization
- **Windows Packaging** (#16): Inno Setup installer with PATH integration
- **Linux Packaging** (#20): AppImage and tarball distribution
- **Integration Tests** (#12): 192 new tests across copilot, rules, updater, TUI, CLI, config, and build
- **User Documentation** (#13): User guide, CLI reference, configuration guide, troubleshooting

### Phase 2 Completion Summary

- Phase 2 (Enhanced UX) is now 100% complete: 24/24 tasks done
- TUI with 8 navigable views (Files, Organized, Analytics, Methodology, Audio, History, Settings, Copilot)
- Full CLI with 30+ sub-commands across 8 command groups
- 3,146 tests passing across Python 3.11-3.12
- ~54,000 LOC across 184 modules

## [2.0.0-alpha.1] - 2026-01-15

### Added

- Phase 1: Core text and image processing with Ollama
- Phase 3: Audio processing, PARA/JD methodologies, CAD/archive/scientific formats
- Phase 4: Deduplication, user preference learning, undo/redo, analytics
- Phase 5: Event system, daemon, Docker, CI/CD pipeline
