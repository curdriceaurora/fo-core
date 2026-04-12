"""Integration tests for config.provider_env — env-to-ModelConfig wiring.

These tests exercise the full provider dispatch paths in isolation from external
services.  All network calls are absent (no HTTP clients initialised); tests
verify that environment variable combinations produce the correct ModelConfig
values.
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

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# get_current_provider — unknown provider warning path
# ---------------------------------------------------------------------------


class TestGetCurrentProviderUnknownValue:
    def test_unknown_provider_falls_back_to_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_PROVIDER", "unknown_provider_xyz")

        result = get_current_provider()

        assert result == "ollama"

    def test_unknown_provider_emits_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_PROVIDER", "bogus")

        with patch("file_organizer.config.provider_env.logger.warning") as mock_warn:
            get_current_provider()

        assert mock_warn.called
        # Warning message should mention the unrecognised value
        call_args = mock_warn.call_args_list[0]
        assert "bogus" in str(call_args) or "FO_PROVIDER" in str(call_args)


# ---------------------------------------------------------------------------
# _get_llama_cpp_configs
# ---------------------------------------------------------------------------


class TestGetLlamaCppConfigs:
    def test_model_path_propagated_to_both_configs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/llama3.gguf")
        monkeypatch.delenv("FO_LLAMA_CPP_N_GPU_LAYERS", raising=False)

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.model_path == "/models/llama3.gguf"
        assert vision_cfg.model_path == "/models/llama3.gguf"

    def test_provider_field_is_llama_cpp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/llama3.gguf")
        monkeypatch.delenv("FO_LLAMA_CPP_N_GPU_LAYERS", raising=False)

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.provider == "llama_cpp"
        assert vision_cfg.provider == "llama_cpp"

    def test_model_types_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/llama3.gguf")
        monkeypatch.delenv("FO_LLAMA_CPP_N_GPU_LAYERS", raising=False)

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION

    def test_n_gpu_layers_added_to_extra_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/llama3.gguf")
        monkeypatch.setenv("FO_LLAMA_CPP_N_GPU_LAYERS", "32")

        text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.extra_params["n_gpu_layers"] == 32
        assert vision_cfg.extra_params["n_gpu_layers"] == 32

    def test_invalid_n_gpu_layers_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/llama3.gguf")
        monkeypatch.setenv("FO_LLAMA_CPP_N_GPU_LAYERS", "not_a_number")

        with patch("file_organizer.config.provider_env.logger.warning") as mock_warn:
            text_cfg, _vision_cfg = _get_llama_cpp_configs()

        # Should not crash; n_gpu_layers absent from extra_params
        assert "n_gpu_layers" not in text_cfg.extra_params
        # Warning emitted for bad value
        warning_text = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "not_a_number" in warning_text or "FO_LLAMA_CPP_N_GPU_LAYERS" in warning_text

    def test_missing_model_path_emits_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FO_LLAMA_CPP_MODEL_PATH", raising=False)
        monkeypatch.delenv("FO_LLAMA_CPP_N_GPU_LAYERS", raising=False)

        with patch("file_organizer.config.provider_env.logger.warning") as mock_warn:
            text_cfg, vision_cfg = _get_llama_cpp_configs()

        assert text_cfg.model_path == ""
        assert vision_cfg.model_path == ""
        warning_text = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "FO_LLAMA_CPP_MODEL_PATH" in warning_text


# ---------------------------------------------------------------------------
# _get_mlx_configs
# ---------------------------------------------------------------------------


class TestGetMlxConfigs:
    def test_model_path_propagated_to_both_configs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_MLX_MODEL_PATH", "mlx-community/Qwen2.5-3B-Instruct-4bit")

        text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.model_path == "mlx-community/Qwen2.5-3B-Instruct-4bit"
        assert vision_cfg.model_path == "mlx-community/Qwen2.5-3B-Instruct-4bit"

    def test_provider_field_is_mlx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_MLX_MODEL_PATH", "/local/mlx-model")

        text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.provider == "mlx"
        assert vision_cfg.provider == "mlx"

    def test_model_types_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_MLX_MODEL_PATH", "/local/mlx-model")

        text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION

    def test_missing_model_path_emits_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FO_MLX_MODEL_PATH", raising=False)

        with patch("file_organizer.config.provider_env.logger.warning") as mock_warn:
            text_cfg, vision_cfg = _get_mlx_configs()

        assert text_cfg.model_path == ""
        assert vision_cfg.model_path == ""
        warning_text = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "FO_MLX_MODEL_PATH" in warning_text


# ---------------------------------------------------------------------------
# _get_claude_configs
# ---------------------------------------------------------------------------


class TestGetClaudeConfigs:
    def test_provider_field_is_claude(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.provider == "claude"
        assert vision_cfg.provider == "claude"

    def test_api_key_propagated_to_both_configs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-secret")
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.api_key == "sk-ant-secret"
        assert vision_cfg.api_key == "sk-ant-secret"

    def test_model_types_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION

    def test_custom_text_model_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.setenv("FO_CLAUDE_MODEL", "claude-3-opus-20240229")
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, _ = _get_claude_configs()

        assert text_cfg.name == "claude-3-opus-20240229"

    def test_vision_model_falls_back_to_text_model_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.setenv("FO_CLAUDE_MODEL", "claude-3-opus-20240229")
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        _, vision_cfg = _get_claude_configs()

        assert vision_cfg.name == "claude-3-opus-20240229"

    def test_separate_vision_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.setenv("FO_CLAUDE_MODEL", "claude-3-5-haiku-20241022")
        monkeypatch.setenv("FO_CLAUDE_VISION_MODEL", "claude-3-5-sonnet-20241022")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = _get_claude_configs()

        assert text_cfg.name == "claude-3-5-haiku-20241022"
        assert vision_cfg.name == "claude-3-5-sonnet-20241022"

    def test_warning_when_no_api_key_at_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FO_CLAUDE_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)

        with patch("file_organizer.config.provider_env.logger.warning") as mock_warn:
            text_cfg, vision_cfg = _get_claude_configs()

        # Should not crash; api_key is None
        assert text_cfg.api_key is None
        assert vision_cfg.api_key is None
        warning_text = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "FO_CLAUDE_API_KEY" in warning_text or "ANTHROPIC_API_KEY" in warning_text

    def test_no_warning_when_anthropic_sdk_key_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FO_CLAUDE_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-sdk")
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)

        with patch("file_organizer.config.provider_env.logger.warning") as mock_warn:
            _get_claude_configs()

        # No warning — SDK key is present
        warning_text = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "FO_CLAUDE_API_KEY" not in warning_text


# ---------------------------------------------------------------------------
# get_model_configs_from_env — provider dispatch via FO_PROVIDER
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromEnvProviderDispatch:
    def test_llama_cpp_dispatch_via_get_model_configs_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FO_PROVIDER=llama_cpp routes through get_model_configs_from_env correctly."""
        monkeypatch.setenv("FO_PROVIDER", "llama_cpp")
        monkeypatch.setenv("FO_LLAMA_CPP_MODEL_PATH", "/models/test.gguf")
        monkeypatch.delenv("FO_LLAMA_CPP_N_GPU_LAYERS", raising=False)

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "llama_cpp"
        assert vision_cfg.provider == "llama_cpp"
        assert text_cfg.model_path == "/models/test.gguf"

    def test_mlx_dispatch_via_get_model_configs_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FO_PROVIDER=mlx routes through get_model_configs_from_env correctly."""
        monkeypatch.setenv("FO_PROVIDER", "mlx")
        monkeypatch.setenv("FO_MLX_MODEL_PATH", "mlx-community/test-model")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "mlx"
        assert vision_cfg.provider == "mlx"
        assert text_cfg.model_path == "mlx-community/test-model"

    def test_claude_dispatch_via_get_model_configs_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FO_PROVIDER=claude routes through get_model_configs_from_env correctly."""
        monkeypatch.setenv("FO_PROVIDER", "claude")
        monkeypatch.setenv("FO_CLAUDE_API_KEY", "sk-ant-dispatch-test")
        monkeypatch.delenv("FO_CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("FO_CLAUDE_VISION_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "claude"
        assert vision_cfg.provider == "claude"
        assert text_cfg.api_key == "sk-ant-dispatch-test"


# ---------------------------------------------------------------------------
# get_model_configs_from_env — OpenAI warning branch
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromEnvOpenAIWarningBranch:
    def test_warning_when_no_key_no_base_url_no_sdk_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.delenv("FO_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FO_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with patch("file_organizer.config.provider_env.logger.warning") as mock_warn:
            text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "openai"
        assert vision_cfg.provider == "openai"
        warning_text = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "FO_OPENAI_API_KEY" in warning_text or "FO_OPENAI_BASE_URL" in warning_text

    def test_no_warning_when_sdk_key_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.delenv("FO_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FO_OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-sdk-key")

        with patch("file_organizer.config.provider_env.logger.warning") as mock_warn:
            text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "openai"
        # No warning about missing credentials
        warning_text = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "FO_OPENAI_BASE_URL" not in warning_text


# ---------------------------------------------------------------------------
# get_model_configs — priority cascade
# ---------------------------------------------------------------------------


class TestGetModelConfigsPriorityCascade:
    def test_fo_provider_set_uses_env_over_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When FO_PROVIDER is set, env config wins over profile config."""
        monkeypatch.setenv("FO_PROVIDER", "openai")
        monkeypatch.setenv("FO_OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("FO_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("FO_OPENAI_MODEL", raising=False)
        monkeypatch.delenv("FO_OPENAI_VISION_MODEL", raising=False)

        text_cfg, vision_cfg = get_model_configs()

        assert text_cfg.provider == "openai"
        assert vision_cfg.provider == "openai"

    def test_falls_back_to_ollama_when_no_provider_and_default_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No FO_PROVIDER + default profile → Ollama defaults."""
        monkeypatch.delenv("FO_PROVIDER", raising=False)
        monkeypatch.delenv("FO_PROFILE", raising=False)

        with patch(
            "file_organizer.config.provider_env._get_model_configs_from_profile"
        ) as mock_profile:
            # Default profile returns None → fall through to Ollama defaults
            mock_profile.return_value = None
            text_cfg, vision_cfg = get_model_configs()

        assert text_cfg.provider == "ollama"
        assert vision_cfg.provider == "ollama"

    def test_profile_config_used_when_non_default_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When profile returns configs, those are used."""
        from file_organizer.models.base import ModelConfig, ModelType

        monkeypatch.delenv("FO_PROVIDER", raising=False)
        monkeypatch.setenv("FO_PROFILE", "work")

        profile_text = ModelConfig(name="llama3:8b", model_type=ModelType.TEXT)
        profile_vision = ModelConfig(name="llava:13b", model_type=ModelType.VISION)

        with patch(
            "file_organizer.config.provider_env._get_model_configs_from_profile"
        ) as mock_profile:
            mock_profile.return_value = (profile_text, profile_vision)
            text_cfg, vision_cfg = get_model_configs(profile="work")

        assert text_cfg.name == "llama3:8b"
        assert vision_cfg.name == "llava:13b"


# ---------------------------------------------------------------------------
# _get_model_configs_from_profile
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromProfile:
    def test_returns_none_when_profile_uses_default_preset(self) -> None:
        """Profile with default ModelPreset → None (fall through)."""
        from file_organizer.config.schema import AppConfig, ModelPreset

        app_cfg = AppConfig(models=ModelPreset())  # all defaults

        with (
            patch("file_organizer.config.manager.ConfigManager") as mock_cls,
        ):
            mgr = mock_cls.return_value
            mgr.load.return_value = app_cfg
            result = _get_model_configs_from_profile("default")

        assert result is None

    def test_returns_configs_when_profile_has_custom_models(self) -> None:
        """Profile with non-default models → (text_cfg, vision_cfg)."""
        from file_organizer.config.schema import AppConfig, ModelPreset
        from file_organizer.models.base import ModelConfig, ModelType

        custom_preset = ModelPreset(text_model="llama3:8b", vision_model="llava:13b")
        app_cfg = AppConfig(models=custom_preset)

        profile_text = ModelConfig(name="llama3:8b", model_type=ModelType.TEXT)
        profile_vision = ModelConfig(name="llava:13b", model_type=ModelType.VISION)

        with (
            patch("file_organizer.config.manager.ConfigManager") as mock_cls,
        ):
            mgr = mock_cls.return_value
            mgr.load.return_value = app_cfg
            mgr.to_text_model_config.return_value = profile_text
            mgr.to_vision_model_config.return_value = profile_vision
            result = _get_model_configs_from_profile("work")

        assert result is not None
        text_cfg, vision_cfg = result
        assert text_cfg.name == "llama3:8b"
        assert vision_cfg.name == "llava:13b"

    def test_returns_none_on_os_error(self) -> None:
        """OSError during profile load → None (silent fallback)."""
        with (
            patch("file_organizer.config.manager.ConfigManager") as mock_cls,
        ):
            mgr = mock_cls.return_value
            mgr.load.side_effect = OSError("profile dir not found")
            result = _get_model_configs_from_profile("missing-profile")

        assert result is None

    def test_returns_none_on_value_error(self) -> None:
        """ValueError during profile load → None (silent fallback)."""
        with (
            patch("file_organizer.config.manager.ConfigManager") as mock_cls,
        ):
            mgr = mock_cls.return_value
            mgr.load.side_effect = ValueError("bad config value")
            result = _get_model_configs_from_profile("bad-profile")

        assert result is None

    def test_returns_none_on_runtime_error(self) -> None:
        """RuntimeError during profile load → None (silent fallback)."""
        with (
            patch("file_organizer.config.manager.ConfigManager") as mock_cls,
        ):
            mgr = mock_cls.return_value
            mgr.load.side_effect = RuntimeError("unexpected")
            result = _get_model_configs_from_profile("broken-profile")

        assert result is None
