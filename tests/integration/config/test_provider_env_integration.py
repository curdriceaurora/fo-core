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

from collections.abc import Callable
from unittest.mock import patch

import pytest

from config.provider_env import (
    _get_claude_configs,
    _get_llama_cpp_configs,
    _get_mlx_configs,
    _get_model_configs_from_profile,
    get_current_provider,
    get_model_configs,
    get_model_configs_from_env,
)
from models.base import ModelType
from models.text_model import TextModel
from models.vision_model import VisionModel

pytestmark = [pytest.mark.integration, pytest.mark.ci]


# ---------------------------------------------------------------------------
# get_current_provider — unknown value warning (lines 51-56)
# ---------------------------------------------------------------------------


class TestGetCurrentProviderIntegration:
    def test_unknown_provider_emits_warning_and_returns_ollama(
        self, provider_env: Callable[..., None]
    ) -> None:
        """Unrecognised FO_PROVIDER logs a warning and returns 'ollama'."""
        provider_env(FO_PROVIDER="gemini")

        result = get_current_provider()

        assert result == "ollama"

    def test_claude_provider_is_recognised(self, provider_env: Callable[..., None]) -> None:
        """'claude' is in the known-providers set and must not trigger the warning."""
        provider_env(FO_PROVIDER="claude")

        assert get_current_provider() == "claude"

    def test_llama_cpp_provider_is_recognised(self, provider_env: Callable[..., None]) -> None:
        """'llama_cpp' is a valid provider."""
        provider_env(FO_PROVIDER="llama_cpp")

        assert get_current_provider() == "llama_cpp"


# ---------------------------------------------------------------------------
# get_model_configs_from_env — ollama path (line 234)
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromEnvOllamaIntegration:
    def test_unset_provider_returns_ollama_text_defaults(
        self, provider_env: Callable[..., None]
    ) -> None:
        """When FO_PROVIDER is absent the function returns Ollama TextModel defaults."""
        provider_env()
        expected = TextModel.get_default_config()

        text_cfg, _ = get_model_configs_from_env()

        assert text_cfg.name == expected.name
        assert text_cfg.provider == "ollama"

    def test_unset_provider_returns_ollama_vision_defaults(
        self, provider_env: Callable[..., None]
    ) -> None:
        """When FO_PROVIDER is absent the function returns Ollama VisionModel defaults."""
        provider_env()
        expected = VisionModel.get_default_config()

        _, vision_cfg = get_model_configs_from_env()

        assert vision_cfg.name == expected.name
        assert vision_cfg.provider == "ollama"

    def test_explicit_ollama_provider_returns_defaults(
        self, provider_env: Callable[..., None]
    ) -> None:
        """Explicitly setting FO_PROVIDER=ollama also returns the default configs."""
        provider_env(FO_PROVIDER="ollama")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "ollama"
        assert vision_cfg.provider == "ollama"
        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION


# ---------------------------------------------------------------------------
# _get_llama_cpp_configs (lines 72-116, branch 237)
# ---------------------------------------------------------------------------


class TestGetLlamaCppConfigsIntegration:
    def test_model_path_propagates_to_both_configs(
        self, provider_env: Callable[..., None]
    ) -> None:
        """FO_LLAMA_CPP_MODEL_PATH is reflected in both text and vision configs."""
        provider_env(FO_PROVIDER="llama_cpp", FO_LLAMA_CPP_MODEL_PATH="/models/llama.gguf")

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.provider == "llama_cpp"
        assert vision_cfg.provider == "llama_cpp"
        assert text_cfg.model_path == "/models/llama.gguf"
        assert vision_cfg.model_path == "/models/llama.gguf"

    def test_model_types_are_correct(self, provider_env: Callable[..., None]) -> None:
        """Text config has ModelType.TEXT; vision config has ModelType.VISION."""
        provider_env(FO_LLAMA_CPP_MODEL_PATH="/models/llama.gguf")

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION

    def test_n_gpu_layers_added_to_extra_params(self, provider_env: Callable[..., None]) -> None:
        """Valid FO_LLAMA_CPP_N_GPU_LAYERS is parsed into extra_params."""
        provider_env(
            FO_LLAMA_CPP_MODEL_PATH="/models/llama.gguf",
            FO_LLAMA_CPP_N_GPU_LAYERS="32",
        )

        text_cfg, _ = _get_llama_cpp_configs()

        assert text_cfg.extra_params.get("n_gpu_layers") == 32

    def test_invalid_n_gpu_layers_is_ignored(self, provider_env: Callable[..., None]) -> None:
        """Non-integer FO_LLAMA_CPP_N_GPU_LAYERS does not crash — it is silently ignored."""
        provider_env(
            FO_LLAMA_CPP_MODEL_PATH="/models/llama.gguf",
            FO_LLAMA_CPP_N_GPU_LAYERS="not-a-number",
        )

        text_cfg, _ = _get_llama_cpp_configs()

        assert "n_gpu_layers" not in text_cfg.extra_params

    def test_missing_model_path_does_not_crash(self, provider_env: Callable[..., None]) -> None:
        """Absent FO_LLAMA_CPP_MODEL_PATH emits a warning but does not raise."""
        provider_env()

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.model_path == ""
        assert vision_cfg.model_path == ""

    def test_get_model_configs_from_env_routes_to_llama_cpp(
        self, provider_env: Callable[..., None]
    ) -> None:
        """FO_PROVIDER=llama_cpp routes through get_model_configs_from_env correctly."""
        provider_env(FO_PROVIDER="llama_cpp", FO_LLAMA_CPP_MODEL_PATH="/models/q4.gguf")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "llama_cpp"
        assert vision_cfg.provider == "llama_cpp"


