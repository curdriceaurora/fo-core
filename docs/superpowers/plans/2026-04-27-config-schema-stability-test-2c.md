# Config Schema Stability Test (Step 2C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CI-blocking test that proves the schema-frozen promise from `docs/release/beta-criteria.md` §3 actually holds — a config written by one `AppConfig` version reads cleanly under another, with no manual migration required.

**Architecture:** The test parameterizes over (a) the current `CURRENT_SCHEMA_VERSION` (alpha→beta boundary today, where the schema does not change) and (b) a synthetic future bump that exercises the migration walker even when no real migration is registered. The test writes a fully-populated `AppConfig` to a YAML file via `ConfigManager.save`, mutates the version in the file to the source version, then loads via `ConfigManager.load` and asserts equality on the round-tripped object. Lives under `@pytest.mark.ci` so it runs on every PR.

**Tech Stack:** pytest, dataclasses, PyYAML (already a dep). No new dependencies.

**Out of scope:** Adding actual migrations (none are needed during the beta line per §3). Schema versioning UX in `fo config show`. Integration with on-disk fixture files from prior versions.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `tests/integration/test_config_schema_stability.py` | Create | The schema-stability test — round-trips configs across version boundaries |
| `pyproject.toml` (`tool.pytest.ini_options`) | No change needed | The `ci` marker is already defined |

Plan conventions: see [2A plan](2026-04-27-audio-model-wiring-2a.md) "Conventions for this plan" section.

---

## Task 1: Round-trip an AppConfig with no version change

**Files:**
- Create: `tests/integration/test_config_schema_stability.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_config_schema_stability.py`:

```python
"""Schema-stability tests guarding the beta-line config-compat promise.

Beta-criteria §3: any config written by 2.0.0-beta.X reads cleanly under
2.0.0-beta.Y for all X, Y. The AppConfig schema stays at version 1.0 for
the duration of beta. These tests prove the round-trip is lossless.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from config.manager import ConfigManager
from config.schema import CURRENT_SCHEMA_VERSION, AppConfig, ModelPreset, UpdateSettings


def _make_realistic_config() -> AppConfig:
    """Build an AppConfig populated like a real user config — non-default
    values across the surface so the round-trip can detect dropped fields."""
    return AppConfig(
        profile_name="beta-tester",
        version=CURRENT_SCHEMA_VERSION,
        default_methodology="para",
        setup_completed=True,
        models=ModelPreset(
            text_model="qwen2.5:7b",
            vision_model="qwen2.5vl:14b",
            temperature=0.3,
            max_tokens=4096,
            device="mps",
            framework="ollama",
        ),
        updates=UpdateSettings(
            check_on_startup=True,
            interval_hours=12,
            include_prereleases=True,
            repo="curdriceaurora/fo-core",
        ),
        watcher={"enabled": True, "interval_seconds": 30},
        para={"areas_root": "Areas", "projects_root": "Projects"},
    )


@pytest.mark.ci
@pytest.mark.integration
class TestConfigRoundTrip:
    def test_save_load_round_trip_at_current_version(
        self, tmp_path: Path
    ) -> None:
        # Inject config_dir explicitly so the test is isolated from
        # DEFAULT_CONFIG_DIR and the FO_CONFIG env var (which would
        # otherwise take precedence per src/config/manager.py:62-65).
        original = _make_realistic_config()
        ConfigManager(config_dir=tmp_path).save(original)

        # Read back via a fresh manager pointing at the same tmp dir
        roundtripped = ConfigManager(config_dir=tmp_path).load()

        # The round-tripped config must equal the original on every field
        assert asdict(roundtripped) == asdict(original)
```

- [ ] **Step 2: Run the test to verify it passes (or surfaces a real bug)**

```bash
pytest tests/integration/test_config_schema_stability.py::TestConfigRoundTrip::test_save_load_round_trip_at_current_version -v
```

Expected: PASS. If it FAILS, the failure surfaces a real schema-stability bug — investigate before committing the test (which field dropped? did a default override an explicit value?).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_config_schema_stability.py
git commit -m "test(config): round-trip AppConfig at current schema version"
```

---

## Task 2: Round-trip across a synthetic version bump (exercises migration walker)

**Files:**
- Modify: `tests/integration/test_config_schema_stability.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_config_schema_stability.py`:

```python
@pytest.mark.ci
@pytest.mark.integration
class TestConfigCrossVersionRoundTrip:
    def test_synthetic_future_version_preserves_known_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Write a config tagged at a synthetic future version, ensure load()
        preserves all fields the current binary knows about.

        We don't register an actual migration — beta promises the schema does
        not change across the line — but we DO promise that adding fields in
        the future won't drop existing fields. This test exercises that path
        using a registry monkeypatch, simulating what a future migration
        landing in beta.5 (say) would do during a beta.4 → beta.5 read.
        """
        from config.migrations import MIGRATIONS, Migration

        # Save with the current binary, injecting config_dir explicitly so the
        # test is isolated from DEFAULT_CONFIG_DIR and the FO_CONFIG env var.
        original = _make_realistic_config()
        manager = ConfigManager(config_dir=tmp_path)
        manager.save(original)

        # Mutate the file's version to a synthetic past version, register a
        # no-op migration to current. This simulates a future beta whose
        # binary knows how to migrate from "0.9" to "1.0" — except for us
        # the migration is identity, proving "no fields dropped" when the
        # only schema delta is "version bumped".
        config_file = manager.config_path
        with config_file.open("r") as f:
            data = yaml.safe_load(f)
        data["version"] = "0.9"
        with config_file.open("w") as f:
            yaml.safe_dump(data, f)

        def _identity_migration(d: dict[str, object]) -> dict[str, object]:
            d["version"] = CURRENT_SCHEMA_VERSION
            return d

        monkeypatch.setitem(
            MIGRATIONS,
            "0.9",
            Migration(to_version=CURRENT_SCHEMA_VERSION, transform=_identity_migration),
        )

        loaded = ConfigManager(config_dir=tmp_path).load()

        # Every field except `version` round-trips identically. `version`
        # is updated to current after the migration (correct behavior).
        assert loaded.version == CURRENT_SCHEMA_VERSION
        assert loaded.profile_name == original.profile_name
        assert loaded.default_methodology == original.default_methodology
        assert loaded.setup_completed == original.setup_completed
        assert asdict(loaded.models) == asdict(original.models)
        assert asdict(loaded.updates) == asdict(original.updates)
        assert loaded.watcher == original.watcher
        assert loaded.para == original.para
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/integration/test_config_schema_stability.py::TestConfigCrossVersionRoundTrip -v
```

Expected: PASS — migration walker should run the no-op transform and produce a config equal to the original.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_config_schema_stability.py
git commit -m "test(config): round-trip across synthetic version bump via migration walker"
```

---

## Task 3: Future-version handling — config newer than binary loads best-effort

**Files:**
- Modify: `tests/integration/test_config_schema_stability.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
    def test_future_version_loads_best_effort_with_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If a user opts back into alpha after running beta, the alpha binary
        sees a future-version config. Per migrations.py the load proceeds
        best-effort with a WARNING. This test pins that contract."""
        import logging

        original = _make_realistic_config()
        manager = ConfigManager(config_dir=tmp_path)
        manager.save(original)

        config_file = manager.config_path
        with config_file.open("r") as f:
            data = yaml.safe_load(f)
        data["version"] = "99.0"  # synthetic future
        with config_file.open("w") as f:
            yaml.safe_dump(data, f)

        with caplog.at_level(logging.WARNING):
            loaded = ConfigManager(config_dir=tmp_path).load()

        # Loaded best-effort: known fields preserved
        assert loaded.profile_name == original.profile_name
        assert loaded.setup_completed == original.setup_completed
        # And a warning was emitted naming the offending version
        assert any("99.0" in rec.message for rec in caplog.records), (
            "Expected a WARNING naming the future version; got: "
            f"{[r.message for r in caplog.records]}"
        )
```

- [ ] **Step 2: Run**

```bash
pytest tests/integration/test_config_schema_stability.py::TestConfigCrossVersionRoundTrip::test_future_version_loads_best_effort_with_warning -v
```

Expected: PASS, given the existing migration-walker behavior in `src/config/migrations.py:170-184`.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_config_schema_stability.py
git commit -m "test(config): future-version configs load best-effort with WARNING"
```

---

## Task 4: Pre-commit + CI verification

- [ ] **Step 1: Run pre-commit validation**

```bash
bash .claude/scripts/pre-commit-validation.sh
```

Expected: PASS.

- [ ] **Step 2: Confirm the test runs in the `ci` marker subset**

```bash
pytest -m "ci" tests/integration/test_config_schema_stability.py -v
```

Expected: all three test methods run and PASS. They will then run on every PR via the `test` job in `.github/workflows/ci.yml`.

- [ ] **Step 3: Push branch and open PR**

Title: `test(config): schema-stability round-trip suite for beta line`

Body should reference §3 of `docs/release/beta-criteria.md` and call out that this is the guard for the frozen-schema promise.

---

## Verification checklist

After this plan executes:

- The `ci` marker subset includes three new tests proving (a) round-trip at current version is lossless, (b) round-trip across a synthetic version bump preserves all fields, (c) future-version configs load best-effort with a logged WARNING.
- Any future PR that breaks the schema-stability promise fails the `ci` job.

This satisfies the "Schema-stability test in CI" row of beta-criteria.md §2.
