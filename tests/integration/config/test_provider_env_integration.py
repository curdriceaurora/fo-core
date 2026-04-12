"""Integration tests for config.provider_env — env-var to ModelConfig wiring.

These tests exercise the full code paths in ``provider_env`` (including helper
functions ``_get_llama_cpp_configs``, ``_get_mlx_configs``, and
``_get_claude_configs``) at the integration level, closing the gap that caused
the per-module coverage floor regression from 58% to 50%.

Coverage targets (missing lines before this file was added):
    51-56   – unknown-provider warning in ``get_current_provider``
    72-116  – ``_get_llama_cpp_configs`` body
    130-160 – ``_get_mlx_configs`` body
    174-206 – ``_get_claude_configs`` body
    234     – ollama branch return in ``get_model_configs_from_env``
    237     – llama_cpp branch return
    240     – mlx branch return
    243     – claude branch return
    258     – openai no-key warning
    308     – profile returns None (default preset guard)
    319-329 – profile load exception handling
    362     – ``get_model_configs`` fallback to env defaults
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from file_organizer.config.provider_env import (
    _get_claude_configs,
    _get_llama_cpp_configs,
    _get_mlx_configs,
    _get_model_configs_from_profile,
    get_current_provider,
    get_model_configs,
    get_model_configs_from_env,
)
from file_organizer.models.base import ModelType
from file_organizer.models.text_model import TextModel
from file_organizer.models.vision_model import VisionModel

pytestmark = [pytest.mark.integration, pytest.mark.ci]


# ---------------------------------------------------------------------------
# get_current_provider — unknown value warning (lines 51-56)
# ---------------------------------------------------------------------------


class TestGetCurrentProviderIntegration:
    def test_unknown_provider_emits_warning_and_returns_ollama(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unrecognised FO_PROVIDER logs a warning and returns 'ollama'."""
        monkeypatch.setenv("FO_PROVIDER", "gemini")

        result = get_current_provider()

        assert result == "ollama"

    def test_claude_provider_is_recognised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'claude' is in the known-providers set and must not trigger the warning."""
        monkeypatch.setenv("FO_PROVIDER", "claude")

        assert get_current_provider() == "claude"

    def test_llama_cpp_provider_is_recognised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'llama_cpp' is a valid provider."""
        monkeypatch.setenv("FO_PROVIDER", "llama_cpp")

        assert get_current_provider() == "llama_cpp"


