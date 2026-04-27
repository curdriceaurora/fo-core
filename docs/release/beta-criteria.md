# Beta Criteria

This document defines what "beta" means for fo-core, the criteria for entering
beta, and the contract between the project and public pre-release testers.

## 1. What "beta" means here

fo-core's beta is a low-ceremony label. The transition consists of two
mechanical changes: bumping the PyPI classifier from `Development Status :: 3 -
Alpha` to `Development Status :: 4 - Beta` (in `pyproject.toml`), and opening
the auto-updater's public pre-release channel (`fo update --pre-release`,
already wired in `src/cli/update.py`).

Beta is **not** a feature freeze. Development continues normally, and new
commands, flags, dependencies, or methodologies may land in any beta point
release. Read the changelog before upgrading.

Beta is **not** time-boxed. `4 - Beta` is the steady state until the maintainer
decides otherwise. There is no committed timeline for bumping to
`5 - Production/Stable`; see §5.

The single guarantee that beta adds beyond alpha is the schema-compatibility
contract in §3.

## 2. Entry checklist

These items must all be true to bump the classifier from `3 - Alpha` to
`4 - Beta` and cut `2.0.0-beta.1`.

- [ ] **Audio works end-to-end.** `AudioModel.generate()` (currently raises
      `NotImplementedError` at `src/models/audio_model.py:53`) is wired to the
      existing transcriber code. `fo benchmark --suite audio` succeeds on a
      sample audio file with the `[media]` extra installed. The `[media]` extra
      description in `pyproject.toml` and the README's Optional Feature Packs
      table accurately describe what ships.
- [ ] **Integration coverage floors.** Global integration coverage ≥ 75%
      (currently 71.9%). Per-module floor ≥ 70% on every module currently
      below it in
      `scripts/coverage/integration_module_floor_baseline.json`. The nine
      modules below 70% as of this writing are: `services/search/__init__.py`
      (38%), `utils/epub_enhanced.py` (55%), `daemon/service.py` (57%),
      `services/deduplication/__init__.py` (60%),
      `services/intelligence/profile_migrator.py` (60%),
      `methodologies/johnny_decimal/adapters.py` (67%),
      `services/intelligence/profile_merger.py` (67%),
      `core/hardware_profile.py` (68%),
      `methodologies/johnny_decimal/numbering.py` (68%). Search and daemon are
      the highest-risk and the tallest hills.
- [ ] **Daemon smoke test in CI** exercising `start → watch → stop → status`
      and recovery after `SIGTERM`. The test runs in the integration job and
      blocks merge on failure.
- [ ] **`--debug` flag wired** in `src/cli/main.py`. The flag enables full
      loguru handlers and surfaces tracebacks (currently swallowed by the
      global error handler that re-emits as `[red]Error: {message}[/red]`).
      The flag is documented in `docs/troubleshooting.md`.
- [ ] **Doc-honesty pass.** Every documented command, extra, and flag exists
      and works as described. No `NotImplementedError` is reachable from a
      documented surface. README, `docs/cli-reference.md`, and
      `docs/USER_GUIDE.md` all match what the code does.
- [ ] **Schema-stability test** in CI that writes a config with one
      `AppConfig` version, reads it back with another, and asserts equality.
      Parameterized over the last alpha and a synthetic future beta to exercise
      both the alpha → beta boundary and the beta.X → beta.Y guarantee in §3.
      `AppConfig` stays at version `1.0` for the duration of beta.
- [ ] **Bug-report template exists** at `.github/ISSUE_TEMPLATE/beta-bug.md`,
      requesting `fo --debug <command>` output, the `fo doctor` summary,
      OS/version, and reproduction steps.

## 3. The one stable promise: frozen schema across beta.X → beta.Y

Any config file written by `2.0.0-beta.X` reads cleanly under
`2.0.0-beta.Y` for all `X`, `Y` in the beta line, with no manual migration.

The `AppConfig` schema version stays at `1.0` for the duration of beta. New
optional fields may be added (with sensible defaults), but no existing field is
renamed, retyped, or removed during beta. A round-trip test in CI guards this
promise.

The schema may bump after the beta line ends. That decision is out of scope
for this document.

This is the only beta-line compatibility guarantee. Everything else (CLI flags,
dependency versions, default models, methodology behavior) may change between
point releases.

## 4. Tester contract

### Opting in

```bash
fo update --pre-release   # switch your update channel to the beta line
fo update check           # the auto-updater will now offer beta releases
```

Once opted in, `fo update install` will pull the latest beta point release.

### Rolling back

```bash
pip install 'fo-core==2.0.0-alpha.3'   # or whatever stable version you prefer
fo update --no-pre-release             # leave the pre-release channel
```

Because the schema is frozen across the beta line (§3), rolling back to alpha
may require deleting your config if you started fresh on beta. Rolling between
beta point releases is always safe.

### Filing bugs

Open an issue using the **Beta bug** template at GitHub Issues. Every report
must include:

- Output of `fo --debug <the failing command>` (full traceback, not just the
  red error line).
- Output of `fo doctor` (Ollama connection + dependency check).
- OS, Python version, fo-core version (`fo version`).
- Minimum reproduction steps.

Reports without `--debug` output will usually be asked to re-run with the flag
before triage proceeds.

### Expectations

- Beta point releases may add commands, flags, dependencies, or change
  defaults. Read the changelog before upgrading.
- The schema promise in §3 is the only compatibility guarantee.
- There is no SLA on bug response time. fo-core is maintained by a small
  team; beta participation is appreciated, not contractually owed.

## 5. Explicit non-promise about GA

There is no committed timeline for bumping `4 - Beta` to
`5 - Production/Stable`. The maintainer will make that call when warranted —
likely driven by a sustained absence of critical bug reports against current
beta releases, but no specific gates are pre-committed.

Public pre-release testers should not assume GA is imminent or planned for any
particular window. Treat beta as the project's current steady state.
