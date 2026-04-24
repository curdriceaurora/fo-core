"""Tests for ConfigManager and AppConfig."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config.manager import ConfigManager
from config.schema import AppConfig, ModelPreset, UpdateSettings
from models.base import DeviceType, ModelType

# ---------------------------------------------------------------------------
# AppConfig / ModelPreset defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAppConfigDefaults:
    """AppConfig should be constructable with zero arguments."""

    def test_default_construction(self) -> None:
        config = AppConfig()
        assert config.profile_name == "default"
        assert config.version == "1.0"
        assert config.default_methodology == "none"
        assert isinstance(config.models, ModelPreset)
        assert isinstance(config.updates, UpdateSettings)

    def test_model_preset_defaults(self) -> None:
        preset = ModelPreset()
        assert preset.text_model == "qwen2.5:3b-instruct-q4_K_M"
        assert preset.vision_model == "qwen2.5vl:7b-q4_K_M"
        assert preset.temperature == 0.5
        assert preset.max_tokens == 3000
        assert preset.device == "auto"
        assert preset.framework == "ollama"

    def test_module_overrides_default_to_none(self) -> None:
        config = AppConfig()
        assert config.watcher is None
        assert config.daemon is None
        assert config.parallel is None
        assert config.pipeline is None
        assert config.events is None
        assert config.deploy is None
        assert config.para is None
        assert config.johnny_decimal is None


# ---------------------------------------------------------------------------
# ConfigManager — load / save / list / delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigManagerLoadSave:
    """ConfigManager persistence round-trip."""

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path / "nonexistent")
        config = mgr.load()
        assert config.profile_name == "default"
        assert isinstance(config.models, ModelPreset)

    def test_save_creates_directory_and_file(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / "new_dir"
        mgr = ConfigManager(cfg_dir)
        mgr.save(AppConfig())

        config_file = cfg_dir / "config.yaml"
        assert config_file.exists()
        raw = yaml.safe_load(config_file.read_text())
        assert "profiles" in raw
        assert "default" in raw["profiles"]

    def test_round_trip(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path)
        original = AppConfig(
            profile_name="test",
            default_methodology="para",
            models=ModelPreset(
                text_model="custom-model:latest",
                temperature=0.8,
            ),
            updates=UpdateSettings(check_on_startup=False, interval_hours=72),
        )
        mgr.save(original, profile="test")
        loaded = mgr.load(profile="test")

        assert loaded.profile_name == "test"
        assert loaded.default_methodology == "para"
        assert loaded.models.text_model == "custom-model:latest"
        assert loaded.models.temperature == 0.8
        # Unset fields keep defaults
        assert loaded.models.vision_model == "qwen2.5vl:7b-q4_K_M"
        assert loaded.updates.check_on_startup is False
        assert loaded.updates.interval_hours == 72

    def test_save_preserves_other_profiles(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path)
        mgr.save(AppConfig(profile_name="a"), profile="a")
        mgr.save(AppConfig(profile_name="b"), profile="b")

        assert "a" in mgr.list_profiles()
        assert "b" in mgr.list_profiles()

    def test_load_invalid_yaml_returns_defaults(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("{{invalid yaml: [", encoding="utf-8")
        mgr = ConfigManager(tmp_path)
        config = mgr.load()
        assert config.profile_name == "default"

    def test_load_nondict_yaml_returns_defaults(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("42", encoding="utf-8")
        mgr = ConfigManager(tmp_path)
        config = mgr.load()
        assert config.profile_name == "default"


@pytest.mark.unit
class TestConfigManagerProfiles:
    """Profile listing and deletion."""

    def test_list_profiles_empty(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path / "empty")
        assert mgr.list_profiles() == []

    def test_list_profiles(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path)
        mgr.save(AppConfig(), profile="alpha")
        mgr.save(AppConfig(), profile="beta")
        assert mgr.list_profiles() == ["alpha", "beta"]

    def test_delete_profile(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path)
        mgr.save(AppConfig(), profile="doomed")
        assert "doomed" in mgr.list_profiles()
        assert mgr.delete_profile("doomed") is True
        assert "doomed" not in mgr.list_profiles()

    def test_delete_nonexistent_profile(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path)
        assert mgr.delete_profile("ghost") is False


# ---------------------------------------------------------------------------
# Module config delegation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelConfigDelegation:
    """ConfigManager.to_*_model_config() methods."""

    def test_to_text_model_config(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path)
        app_cfg = AppConfig(models=ModelPreset(text_model="my-text:latest", temperature=0.3))
        mc = mgr.to_text_model_config(app_cfg)
        assert mc.name == "my-text:latest"
        assert mc.model_type == ModelType.TEXT
        assert mc.temperature == 0.3
        assert mc.device == DeviceType.AUTO

    def test_to_vision_model_config(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path)
        app_cfg = AppConfig(models=ModelPreset(vision_model="my-vis:7b", device="mps"))
        mc = mgr.to_vision_model_config(app_cfg)
        assert mc.name == "my-vis:7b"
        assert mc.model_type == ModelType.VISION
        assert mc.device == DeviceType.MPS


@pytest.mark.unit
class TestModuleOverridesSerialization:
    """Module override dicts survive save/load."""

    def test_watcher_overrides_round_trip(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path)
        cfg = AppConfig(watcher={"recursive": False, "debounce_seconds": 5.0})
        mgr.save(cfg)
        loaded = mgr.load()
        assert loaded.watcher is not None
        assert loaded.watcher["recursive"] is False
        assert loaded.watcher["debounce_seconds"] == 5.0

    def test_none_overrides_not_serialized(self, tmp_path: Path) -> None:
        mgr = ConfigManager(tmp_path)
        cfg = AppConfig()
        mgr.save(cfg)
        raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
        profile_data = raw["profiles"]["default"]
        assert "watcher" not in profile_data
        assert "daemon" not in profile_data
        assert "updates" in profile_data


# ---------------------------------------------------------------------------
# F6 — Config schema migration path (hardening roadmap #159)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
@pytest.mark.integration
@pytest.mark.xdist_group("config_migrations_registry")
class TestConfigSchemaVersion:
    """F6: loading a config with a mismatched schema version must be
    handled explicitly — migrate known old versions, warn loudly on
    unknown/future versions rather than silently reading as defaults.
    """

    def test_current_schema_version_exported(self) -> None:
        """``CURRENT_SCHEMA_VERSION`` is exported from ``config.schema``
        and matches ``AppConfig().version`` default — bumping one
        forces updating the other via the import contract."""
        from config.schema import CURRENT_SCHEMA_VERSION

        assert CURRENT_SCHEMA_VERSION == AppConfig().version

    def test_load_accepts_current_version(self, tmp_path: Path) -> None:
        """A config saved with the current version loads cleanly,
        no warning emitted."""
        import logging

        mgr = ConfigManager(tmp_path)
        cfg = AppConfig(version=AppConfig().version, profile_name="default")
        mgr.save(cfg)

        caplog_records: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                caplog_records.append(record)

        handler = _Handler()
        handler.setLevel(logging.WARNING)
        root = logging.getLogger("config.manager")
        root.addHandler(handler)
        try:
            loaded = mgr.load()
        finally:
            root.removeHandler(handler)

        assert loaded.version == cfg.version
        version_warnings = [r for r in caplog_records if "version" in r.getMessage().lower()]
        assert not version_warnings, (
            f"current-version load should not warn; got {[r.getMessage() for r in version_warnings]}"
        )

    def test_load_future_version_warns_loudly(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """F6: a config from a newer fo version (unknown to this
        install) must WARN loudly so the operator knows the config may
        have fields this binary can't round-trip. We still load best
        effort — refusing would strand users mid-upgrade."""
        mgr = ConfigManager(tmp_path)
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump(
                {
                    "profiles": {
                        "default": {
                            "version": "99.0",  # from the future
                            "default_methodology": "none",
                        }
                    }
                }
            )
        )

        with caplog.at_level("WARNING", logger="config.manager"):
            loaded = mgr.load()

        # Must still load (best effort) — refusing would strand upgrades.
        assert isinstance(loaded, AppConfig)
        # Must have warned about the unknown version.
        msgs = [r.getMessage() for r in caplog.records]
        assert any("99.0" in m and "version" in m.lower() for m in msgs), (
            f"expected a loud warning about unknown version 99.0; got {msgs}"
        )

    def test_save_always_writes_current_version(self, tmp_path: Path) -> None:
        """F6: ``save`` stamps the CURRENT_SCHEMA_VERSION into the
        serialized record, even if the in-memory AppConfig has a
        stale version field (e.g. after loading an older config,
        migrating it, and re-saving)."""
        from config.schema import CURRENT_SCHEMA_VERSION

        mgr = ConfigManager(tmp_path)
        cfg = AppConfig()
        cfg.version = "0.5"  # pretend this was an ancient config
        mgr.save(cfg)

        raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
        profile_data = raw["profiles"]["default"]
        assert profile_data["version"] == CURRENT_SCHEMA_VERSION, (
            "save must stamp the current schema version so a later load "
            "doesn't re-trigger migration on already-migrated data"
        )

    def test_unversioned_config_classified_as_legacy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F6 codex P2 PRRT_kwDOR_Rkws59fwMM: configs with no
        ``version`` field (pre-F6 files) must be treated as
        ``LEGACY_CONFIG_VERSION``, NOT as ``CURRENT_SCHEMA_VERSION``.

        This matters after a future bump: if current becomes 2.0, an
        unversioned file is semantically 1.0 and must route through
        the 1.0→2.0 migration. If we defaulted to current (2.0),
        migrations would silently skip.

        Today ``CURRENT_SCHEMA_VERSION == LEGACY_CONFIG_VERSION == "1.0"``
        so with no monkeypatching the migration path early-returns and
        we can't observe the classification (coderabbit
        PRRT_kwDOR_Rkws59gscN: the previous skip-on-equal variant
        provided zero active coverage of the P2 fix). We simulate the
        post-bump state by monkeypatching ``CURRENT_SCHEMA_VERSION``
        to ``"2.0"`` everywhere it's read, register a sentinel
        migration keyed on ``LEGACY_CONFIG_VERSION`` ("1.0"), and
        assert the migration runs — proving the unversioned file was
        classified as legacy rather than current (which would skip
        all migrations).
        """
        import config.manager as manager_mod
        from config import migrations as migrations_mod
        from config import schema as schema_mod
        from config.schema import LEGACY_CONFIG_VERSION

        # Force divergence so the migration walker actually runs.
        # (No monkeypatch on ``migrations_mod``: the walker now uses
        # each ``Migration.to_version`` rather than reading a module-
        # level CURRENT_SCHEMA_VERSION — round-8 codex PRRT_kwDOR_Rkws59hdFY.
        # Setting only schema_mod + manager_mod is sufficient because
        # manager.load passes ``to_version=CURRENT_SCHEMA_VERSION`` as
        # an argument to migrate_to_current.)
        monkeypatch.setattr(schema_mod, "CURRENT_SCHEMA_VERSION", "2.0")
        monkeypatch.setattr(manager_mod, "CURRENT_SCHEMA_VERSION", "2.0")

        migration_ran = {"value": False}

        def legacy_migration(data: dict[str, object]) -> dict[str, object]:
            # Flag the migration and bump the internal version so the
            # walker's "reached current" check terminates cleanly.
            migration_ran["value"] = True
            data["version"] = "2.0"
            return data

        from config.migrations import Migration

        original = migrations_mod.MIGRATIONS.copy()
        try:
            migrations_mod.MIGRATIONS[LEGACY_CONFIG_VERSION] = Migration(
                to_version="2.0", transform=legacy_migration
            )
            cfg_path = tmp_path / "config.yaml"
            # Deliberately NO version field — simulates a pre-F6 file.
            cfg_path.write_text(
                yaml.dump(
                    {
                        "profiles": {
                            "default": {"default_methodology": "para"},
                        }
                    }
                )
            )

            loaded = ConfigManager(tmp_path).load()

            # Load succeeded AND the legacy-keyed migration actually
            # ran: proves the unversioned file was classified as
            # LEGACY_CONFIG_VERSION, not as CURRENT.
            assert isinstance(loaded, AppConfig)
            assert migration_ran["value"], (
                "migration keyed on LEGACY_CONFIG_VERSION must run when "
                "loading an unversioned config under simulated schema bump "
                "(codex P2 PRRT_kwDOR_Rkws59fwMM classification contract)"
            )
        finally:
            migrations_mod.MIGRATIONS.clear()
            migrations_mod.MIGRATIONS.update(original)

    def test_legacy_config_version_is_exported(self) -> None:
        """``LEGACY_CONFIG_VERSION`` must be importable from
        ``config.schema`` — it's the contract that unversioned files
        classify to, and F6 callers depend on its stability.
        """
        from config.schema import LEGACY_CONFIG_VERSION

        assert isinstance(LEGACY_CONFIG_VERSION, str)
        assert LEGACY_CONFIG_VERSION  # non-empty

    def test_migrate_registered_old_version(self, tmp_path: Path) -> None:
        """F6: registering a migration for an old version causes
        loads from that version to transform the data before
        AppConfig construction. Uses a synthetic migration so the
        contract is exercised without a real migration existing yet.
        """
        from config import migrations as migrations_mod

        # Register a synthetic migration 0.5 → current via monkeypatch.
        # The load path must invoke it and apply the transformation.
        def migrate_0_5_to_1_0(data: dict) -> dict:
            data.setdefault("default_methodology", "migrated-default")
            return data

        from config.migrations import Migration

        original_migrations = migrations_mod.MIGRATIONS.copy()
        try:
            migrations_mod.MIGRATIONS["0.5"] = Migration(
                to_version="1.0", transform=migrate_0_5_to_1_0
            )
            cfg_path = tmp_path / "config.yaml"
            cfg_path.write_text(
                yaml.dump(
                    {
                        "profiles": {
                            "default": {
                                "version": "0.5",
                                # no default_methodology — migration must add it
                            }
                        }
                    }
                )
            )
            loaded = ConfigManager(tmp_path).load()
            assert loaded.default_methodology == "migrated-default", (
                "migration for 0.5 should have added the default_methodology field"
            )
        finally:
            migrations_mod.MIGRATIONS.clear()
            migrations_mod.MIGRATIONS.update(original_migrations)

    def test_migration_failure_falls_back_to_defaults(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """F6: if a registered migration raises, the load path must
        log the failure and fall back to AppConfig defaults rather
        than crashing the CLI at startup."""
        from config import migrations as migrations_mod

        def broken_migration(data: dict) -> dict:
            raise RuntimeError("simulated bad migration")

        from config.migrations import Migration

        original_migrations = migrations_mod.MIGRATIONS.copy()
        try:
            migrations_mod.MIGRATIONS["0.5"] = Migration(
                to_version="1.0", transform=broken_migration
            )
            cfg_path = tmp_path / "config.yaml"
            cfg_path.write_text(yaml.dump({"profiles": {"default": {"version": "0.5"}}}))

            with caplog.at_level("ERROR", logger="config.manager"):
                loaded = ConfigManager(tmp_path).load()

            assert isinstance(loaded, AppConfig)
            msgs = [r.getMessage() for r in caplog.records]
            assert any("migration" in m.lower() for m in msgs), (
                "broken migration must be logged at ERROR"
            )
        finally:
            migrations_mod.MIGRATIONS.clear()
            migrations_mod.MIGRATIONS.update(original_migrations)

    def test_migrate_to_current_is_noop_when_versions_match(self) -> None:
        """F6: ``migrate_to_current`` short-circuits when the source
        matches the target so the chain-walk cost is zero for the
        common "already current" case."""
        from config.migrations import migrate_to_current

        data = {"version": "1.0", "key": "value"}
        result = migrate_to_current(data, from_version="1.0", to_version="1.0")
        assert result is data  # short-circuit: same object returned

    def test_migrate_to_current_skips_earlier_migrations(self) -> None:
        """F6: chain-walk pattern — if from_version is ``"1.0"`` and
        the registry has migrations for ``"0.5"`` and ``"1.0"``, only
        the ``"1.0"`` migration runs. The earlier one is skipped.
        """
        from config import migrations as migrations_mod
        from config.migrations import migrate_to_current

        calls: list[str] = []

        def mig_05(data: dict) -> dict:
            calls.append("0.5")
            data["v05_ran"] = True
            return data

        def mig_10(data: dict) -> dict:
            calls.append("1.0")
            data["v10_ran"] = True
            return data

        from config.migrations import Migration

        original = migrations_mod.MIGRATIONS.copy()
        try:
            migrations_mod.MIGRATIONS["0.5"] = Migration(to_version="1.0", transform=mig_05)
            migrations_mod.MIGRATIONS["1.0"] = Migration(to_version="2.0", transform=mig_10)
            # from_version is "1.0" — must skip the 0.5 migration.
            result = migrate_to_current({}, from_version="1.0", to_version="2.0")
            assert calls == ["1.0"]
            assert "v10_ran" in result
            assert "v05_ran" not in result
        finally:
            migrations_mod.MIGRATIONS.clear()
            migrations_mod.MIGRATIONS.update(original)

    def test_migrate_to_current_gap_in_registry_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """F6: if the registry has migrations for ``"0.5"`` and
        ``"2.0"`` but not ``"1.0"``, starting from 1.0 should WARN
        about the gap and return data unchanged rather than running
        the 2.0 migration on 1.0-shaped data.
        """
        from config import migrations as migrations_mod
        from config.migrations import Migration, migrate_to_current

        original = migrations_mod.MIGRATIONS.copy()
        try:
            migrations_mod.MIGRATIONS["0.5"] = Migration(to_version="1.0", transform=lambda d: d)
            migrations_mod.MIGRATIONS["2.0"] = Migration(
                to_version="3.0", transform=lambda d: d
            )  # gap at 1.0 between 0.5's output (1.0) and 2.0's input
            data = {"marker": "v1"}
            with caplog.at_level("WARNING", logger="config.migrations"):
                result = migrate_to_current(data, from_version="1.0", to_version="2.5")
            assert result == {"marker": "v1"}
            msgs = [r.getMessage() for r in caplog.records]
            assert any("No migration registered" in m for m in msgs)
        finally:
            migrations_mod.MIGRATIONS.clear()
            migrations_mod.MIGRATIONS.update(original)

    def test_migrate_to_current_exhausted_registry_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """F6: if the chain runs out before reaching ``to_version``
        (e.g. registry ends at 1.0 but target is 2.0), WARN about
        the incomplete migration and return best-effort."""
        from config import migrations as migrations_mod
        from config.migrations import Migration, migrate_to_current

        original = migrations_mod.MIGRATIONS.copy()
        try:
            migrations_mod.MIGRATIONS["0.5"] = Migration(to_version="1.0", transform=lambda d: d)
            # Chain ends at 0.5 → no 1.0 migration, so it stops.
            with caplog.at_level("WARNING", logger="config.migrations"):
                result = migrate_to_current(
                    {"marker": "start"},
                    from_version="0.5",
                    to_version="9.9",
                )
            assert result == {"marker": "start"}
            msgs = [r.getMessage() for r in caplog.records]
            assert any(
                "did not reach target version" in m or "No migration registered" in m for m in msgs
            ), f"expected exhaustion warning; got {msgs}"
        finally:
            migrations_mod.MIGRATIONS.clear()
            migrations_mod.MIGRATIONS.update(original)

    def test_migrate_to_current_reraises_migration_exception(self) -> None:
        """F6: ``migrate_to_current`` re-raises migration exceptions
        so the caller (ConfigManager.load) can log + fall back. Silent
        migration failure would produce subtly-wrong runtime config."""
        from config import migrations as migrations_mod
        from config.migrations import migrate_to_current

        def bad(data: dict) -> dict:
            raise RuntimeError("boom")

        from config.migrations import Migration

        original = migrations_mod.MIGRATIONS.copy()
        try:
            migrations_mod.MIGRATIONS["0.5"] = Migration(to_version="1.0", transform=bad)
            with pytest.raises(RuntimeError, match="boom"):
                migrate_to_current({}, from_version="0.5", to_version="1.0")
        finally:
            migrations_mod.MIGRATIONS.clear()
            migrations_mod.MIGRATIONS.update(original)

    def test_compare_versions_numeric_ordering(self) -> None:
        """Codex PRRT_kwDOR_Rkws59fzVk: ``compare_versions`` orders
        versions numerically, not lexicographically, so ``"10.0"``
        correctly compares greater than ``"2.0"``."""
        from config.migrations import compare_versions

        assert compare_versions("2.0", "10.0") < 0
        assert compare_versions("10.0", "2.0") > 0
        assert compare_versions("1.0", "1.0") == 0
        assert compare_versions("1.0.1", "1.0.0") > 0
        assert compare_versions("2.1", "2.0") > 0

    def test_compare_versions_non_numeric_fallback(self) -> None:
        """Malformed version strings fall back to ``(0,)`` so the
        walker treats them as very old rather than crashing."""
        from config.migrations import compare_versions

        # Non-numeric components should not raise.
        assert compare_versions("abc", "1.0") < 0  # (0,) < (1, 0)
        assert compare_versions("1.0", "abc") > 0

    def test_migration_walker_stops_on_registry_gap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Codex P2 PRRT_kwDOR_Rkws59hdFY: when the registry has a
        gap (e.g. ``{"0.5", "2.0"}`` with no ``"1.0"``) the walker
        must NOT jump ``current`` to 2.0 and run the 2.0 migration
        on 1.0-shaped data. It stops at 1.0 with a warning,
        returning the data as-produced by the 0.5 step.

        Also asserts ``Migration.to_version`` is the authoritative
        version tracker — the walker never infers it from the next
        registry key.
        """
        from config import migrations as migrations_mod
        from config.migrations import Migration, migrate_to_current

        calls: list[str] = []

        def mig_half(d: dict[str, object]) -> dict[str, object]:
            calls.append("0.5->1.0")
            d["after_half"] = True
            return d

        def mig_two(d: dict[str, object]) -> dict[str, object]:  # pragma: no cover
            # Should NEVER run in this scenario — the gap stops the
            # walker before 2.0 is reached.
            calls.append("2.0->3.0 (BUG: gap jumped)")
            d["after_two"] = True
            return d

        original = dict(migrations_mod.MIGRATIONS)
        try:
            migrations_mod.MIGRATIONS.clear()
            # Registry has 0.5 producing 1.0, and 2.0 producing 3.0 —
            # but NO migration from 1.0 → 2.0. Classic gap.
            migrations_mod.MIGRATIONS["0.5"] = Migration(to_version="1.0", transform=mig_half)
            migrations_mod.MIGRATIONS["2.0"] = Migration(to_version="3.0", transform=mig_two)

            result = migrate_to_current(
                {"starting": "at 0.5"}, from_version="0.5", to_version="3.0"
            )

            # Only the 0.5 migration ran; walker stopped at the gap.
            assert calls == ["0.5->1.0"], (
                f"walker must stop at the gap (current=1.0, no registered migration); "
                f"got calls={calls}"
            )
            assert result.get("after_half") is True
            assert "after_two" not in result, (
                "migration 2.0->3.0 must NOT have run on 1.0-shaped data "
                "(codex P2 PRRT_kwDOR_Rkws59hdFY)"
            )
        finally:
            migrations_mod.MIGRATIONS.clear()
            migrations_mod.MIGRATIONS.update(original)

    def test_migration_walker_stops_on_non_increasing_target(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Defensive: a migration that declares a ``to_version`` at or
        below its source version would otherwise loop forever. Walker
        logs an ERROR, aborts the transform BEFORE running the bad
        migration, and returns the input data unchanged.
        """
        from config import migrations as migrations_mod
        from config.migrations import Migration, migrate_to_current

        ran_flag: dict[str, bool] = {"value": False}

        def bad_migration(d: dict[str, object]) -> dict[str, object]:
            # Should NEVER run — the walker must short-circuit on the
            # non-increasing to_version check before invoking the
            # transform.
            ran_flag["value"] = True
            d["ran"] = True
            return d

        original = dict(migrations_mod.MIGRATIONS)
        try:
            migrations_mod.MIGRATIONS.clear()
            # Declares to_version == source (no progress).
            migrations_mod.MIGRATIONS["1.0"] = Migration(to_version="1.0", transform=bad_migration)

            input_data: dict[str, object] = {"marker": "unchanged"}
            with caplog.at_level("ERROR", logger="config.migrations"):
                result = migrate_to_current(input_data, from_version="1.0", to_version="2.0")

            # Walker short-circuited before calling the bad migration.
            assert ran_flag["value"] is False, (
                "walker must detect the non-increasing to_version and "
                "bail BEFORE invoking the transform; otherwise an infinite "
                "loop is possible"
            )
            # Data returned is the input dict, completely unmodified.
            assert result == {"marker": "unchanged"}
            assert "ran" not in result
            # Error was logged so operators can diagnose the bad config.
            msgs = [r.getMessage() for r in caplog.records]
            assert any("non-increasing target" in m for m in msgs), (
                f"expected non-increasing-target error; got {msgs}"
            )
        finally:
            migrations_mod.MIGRATIONS.clear()
            migrations_mod.MIGRATIONS.update(original)
