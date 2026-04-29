"""Schema-stability tests guarding the beta-line config-compat promise.

Beta-criteria §3: any config written by 2.0.0-beta.X reads cleanly under
2.0.0-beta.Y for all X, Y. The AppConfig schema stays at version 1.0 for
the duration of beta. These tests prove the round-trip is lossless and
that the migration walker handles synthetic version bumps + future
versions correctly.

This is the guard for the frozen-schema promise — any future PR that
breaks it fails the `ci` job.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from config.manager import ConfigManager
from config.schema import CURRENT_SCHEMA_VERSION, AppConfig, ModelPreset, UpdateSettings


def _make_realistic_config() -> AppConfig:
    """Build an AppConfig populated like a real user config.

    Non-default values across the surface so the round-trip can detect
    dropped fields. A pure-defaults config wouldn't catch e.g. a missing
    ``setup_completed=True`` field — defaults would mask the loss.

    ``profile_name`` stays as ``"default"`` because ``ConfigManager.save``
    keys the on-disk record by ``config.profile_name`` while
    ``ConfigManager.load`` defaults to ``profile="default"`` — using a
    custom name here would silently fall back to defaults on load.
    """
    return AppConfig(
        profile_name="default",
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
    """Same-version save → load round-trip must be lossless."""

    def test_save_load_round_trip_at_current_version(self, tmp_path: Path) -> None:
        """A config saved by the current binary loads back identically.

        Inject ``config_dir`` explicitly so the test is isolated from
        ``DEFAULT_CONFIG_DIR`` and the ``FO_CONFIG`` env var (which would
        otherwise take precedence per ``src/config/manager.py``).
        """
        original = _make_realistic_config()
        ConfigManager(config_dir=tmp_path).save(original)

        # Read back via a fresh manager pointing at the same tmp dir.
        roundtripped = ConfigManager(config_dir=tmp_path).load()

        # The round-tripped config must equal the original on every field.
        # Comparing via asdict() catches sub-dataclass field drift too
        # (e.g. ModelPreset losing `framework`).
        assert asdict(roundtripped) == asdict(original)


@pytest.mark.ci
@pytest.mark.integration
class TestConfigCrossVersionRoundTrip:
    """Cross-version compat: schema stays frozen but the walker still works."""

    def test_past_version_migration_preserves_all_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Write at a synthetic PAST version; the migration walker must
        preserve all fields when the migration is identity.

        We don't register an actual migration — beta promises the schema does
        not change across the line — but we DO promise that adding fields in
        the future won't drop existing fields. This test exercises that path
        using a registry monkeypatch, simulating what a future migration
        landing in beta.5 (say) would do during a beta.4 → beta.5 read.

        Renamed from ``test_synthetic_future_version_preserves_known_fields``
        per Copilot review on PR #238 — "future" was misleading because the
        on-disk version is OLDER than the binary's, and the migration walker
        ratchets it forward.
        """
        from config.migrations import MIGRATIONS, Migration

        # Save with the current binary, injecting config_dir explicitly so the
        # test is isolated from DEFAULT_CONFIG_DIR and the FO_CONFIG env var.
        original = _make_realistic_config()
        manager = ConfigManager(config_dir=tmp_path)
        manager.save(original)

        # Mutate the file's version to a synthetic past version; register a
        # no-op migration to current. This simulates a future beta whose
        # binary knows how to migrate from "0.9" to "1.0" — except for us
        # the migration is identity, proving "no fields dropped" when the
        # only schema delta is "version bumped". The version lives at
        # ``data["profiles"][profile_name]["version"]`` — top-level
        # ``data["version"]`` is unused by the loader.
        config_file = manager.config_path
        with config_file.open("r") as f:
            data = yaml.safe_load(f)
        data["profiles"]["default"]["version"] = "0.9"
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
        # Comparing the full asdict() (with `version` neutralized) catches
        # regressions on fields the test author forgot to enumerate
        # (e.g. daemon/parallel/pipeline/events/deploy/johnny_decimal
        # silently defaulting differently post-migration). Per Copilot
        # review on PR #238.
        assert loaded.version == CURRENT_SCHEMA_VERSION
        loaded_dict = asdict(loaded)
        original_dict = asdict(original)
        loaded_dict["version"] = original_dict["version"] = "<<ignored>>"
        assert loaded_dict == original_dict

    def test_future_version_loads_best_effort_with_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Config newer than the binary loads best-effort with a WARNING.

        If a user opts back into alpha after running beta, the alpha binary
        sees a future-version config. Per ``src/config/migrations.py`` the
        load proceeds best-effort with a WARNING. This test pins that
        contract — without it, a future change to make future-version
        configs hard-fail would silently break the alpha→beta→alpha
        round-trip flow that beta-criteria §3 implicitly relies on.
        """
        original = _make_realistic_config()
        manager = ConfigManager(config_dir=tmp_path)
        manager.save(original)

        config_file = manager.config_path
        with config_file.open("r") as f:
            data = yaml.safe_load(f)
        # As above, mutate the profile-scoped version, not top-level.
        data["profiles"]["default"]["version"] = "99.0"  # synthetic future
        with config_file.open("w") as f:
            yaml.safe_dump(data, f)

        # `config.manager` uses stdlib logging; caplog captures it directly.
        # Set level on the specific logger to defeat any propagation tweaks
        # other tests may have left in the global root.
        with caplog.at_level(logging.WARNING, logger="config.manager"):
            loaded = ConfigManager(config_dir=tmp_path).load()

        # Loaded best-effort: ALL known fields preserved (not just a
        # sampled subset — Copilot review on PR #238 caught that the
        # original 2-field check would let `models`/`updates`/`watcher`
        # etc. silently regress). Compare full asdict() with `version`
        # neutralized — it doesn't ratchet for future-version configs
        # the binary doesn't know how to migrate, but the contract for
        # known fields is unconditional.
        loaded_dict = asdict(loaded)
        original_dict = asdict(original)
        loaded_dict["version"] = original_dict["version"] = "<<ignored>>"
        assert loaded_dict == original_dict
        # And a warning was emitted naming the offending version. Check
        # both `getMessage()` (formatted) and the raw `args` tuple — the
        # warning uses %s formatting so the version may show up either
        # in the formatted text or the args list depending on capture
        # timing.
        warned_for_future_version = any(
            "99.0" in rec.getMessage() or "99.0" in str(rec.args or ()) for rec in caplog.records
        )
        assert warned_for_future_version, (
            "Expected a WARNING naming the future version; got: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )
