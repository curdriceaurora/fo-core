"""Unit tests for config.provider_env — env var to ModelConfig mapping."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest

from config.provider_env import get_current_provider, get_model_configs_from_env
from models.base import ModelType
from models.text_model import TextModel
from models.vision_model import VisionModel

pytestmark = [pytest.mark.unit, pytest.mark.ci]


# ---------------------------------------------------------------------------
# get_current_provider
# ---------------------------------------------------------------------------


class TestGetCurrentProvider:
    def test_defaults_to_ollama_when_unset(self, provider_env: Callable[..., None]) -> None:
        provider_env()

        assert get_current_provider() == "ollama"

    def test_returns_openai_when_set(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="openai")

        assert get_current_provider() == "openai"

    def test_returns_ollama_when_set_explicitly(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="ollama")

        assert get_current_provider() == "ollama"

    def test_returns_mlx_when_set(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="mlx")

        assert get_current_provider() == "mlx"

    def test_falls_back_to_ollama_on_unknown_value(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="anthropic")

        # Should not raise — returns safe default
        assert get_current_provider() == "ollama"

    def test_strips_whitespace(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="  openai  ")

        assert get_current_provider() == "openai"

    def test_case_insensitive(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="OPENAI")

        assert get_current_provider() == "openai"


# ---------------------------------------------------------------------------
# get_model_configs_from_env — ollama path
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromEnvOllama:
    def test_returns_ollama_defaults_when_provider_unset(
        self, provider_env: Callable[..., None]
    ) -> None:
        provider_env()
        expected_text = TextModel.get_default_config()
        expected_vision = VisionModel.get_default_config()

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.name == expected_text.name
        assert text_cfg.provider == "ollama"
        assert vision_cfg.name == expected_vision.name
        assert vision_cfg.provider == "ollama"

    def test_returned_configs_have_correct_model_types(
        self, provider_env: Callable[..., None]
    ) -> None:
        provider_env()

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION


# ---------------------------------------------------------------------------
# get_model_configs_from_env — openai path
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromEnvOpenAI:
    def test_openai_provider_sets_provider_field(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="openai", FO_OPENAI_API_KEY="sk-abc")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "openai"
        assert vision_cfg.provider == "openai"

    def test_api_key_propagated_to_both_configs(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="openai", FO_OPENAI_API_KEY="sk-secret")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.api_key == "sk-secret"
        assert vision_cfg.api_key == "sk-secret"

    def test_base_url_propagated_to_both_configs(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="openai", FO_OPENAI_BASE_URL="http://localhost:1234/v1")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.api_base_url == "http://localhost:1234/v1"
        assert vision_cfg.api_base_url == "http://localhost:1234/v1"

    def test_custom_text_model_name(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="openai", FO_OPENAI_MODEL="gpt-4o")

        text_cfg, _ = get_model_configs_from_env()

        assert text_cfg.name == "gpt-4o"

    def test_vision_model_falls_back_to_text_model_name(
        self, provider_env: Callable[..., None]
    ) -> None:
        provider_env(FO_PROVIDER="openai", FO_OPENAI_MODEL="gpt-4o")

        _, vision_cfg = get_model_configs_from_env()

        assert vision_cfg.name == "gpt-4o"

    def test_separate_vision_model_name(self, provider_env: Callable[..., None]) -> None:
        provider_env(
            FO_PROVIDER="openai",
            FO_OPENAI_MODEL="gpt-4o-mini",
            FO_OPENAI_VISION_MODEL="gpt-4o",
        )

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.name == "gpt-4o-mini"
        assert vision_cfg.name == "gpt-4o"

    def test_model_types_correct_for_openai_configs(
        self, provider_env: Callable[..., None]
    ) -> None:
        provider_env(FO_PROVIDER="openai", FO_OPENAI_API_KEY="sk-test")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION


# ---------------------------------------------------------------------------
# get_model_configs_from_env — mlx path
# ---------------------------------------------------------------------------


class TestGetModelConfigsFromEnvMLX:
    def test_mlx_provider_sets_provider_field(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="mlx", FO_MLX_MODEL_PATH="mlx-community/Qwen2.5-3B-Instruct-4bit")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "mlx"
        assert vision_cfg.provider == "mlx"

    def test_mlx_model_path_propagates_to_text_and_vision(
        self, provider_env: Callable[..., None]
    ) -> None:
        provider_env(FO_PROVIDER="mlx", FO_MLX_MODEL_PATH="/models/mlx")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.model_path == "/models/mlx"
        assert vision_cfg.model_path == "/models/mlx"

    def test_missing_mlx_model_path_does_not_crash(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="mlx")

        with patch("config.provider_env.logger.warning") as mock_warning:
            text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.provider == "mlx"
        assert vision_cfg.provider == "mlx"
        assert text_cfg.model_path == ""
        assert vision_cfg.model_path == ""
        warning_messages = " ".join(str(call.args[0]) for call in mock_warning.call_args_list)
        assert "FO_MLX_MODEL_PATH" in warning_messages

    def test_model_types_correct_for_mlx_configs(self, provider_env: Callable[..., None]) -> None:
        provider_env(FO_PROVIDER="mlx", FO_MLX_MODEL_PATH="mlx-community/Qwen2.5-3B-Instruct-4bit")

        text_cfg, vision_cfg = get_model_configs_from_env()

        assert text_cfg.model_type == ModelType.TEXT
        assert vision_cfg.model_type == ModelType.VISION