# ---------------------------------------------------------------------------
# _get_mlx_configs (lines 130-160, branch 240)
# ---------------------------------------------------------------------------


class TestGetMlxConfigsIntegration:
    def test_model_path_propagates_to_both_configs(
        self, provider_env: Callable[..., None]
    ) -> None:
        """FO_MLX_MODEL_PATH appears in both text and vision configs."""
        provider_env(FO_MLX_MODEL_PATH="mlx-community/Qwen2.5-3B-4bit")

        text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.provider == "mlx"
        assert vision_cfg.provider == "mlx"
        assert text_cfg.model_path == "mlx-community/Qwen2.5-3B-4bit"
        assert vision_cfg.model_path == "mlx-community/Qwen2.5-3B-4bit"

    def test_model_types_are_correct(self, provider_env: Callable[..., None]) -> None:
        """MLX configs carry the correct ModelType values."""
        provider_env(FO_MLX_MODEL_PATH="mlx-community/Qwen2.5-3B-4bit")

        text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION

    def test_missing_model_path_does_not_crash(self, provider_env: Callable[..., None]) -> None:
        """Absent FO_MLX_MODEL_PATH emits a warning but does not raise."""
        provider_env()

        text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.model_path == ""
        assert vision_cfg.model_path == ""

    def test_get_model_configs_from_env_routes_to_mlx(
        self, provider_env: Callable[..., None]
    ) -> None:
        """FO_PROVIDER=mlx routes through get_model_configs_from_env correctly."""
        provider_env(FO_PROVIDER="mlx", FO_MLX_MODEL_PATH="mlx-community/Qwen2.5-3B-4bit")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "mlx"
        assert vision_cfg.provider == "mlx"


# ---------------------------------------------------------------------------
# _get_claude_configs (lines 174-206, branch 243)
# ---------------------------------------------------------------------------


class TestGetClaudeConfigsIntegration:
    def test_api_key_propagates_to_both_configs(self, provider_env: Callable[..., None]) -> None:
        """FO_CLAUDE_API_KEY is set on both text and vision configs."""
        provider_env(FO_CLAUDE_API_KEY="sk-ant-test123")

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.provider == "claude"
        assert vision_cfg.provider == "claude"
        assert text_cfg.api_key == "sk-ant-test123"
        assert vision_cfg.api_key == "sk-ant-test123"

    def test_model_types_are_correct(self, provider_env: Callable[..., None]) -> None:
        """Claude configs carry correct ModelType values."""
        provider_env(FO_CLAUDE_API_KEY="sk-ant-test")

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION

    def test_custom_text_model_name(self, provider_env: Callable[..., None]) -> None:
        """FO_CLAUDE_MODEL sets the text model name."""
        provider_env(
            FO_CLAUDE_API_KEY="sk-ant-test",
            FO_CLAUDE_MODEL="claude-3-haiku-20240307",
        )

        text_cfg, _ = _get_claude_configs()

        assert text_cfg.name == "claude-3-haiku-20240307"

    def test_vision_model_falls_back_to_text_model(
        self, provider_env: Callable[..., None]
    ) -> None:
        """When FO_CLAUDE_VISION_MODEL is unset, vision model name matches text model."""
        provider_env(
            FO_CLAUDE_API_KEY="sk-ant-test",
            FO_CLAUDE_MODEL="claude-3-haiku-20240307",
        )

        text_cfg, vision_cfg = _get_claude_configs()

        assert vision_cfg.name == text_cfg.name

    def test_separate_vision_model_name(self, provider_env: Callable[..., None]) -> None:
        """FO_CLAUDE_VISION_MODEL overrides the vision model name independently."""
        provider_env(
            FO_CLAUDE_API_KEY="sk-ant-test",
            FO_CLAUDE_MODEL="claude-3-haiku-20240307",
            FO_CLAUDE_VISION_MODEL="claude-3-5-sonnet-20241022",
        )

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.name == "claude-3-haiku-20240307"
        assert vision_cfg.name == "claude-3-5-sonnet-20241022"

    def test_no_api_key_emits_warning(self, provider_env: Callable[..., None]) -> None:
        """When neither FO_CLAUDE_API_KEY nor ANTHROPIC_API_KEY is set a warning is logged."""
        provider_env()

        # Must not raise — function should complete and return configs.
        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.provider == "claude"
        assert vision_cfg.provider == "claude"

    def test_anthropic_sdk_key_suppresses_warning(
        self, provider_env: Callable[..., None]
    ) -> None:
        """ANTHROPIC_API_KEY present means the SDK will pick it up — no warning needed."""
        provider_env(ANTHROPIC_API_KEY="sk-ant-sdk-key")

        text_cfg, _ = _get_claude_configs()

        assert text_cfg.provider == "claude"

    def test_get_model_configs_from_env_routes_to_claude(
        self, provider_env: Callable[..., None]
    ) -> None:
        """FO_PROVIDER=claude routes through get_model_configs_from_env correctly."""
        provider_env(FO_PROVIDER="claude", FO_CLAUDE_API_KEY="sk-ant-test")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "claude"
        assert vision_cfg.provider == "claude"