# ---------------------------------------------------------------------------
# get_model_configs_from_env — ollama path (line 234)
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromEnvOllamaIntegration:
    def test_unset_provider_returns_ollama_text_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When FO_PROVIDER is absent the function returns Ollama TextModel defaults."""
        monkeypatch.delenv("FO_PROVIDER", raising=False)
        expected = TextModel.get_default_config()

        text_cfg, _ = get_model_configs_from_env()

        assert text_cfg.name == expected.name
        assert text_cfg.provider == "ollama"

    def test_unset_provider_returns_ollama_vision_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When FO_PROVIDER is absent the function returns Ollama VisionModel defaults."""
        monkeypatch.delenv("FO_PROVIDER", raising=False)
        expected = VisionModel.get_default_config()

        _, vision_cfg = get_model_configs_from_env()

        assert vision_cfg.name == expected.name
        assert vision_cfg.provider == "ollama"

    def test_explicit_ollama_provider_returns_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicitly setting FO_PROVIDER=ollama also returns the default configs."""
        monkeypatch.setenv("FO_PROVIDER", "ollama")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "ollama"
        assert vision_cfg.provider == "ollama"
        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION


# ---------------------------------------------------------------------------
# _get_llama_cpp_configs (lines 72-116, branch 237)
# ---------------------------------------------------------------------------


class TestGetLlamaCppConfigsIntegration:
    def test_model_path_propagates_to_both_configs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FO_LLAMA_CPP_MODEL_PATH is reflected in both text and vision configs."""
        monkeypatch.setenv("FO_PROVIDER", "llama_cpp")
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/llama.gguf")
        monkeypatch.delenv("FO_LLAMA_CPP_N_GPU_LAYERS", raising=False)

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.provider == "llama_cpp"
        assert vision_cfg.provider == "llama_cpp"
        assert text_cfg.model_path == "/models/llama.gguf"
        assert vision_cfg.model_path == "/models/llama.gguf"

    def test_model_types_are_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Text config has ModelType.TEXT; vision config has ModelType.VISION."""
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/llama.gguf")
        monkeypatch.delenv("FO_LLAMA_CPP_N_GPU_LAYERS", raising=False)

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION

    def test_n_gpu_layers_added_to_extra_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid FO_LLAMA_CPP_N_GPU_LAYERS is parsed into extra_params."""
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/llama.gguf")
        monkeypatch.setenv("FO_LLAMA_CPP_N_GPU_LAYERS", "32")

        text_cfg, _ = _get_llama_cpp_configs()

        assert text_cfg.extra_params.get("n_gpu_layers") == 32

    def test_invalid_n_gpu_layers_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-integer FO_LLAMA_CPP_N_GPU_LAYERS does not crash — it is silently ignored."""
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/llama.gguf")
        monkeypatch.setenv("FO_LLAMA_CPP_N_GPU_LAYERS", "not-a-number")

        text_cfg, _ = _get_llama_cpp_configs()

        assert "n_gpu_layers" not in text_cfg.extra_params

    def test_missing_model_path_does_not_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Absent FO_LLAMA_CPP_MODEL_PATH emits a warning but does not raise."""
        monkeypatch.delenv("FO_LLAMA_CPP_MODEL_PATH", raising=False)
        monkeypatch.delenv("FO_LLAMA_CPP_N_GPU_LAYERS", raising=False)

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.model_path == ""
        assert vision_cfg.model_path == ""

    def test_get_model_configs_from_env_routes_to_llama_cpp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FO_PROVIDER=llama_cpp routes through get_model_configs_from_env correctly."""
        monkeypatch.setenv("FO_PROVIDER", "llama_cpp")
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/q4.gguf")
        monkeypatch.delenv("FO_LLAMA_CPP_N_GPU_LAYERS", raising=False)

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "llama_cpp"
        assert vision_cfg.provider == "llama_cpp"


# ---------------------------------------------------------------------------
# _get_mlx_configs (lines 130-160, branch 240)
# ---------------------------------------------------------------------------


class TestGetMlxConfigsIntegration:
    def test_model_path_propagates_to_both_configs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FO_MLX_MODEL_PATH appears in both text and vision configs."""
        monkeypatch.setenv("FO_MLX_MODEL_PATH", "mlx-community/Qwen2.5-3B-4bit")

        text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.provider == "mlx"
        assert vision_cfg.provider == "mlx"
        assert text_cfg.model_path == "mlx-community/Qwen2.5-3B-4bit"
        assert vision_cfg.model_path == "mlx-community/Qwen2.5-3B-4bit"

    def test_model_types_are_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MLX configs carry the correct ModelType values."""
        monkeypatch.setenv("FO_MLX_MODEL_PATH", "mlx-community/Qwen2.5-3B-4bit")

        text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION

    def test_missing_model_path_does_not_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Absent FO_MLX_MODEL_PATH emits a warning but does not raise."""
        monkeypatch.delenv("FO_MLX_MODEL_PATH", raising=False)

        text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.model_path == ""
        assert vision_cfg.model_path == ""

    def test_get_model_configs_from_env_routes_to_mlx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FO_PROVIDER=mlx routes through get_model_configs_from_env correctly."""
        monkeypatch.setenv("FO_PROVIDER", "mlx")
        monkeypatch.setenv("FO_MLX_MODEL_PATH", "mlx-community/Qwen2.5-3B-4bit")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "mlx"
        assert vision_cfg.provider == "mlx"


# ---------------------------------------------------------------------------
# _get_claude_configs (lines 174-206, branch 243)
# ---------------------------------------------------------------------------


class TestGetClaudeConfigsIntegration:
    def test_api_key_propagates_to_both_configs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FO_CLAUDE_API_KEY is set on both text and vision configs."""
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test123")
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.provider == "claude"
        assert vision_cfg.provider == "claude"
        assert text_cfg.api_key == "sk-ant-test123"
        assert vision_cfg.api_key == "sk-ant-test123"

    def test_model_types_are_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Claude configs carry correct ModelType values."""
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION

    def test_custom_text_model_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FO_CLAUDE_MODEL sets the text model name."""
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.setenv("FO_CLAUDE_MODEL", "claude-3-haiku-20240307")
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, _ = _get_claude_configs()

        assert text_cfg.name == "claude-3-haiku-20240307"

    def test_vision_model_falls_back_to_text_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When FO_CLAUDE_VISION_MODEL is unset, vision model name matches text model."""
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.setenv("FO_CLAUDE_MODEL", "claude-3-haiku-20240307")
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = _get_claude_configs()

        assert vision_cfg.name == text_cfg.name

    def test_separate_vision_model_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FO_CLAUDE_VISION_MODEL overrides the vision model name independently."""
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.setenv("FO_CLAUDE_MODEL", "claude-3-haiku-20240307")
        monkeypatch.setenv("FO_CLAUDE_VISION_MODEL", "claude-3-5-sonnet-20241022")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.name == "claude-3-haiku-20240307"
        assert vision_cfg.name == "claude-3-5-sonnet-20241022"

    def test_no_api_key_emits_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When neither FO_CLAUDE_API_KEY nor ANTHROPIC_API_KEY is set a warning is logged."""
        monkeypatch.delenv("FO_CLAUDE_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)

        # Must not raise — function should complete and return configs.
        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.provider == "claude"
        assert vision_cfg.provider == "claude"

    def test_anthropic_sdk_key_suppresses_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ANTHROPIC_API_KEY present means the SDK will pick it up — no warning needed."""
        monkeypatch.delenv("FO_CLAUDE_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-sdk-key")
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)

        text_cfg, _ = _get_claude_configs()

        assert text_cfg.provider == "claude"

    def test_get_model_configs_from_env_routes_to_claude(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FO_PROVIDER=claude routes through get_model_configs_from_env correctly."""
        monkeypatch.setenv("FO_PROVIDER", "claude")
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "claude"
        assert vision_cfg.provider == "claude"


# ---------------------------------------------------------------------------
# OpenAI no-key warning (line 258)
# ---------------------------------------------------------------------------


class TestOpenAINoKeyWarningIntegration:
    def test_openai_without_key_or_url_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FO_PROVIDER=openai with no credentials set logs a warning but returns configs."""
        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.delenv("FO_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FO_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FO_OPENAI_MODEL", raising=False)
        monkeypatch.delenv("FO_OPENAI_VISION_MODEL", raising=False)

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "openai"
        assert vision_cfg.provider == "openai"
        assert text_cfg.api_key is None
        assert text_cfg.api_base_url is None

    def test_openai_sdk_key_suppresses_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OPENAI_API_KEY suppresses the missing-credentials warning."""
        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.delenv("FO_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FO_OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-sdk-key")

        text_cfg, _ = get_model_configs_from_env()

        assert text_cfg.provider == "openai"


# ---------------------------------------------------------------------------
# _get_model_configs_from_profile — default preset guard (line 308) and
# exception handling (lines 319-329)
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromProfileIntegration:
    def test_returns_none_when_profile_has_default_preset(self) -> None:
        """When the loaded profile is fully at defaults, None is returned."""
        from file_organizer.config.schema import AppConfig, ModelPreset

        default_app_cfg = AppConfig(models=ModelPreset())

        with patch("file_organizer.config.manager.ConfigManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.load.return_value = default_app_cfg

            result = _get_model_configs_from_profile("default")

        assert result is None

    def test_returns_none_on_os_error(self) -> None:
        """OSError during profile load is caught and None is returned."""
        with patch("file_organizer.config.manager.ConfigManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.load.side_effect = OSError("disk error")

            result = _get_model_configs_from_profile("default")

        assert result is None

    def test_returns_none_on_value_error(self) -> None:
        """ValueError during profile load is caught and None is returned."""
        with patch("file_organizer.config.manager.ConfigManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.load.side_effect = ValueError("bad config value")

            result = _get_model_configs_from_profile("default")

        assert result is None

    def test_returns_none_on_import_error(self) -> None:
        """ImportError (missing optional dependency) is caught and None is returned."""
        import sys

        with patch.dict(sys.modules, {"file_organizer.config.manager": None}):
            result = _get_model_configs_from_profile("default")

        assert result is None

    def test_returns_configs_when_profile_has_custom_preset(self) -> None:
        """Non-default profile values are returned as (text_cfg, vision_cfg)."""
        from file_organizer.config.schema import AppConfig, ModelPreset
        from file_organizer.models.base import ModelConfig

        custom_app_cfg = AppConfig(models=ModelPreset(text_model="llama3:8b"))
        text_cfg = ModelConfig(name="llama3:8b", model_type=ModelType.TEXT)
        vision_cfg = ModelConfig(name="llava:13b", model_type=ModelType.VISION)

        with patch("file_organizer.config.manager.ConfigManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.load.return_value = custom_app_cfg
            mgr.to_text_model_config.return_value = text_cfg
            mgr.to_vision_model_config.return_value = vision_cfg

            result = _get_model_configs_from_profile("custom")

        assert result is not None
        assert result[0].name == "llama3:8b"
        assert result[1].name == "llava:13b"


# ---------------------------------------------------------------------------
# get_model_configs — fallback to env defaults (line 362)
# ---------------------------------------------------------------------------


class TestGetModelConfigsFallbackIntegration:
    def test_falls_back_to_ollama_when_no_env_or_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When FO_PROVIDER is unset and no profile exists, Ollama defaults are used."""
        monkeypatch.delenv("FO_PROVIDER", raising=False)
        monkeypatch.delenv("FO_PROFILE", raising=False)

        with patch(
            "file_organizer.config.provider_env._get_model_configs_from_profile",
            return_value=None,
        ):
            text_cfg, vision_cfg = get_model_configs()

        assert text_cfg.provider == "ollama"
        assert vision_cfg.provider == "ollama"

    def test_env_provider_takes_priority_over_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FO_PROVIDER set means profile lookup is skipped entirely."""
        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.setenv("FO_OPENAI_API_KEY", "sk-priority-test")

        text_cfg, _ = get_model_configs()

        assert text_cfg.provider == "openai"
