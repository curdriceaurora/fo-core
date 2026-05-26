"""Coverage tests for config.manager module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from config.manager import ConfigManager
from config.schema import AppConfig, ModelPreset, UpdateSettings

pytestmark = [pytest.mark.unit, pytest.mark.ci]


class TestConfigManagerInit:
    def test_default_config_dir(self):
        mgr = ConfigManager()
        assert mgr.config_dir is not None

    def test_custom_config_dir(self, tmp_path):
        mgr = ConfigManager(config_dir=tmp_path)
        assert mgr.config_dir == tmp_path


class TestConfigManagerLoad:
    def test_load_missing_file_returns_defaults(self, tmp_path):
        mgr = ConfigManager(config_dir=tmp_path)
        cfg = mgr.load("default")
        assert cfg.profile_name == "default"

    def test_load_invalid_yaml_returns_defaults(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("{{invalid yaml")
        mgr = ConfigManager(config_dir=tmp_path)
        cfg = mgr.load("default")
        assert cfg.profile_name == "default"

    def test_load_non_dict_returns_defaults(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump("just a string"))
        mgr = ConfigManager(config_dir=tmp_path)
        cfg = mgr.load("default")
        assert cfg.profile_name == "default"

    def test_load_missing_profile_returns_defaults(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"profiles": {"other": {}}}))
        mgr = ConfigManager(config_dir=tmp_path)
        cfg = mgr.load("default")
        assert cfg.profile_name == "default"

    def test_load_valid_profile(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        data = {
            "profiles": {
                "custom": {
                    "version": "2.0",
                    "default_methodology": "para",
                    "models": {"temperature": 0.5},
                }
            }
        }
        config_path.write_text(yaml.dump(data))
        mgr = ConfigManager(config_dir=tmp_path)
        cfg = mgr.load("custom")
        assert cfg.profile_name == "custom"
        assert cfg.default_methodology == "para"
        assert cfg.models.temperature == 0.5

    def test_load_non_dict_models_uses_default(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        data = {"profiles": {"test": {"models": "not-a-dict"}}}
        config_path.write_text(yaml.dump(data))
        mgr = ConfigManager(config_dir=tmp_path)
        cfg = mgr.load("test")
        assert isinstance(cfg.models, ModelPreset)

    def test_load_non_dict_updates_uses_default(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        data = {"profiles": {"test": {"updates": "not-a-dict"}}}
        config_path.write_text(yaml.dump(data))
        mgr = ConfigManager(config_dir=tmp_path)
        cfg = mgr.load("test")
        assert isinstance(cfg.updates, UpdateSettings)


class TestConfigManagerSave:
    def test_save_creates_dir_and_file(self, tmp_path):
        config_dir = tmp_path / "subdir"
        mgr = ConfigManager(config_dir=config_dir)
        cfg = AppConfig(profile_name="test")
        mgr.save(cfg)
        assert (config_dir / "config.yaml").exists()

    def test_save_preserves_other_profiles(self, tmp_path):
        mgr = ConfigManager(config_dir=tmp_path)
        cfg1 = AppConfig(profile_name="p1")
        cfg2 = AppConfig(profile_name="p2")
        mgr.save(cfg1)
        mgr.save(cfg2)

        profiles = mgr.list_profiles()
        assert "p1" in profiles
        assert "p2" in profiles

    def test_save_with_profile_override(self, tmp_path):
        mgr = ConfigManager(config_dir=tmp_path)
        cfg = AppConfig(profile_name="original")
        mgr.save(cfg, profile="overridden")

        profiles = mgr.list_profiles()
        assert "overridden" in profiles

    @pytest.mark.ci
    def test_save_overwrites_invalid_existing(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("invalid yaml: {{")
        mgr = ConfigManager(config_dir=tmp_path)
        cfg = AppConfig(profile_name="test")
        mgr.save(cfg)

        loaded = mgr.load("test")
        assert loaded.profile_name == "test"


class TestConfigManagerListProfiles:
    def test_empty_when_no_file(self, tmp_path):
        mgr = ConfigManager(config_dir=tmp_path)
        assert mgr.list_profiles() == []

    def test_empty_on_invalid_yaml(self, tmp_path):
        (tmp_path / "config.yaml").write_text("{{bad")
        mgr = ConfigManager(config_dir=tmp_path)
        assert mgr.list_profiles() == []

    def test_empty_on_non_dict(self, tmp_path):
        (tmp_path / "config.yaml").write_text(yaml.dump("string"))
        mgr = ConfigManager(config_dir=tmp_path)
        assert mgr.list_profiles() == []

    def test_empty_on_non_dict_profiles(self, tmp_path):
        (tmp_path / "config.yaml").write_text(yaml.dump({"profiles": "not-dict"}))
        mgr = ConfigManager(config_dir=tmp_path)
        assert mgr.list_profiles() == []

    def test_returns_sorted_names(self, tmp_path):
        mgr = ConfigManager(config_dir=tmp_path)
        mgr.save(AppConfig(profile_name="z"))
        mgr.save(AppConfig(profile_name="a"))
        assert mgr.list_profiles() == ["a", "z"]


class TestConfigManagerDeleteProfile:
    def test_delete_existing(self, tmp_path):
        mgr = ConfigManager(config_dir=tmp_path)
        mgr.save(AppConfig(profile_name="doomed"))
        assert mgr.delete_profile("doomed") is True
        assert "doomed" not in mgr.list_profiles()

    def test_delete_nonexistent(self, tmp_path):
        mgr = ConfigManager(config_dir=tmp_path)
        assert mgr.delete_profile("nope") is False

    def test_delete_no_file(self, tmp_path):
        mgr = ConfigManager(config_dir=tmp_path)
        assert mgr.delete_profile("any") is False

    @pytest.mark.ci
    def test_delete_invalid_yaml(self, tmp_path):
        (tmp_path / "config.yaml").write_text("{{bad")
        mgr = ConfigManager(config_dir=tmp_path)
        assert mgr.delete_profile("any") is False


class TestConfigManagerModuleDelegation:
    def test_to_text_model_config(self):
        mgr = ConfigManager()
        cfg = AppConfig()
        model_cfg = mgr.to_text_model_config(cfg)
        assert model_cfg.name == cfg.models.text_model

    def test_to_vision_model_config(self):
        mgr = ConfigManager()
        cfg = AppConfig()
        model_cfg = mgr.to_vision_model_config(cfg)
        assert model_cfg.name == cfg.models.vision_model

    def test_to_text_model_config_default_framework_yields_ollama_provider(self) -> None:
        """Default profile (framework=ollama) → ModelConfig.provider=ollama (#408 / #423)."""
        mgr = ConfigManager()
        cfg = AppConfig()
        model_cfg = mgr.to_text_model_config(cfg)
        assert model_cfg.framework == "ollama"
        assert model_cfg.provider == "ollama"

    def test_to_text_model_config_llama_cpp_framework_drives_provider(self) -> None:
        """framework=llama_cpp + model_path → provider=llama_cpp (this was the #423 P1 catch)."""
        mgr = ConfigManager()
        cfg = AppConfig(models=ModelPreset(framework="llama_cpp", model_path="/m/qwen3.gguf"))
        model_cfg = mgr.to_text_model_config(cfg)
        assert model_cfg.framework == "llama_cpp"
        assert model_cfg.provider == "llama_cpp"

    def test_to_text_model_config_mlx_framework_drives_provider(self) -> None:
        """framework=mlx + model_path → provider=mlx."""
        mgr = ConfigManager()
        cfg = AppConfig(models=ModelPreset(framework="mlx", model_path="mlx-community/Qwen2.5-3B"))
        model_cfg = mgr.to_text_model_config(cfg)
        assert model_cfg.framework == "mlx"
        assert model_cfg.provider == "mlx"

    def test_to_vision_model_config_llama_cpp_framework_drives_provider(self) -> None:
        """Vision converter mirrors text converter for the framework→provider mapping."""
        mgr = ConfigManager()
        cfg = AppConfig(models=ModelPreset(framework="llama_cpp", model_path="/m/qwen3.gguf"))
        model_cfg = mgr.to_vision_model_config(cfg)
        assert model_cfg.framework == "llama_cpp"
        assert model_cfg.provider == "llama_cpp"

    def test_to_text_model_config_unknown_framework_falls_back_to_ollama_provider(self) -> None:
        """Unknown framework string → provider=ollama (safe fallback)."""
        mgr = ConfigManager()
        cfg = AppConfig(models=ModelPreset(framework="ghosthypewriter"))
        model_cfg = mgr.to_text_model_config(cfg)
        # framework keeps the user's literal string (downstream may warn)
        assert model_cfg.framework == "ghosthypewriter"
        # provider falls back to ollama so executor routing stays sane
        assert model_cfg.provider == "ollama"

    def test_to_text_model_config_propagates_model_path(self) -> None:
        """Profile-set model_path lands on ModelConfig.model_path (#408 / #423).

        Companion to the framework→provider mapping: without this,
        profile-based ``framework: llama_cpp`` users had no way to
        persist their .gguf path and the executor failed at init.
        """
        mgr = ConfigManager()
        cfg = AppConfig(
            models=ModelPreset(
                framework="llama_cpp",
                model_path="/models/qwen3.gguf",
            )
        )
        model_cfg = mgr.to_text_model_config(cfg)
        assert model_cfg.provider == "llama_cpp"
        assert model_cfg.model_path == "/models/qwen3.gguf"

    def test_to_vision_model_config_propagates_model_path(self) -> None:
        """Same plumbing for the vision converter."""
        mgr = ConfigManager()
        cfg = AppConfig(
            models=ModelPreset(
                framework="mlx",
                model_path="mlx-community/Qwen2.5-3B-Instruct-4bit",
            )
        )
        model_cfg = mgr.to_vision_model_config(cfg)
        assert model_cfg.provider == "mlx"
        assert model_cfg.model_path == "mlx-community/Qwen2.5-3B-Instruct-4bit"

    def test_to_text_model_config_default_model_path_is_none(self) -> None:
        """Profiles without model_path produce a ModelConfig.model_path=None.

        Ollama (the default) doesn't need it; downstream executors for
        llama_cpp / mlx will raise loudly if invoked without one.
        """
        mgr = ConfigManager()
        cfg = AppConfig()
        model_cfg = mgr.to_text_model_config(cfg)
        assert model_cfg.model_path is None

    def test_to_text_model_config_llama_cpp_without_path_falls_back_to_ollama(
        self,
    ) -> None:
        """framework=llama_cpp + missing model_path → provider=ollama (defensive).

        Codex P1 catch on PR #423: setup-wizard profiles can land here
        with framework=llama_cpp but no model_path (setup.py never
        collected one, validate_config doesn't reject the combination).
        Routing those to the llama_cpp executor would crash at init;
        falling back to Ollama preserves the pre-fix silent behavior.
        """
        mgr = ConfigManager()
        cfg = AppConfig(models=ModelPreset(framework="llama_cpp"))  # no model_path
        model_cfg = mgr.to_text_model_config(cfg)
        # framework keeps the user's literal choice — they see the
        # mismatch if they read the log; we just don't crash on it.
        assert model_cfg.framework == "llama_cpp"
        assert model_cfg.provider == "ollama"
        assert model_cfg.model_path is None

    def test_to_text_model_config_mlx_without_path_falls_back_to_ollama(self) -> None:
        """Same defensive guard for mlx (Codex P1 catch on PR #423)."""
        mgr = ConfigManager()
        cfg = AppConfig(models=ModelPreset(framework="mlx"))  # no model_path
        model_cfg = mgr.to_text_model_config(cfg)
        assert model_cfg.framework == "mlx"
        assert model_cfg.provider == "ollama"

    def test_to_text_model_config_llama_cpp_blank_path_falls_back_to_ollama(
        self,
    ) -> None:
        """Whitespace-only model_path also triggers the guard."""
        mgr = ConfigManager()
        cfg = AppConfig(models=ModelPreset(framework="llama_cpp", model_path="   "))
        model_cfg = mgr.to_text_model_config(cfg)
        assert model_cfg.provider == "ollama"

    def test_to_vision_model_config_llama_cpp_without_path_falls_back_to_ollama(
        self,
    ) -> None:
        """Vision converter shares the same guard."""
        mgr = ConfigManager()
        cfg = AppConfig(models=ModelPreset(framework="llama_cpp"))
        model_cfg = mgr.to_vision_model_config(cfg)
        assert model_cfg.provider == "ollama"

    def test_provider_from_framework_handles_non_string_model_path(self) -> None:
        """Non-string model_path (e.g. int from manually edited YAML) doesn't crash.

        Codex P2 catch on PR #423: YAML can deserialize ``models.model_path``
        as a non-str (a stray ``models.model_path: 123`` integer, a
        ``Path`` from a programmatic caller, …). A naive ``.strip()``
        call would AttributeError; we coerce defensively via
        ``isinstance(model_path, str)``.
        """
        from config.manager import _provider_from_framework

        # int → guard treats as "no path", falls back to ollama
        assert _provider_from_framework("llama_cpp", model_path=123) == "ollama"
        # bytes → same fallback (str() coercion would have produced
        # b"…" garbage, so the defensive guard is correct)
        assert _provider_from_framework("llama_cpp", model_path=b"/m/x.gguf") == "ollama"
        # None → fallback (default arg path)
        assert _provider_from_framework("mlx", model_path=None) == "ollama"
        # Valid str path → still resolves to the local provider
        assert _provider_from_framework("llama_cpp", model_path="/m/x.gguf") == "llama_cpp"

    @patch("config.manager.WatcherConfig", create=True)
    def test_to_watcher_config(self, mock_watcher_cls):
        with patch("watcher.config.WatcherConfig", mock_watcher_cls, create=True):
            mgr = ConfigManager()
            cfg = AppConfig(watcher={"poll_interval": 2})
            mgr.to_watcher_config(cfg)

    def test_to_daemon_config(self):
        mgr = ConfigManager()
        cfg = AppConfig(daemon={"poll_interval": 2})
        result = mgr.to_daemon_config(cfg)
        assert result.poll_interval == 2

    def test_to_daemon_config_with_paths(self, tmp_path: Path):
        mgr = ConfigManager()
        watch_a = tmp_path / "a"
        out_dir = tmp_path / "out"
        cfg = AppConfig(
            daemon={
                "watch_directories": [str(watch_a)],
                "output_directory": str(out_dir),
            }
        )
        result = mgr.to_daemon_config(cfg)
        assert watch_a in result.watch_directories

    def test_config_to_dict_includes_overrides(self):
        mgr = ConfigManager()
        cfg = AppConfig(watcher={"poll": 1}, daemon={"poll_interval": 2})
        d = mgr.config_to_dict(cfg)
        assert "watcher" in d
        assert "daemon" in d

    def test_config_to_dict_excludes_none_overrides(self):
        mgr = ConfigManager()
        cfg = AppConfig()
        d = mgr.config_to_dict(cfg)
        assert "watcher" not in d