# ---------------------------------------------------------------------------
# OpenAI no-key warning (line 258)
# ---------------------------------------------------------------------------


class TestOpenAINoKeyWarningIntegration:
    def test_openai_without_key_or_url_does_not_crash(
        self, provider_env: Callable[..., None]
    ) -> None:
        """FO_PROVIDER=openai with no credentials set logs a warning but returns configs."""
        provider_env(FO_PROVIDER="openai")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "openai"
        assert vision_cfg.provider == "openai"
        assert text_cfg.api_key is None
        assert text_cfg.api_base_url is None

    def test_openai_sdk_key_suppresses_warning(self, provider_env: Callable[..., None]) -> None:
        """OPENAI_API_KEY suppresses the missing-credentials warning."""
        provider_env(FO_PROVIDER="openai", OPENAI_API_KEY="sk-sdk-key")

        text_cfg, _ = get_model_configs_from_env()

        assert text_cfg.provider == "openai"


# ---------------------------------------------------------------------------
# _get_model_configs_from_profile — default preset guard (line 308) and
# exception handling (lines 319-329)
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromProfileIntegration:
    def test_returns_none_when_profile_has_default_preset(self) -> None:
        """When the loaded profile is fully at defaults, None is returned."""
        from config.schema import AppConfig, ModelPreset

        default_app_cfg = AppConfig(models=ModelPreset())

        with patch("config.manager.ConfigManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.load.return_value = default_app_cfg

            result = _get_model_configs_from_profile("default")

        assert result is None

    def test_returns_none_on_os_error(self) -> None:
        """OSError during profile load is caught and None is returned."""
        with patch("config.manager.ConfigManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.load.side_effect = OSError("disk error")

            result = _get_model_configs_from_profile("default")

        assert result is None

    def test_returns_none_on_value_error(self) -> None:
        """ValueError during profile load is caught and None is returned."""
        with patch("config.manager.ConfigManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.load.side_effect = ValueError("bad config value")

            result = _get_model_configs_from_profile("default")

        assert result is None

    def test_returns_none_on_import_error(self) -> None:
        """ImportError (missing optional dependency) is caught and None is returned."""
        import sys

        with patch.dict(sys.modules, {"config.manager": None}):
            result = _get_model_configs_from_profile("default")

        assert result is None

    def test_returns_configs_when_profile_has_custom_preset(self) -> None:
        """Non-default profile values are returned as (text_cfg, vision_cfg)."""
        from config.schema import AppConfig, ModelPreset
        from models.base import ModelConfig

        custom_app_cfg = AppConfig(models=ModelPreset(text_model="llama3:8b"))
        text_cfg = ModelConfig(name="llama3:8b", model_type=ModelType.TEXT)
        vision_cfg = ModelConfig(name="llava:13b", model_type=ModelType.VISION)

        with patch("config.manager.ConfigManager") as mock_cls:
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
        self, provider_env: Callable[..., None]
    ) -> None:
        """When FO_PROVIDER is unset and no profile exists, Ollama defaults are used."""
        provider_env()

        with patch(
            "config.provider_env._get_model_configs_from_profile",
            return_value=None,
        ):
            text_cfg, vision_cfg = get_model_configs()

        assert text_cfg.provider == "ollama"
        assert vision_cfg.provider == "ollama"

    def test_env_provider_takes_priority_over_profile(
        self, provider_env: Callable[..., None]
    ) -> None:
        """FO_PROVIDER set means profile lookup is skipped entirely."""
        provider_env(FO_PROVIDER="openai", FO_OPENAI_API_KEY="sk-priority-test")

        text_cfg, _ = get_model_configs()

        assert text_cfg.provider == "openai"
