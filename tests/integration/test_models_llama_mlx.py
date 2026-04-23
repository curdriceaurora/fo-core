"""Integration tests for LlamaCpp and MLX text model implementations.

Covers:
- _llama_cpp_helpers.py: is_llama_cpp_token_exhausted(), extract_llama_cpp_text()
- llama_cpp_text_model.py: LlamaCppTextModel init/initialize/generate/cleanup/_device_to_gpu_layers
- mlx_text_model.py: MLXTextModel init/initialize/generate/cleanup/_call_generate/_is_signature_mismatch
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# _llama_cpp_helpers.py
# ---------------------------------------------------------------------------


class TestLlamaCppHelpers:
    def test_extract_llama_cpp_text_returns_stripped_text(self) -> None:
        from models._llama_cpp_helpers import extract_llama_cpp_text

        response = {"choices": [{"text": "  hello llama  ", "finish_reason": "stop"}]}
        assert extract_llama_cpp_text(response) == "hello llama"

    def test_extract_llama_cpp_text_empty_choices_returns_empty(self) -> None:
        from models._llama_cpp_helpers import extract_llama_cpp_text

        assert extract_llama_cpp_text({"choices": []}) == ""

    def test_extract_llama_cpp_text_no_choices_key_returns_empty(self) -> None:
        from models._llama_cpp_helpers import extract_llama_cpp_text

        assert extract_llama_cpp_text({}) == ""

    def test_extract_llama_cpp_text_none_text_returns_empty(self) -> None:
        from models._llama_cpp_helpers import extract_llama_cpp_text

        response = {"choices": [{"text": None, "finish_reason": "stop"}]}
        assert extract_llama_cpp_text(response) == ""

    def test_is_llama_cpp_token_exhausted_false_on_stop(self) -> None:
        from models._llama_cpp_helpers import is_llama_cpp_token_exhausted

        response = {
            "choices": [
                {"text": "A full answer here with plenty of words.", "finish_reason": "stop"}
            ]
        }
        assert is_llama_cpp_token_exhausted(response) is False

    def test_is_llama_cpp_token_exhausted_true_when_length_and_short(self) -> None:
        from models._llama_cpp_helpers import is_llama_cpp_token_exhausted

        response = {"choices": [{"text": "", "finish_reason": "length"}]}
        assert is_llama_cpp_token_exhausted(response) is True

    def test_is_llama_cpp_token_exhausted_false_when_length_but_long_text(self) -> None:
        from models._llama_cpp_helpers import is_llama_cpp_token_exhausted

        response = {
            "choices": [
                {
                    "text": "This is a long enough answer to pass the check.",
                    "finish_reason": "length",
                }
            ]
        }
        assert is_llama_cpp_token_exhausted(response) is False

    def test_is_llama_cpp_token_exhausted_false_on_empty_choices(self) -> None:
        from models._llama_cpp_helpers import is_llama_cpp_token_exhausted

        assert is_llama_cpp_token_exhausted({"choices": []}) is False

    def test_is_llama_cpp_token_exhausted_false_when_no_choices_key(self) -> None:
        from models._llama_cpp_helpers import is_llama_cpp_token_exhausted

        assert is_llama_cpp_token_exhausted({}) is False


# ---------------------------------------------------------------------------
# Helpers for LlamaCppTextModel
# ---------------------------------------------------------------------------


def _make_llama_model(model_path: str = "/models/test.gguf") -> Any:
    """Return an initialised LlamaCppTextModel with a mock Llama client."""
    pytest.importorskip("llama_cpp")
    from models.llama_cpp_text_model import LlamaCppTextModel

    config = LlamaCppTextModel.get_default_config(model_path=model_path)
    with patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True):
        model = LlamaCppTextModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


def _llama_ok_response(text: str = "Generated text") -> dict:
    return {"choices": [{"text": text, "finish_reason": "stop"}]}


def _llama_exhausted_response(text: str = "") -> dict:
    return {"choices": [{"text": text, "finish_reason": "length"}]}


# ---------------------------------------------------------------------------
# llama_cpp_text_model.py — LlamaCppTextModel
# ---------------------------------------------------------------------------


class TestLlamaCppTextModel:
    @pytest.fixture(autouse=True)
    def _require_llama_cpp(self) -> None:
        pytest.importorskip("llama_cpp")

    def test_init_raises_import_error_when_unavailable(self, tmp_path: Path) -> None:
        from models.base import ModelConfig, ModelType
        from models.llama_cpp_text_model import LlamaCppTextModel

        config = ModelConfig(
            name="llama-cpp",
            model_type=ModelType.TEXT,
            provider="llama_cpp",
            model_path=str(tmp_path / "test.gguf"),
        )
        with (
            patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", False),
            pytest.raises(ImportError, match="llama-cpp-python"),
        ):
            LlamaCppTextModel(config)

    def test_init_raises_value_error_for_wrong_model_type(self, tmp_path: Path) -> None:
        from models.base import ModelConfig, ModelType
        from models.llama_cpp_text_model import LlamaCppTextModel

        config = ModelConfig(
            name="llama-cpp",
            model_type=ModelType.VISION,
            provider="llama_cpp",
            model_path=str(tmp_path / "test.gguf"),
        )
        with (
            patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True),
            pytest.raises(ValueError, match="TEXT"),
        ):
            LlamaCppTextModel(config)

    def test_init_raises_value_error_for_empty_model_path(self) -> None:
        from models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config(model_path="")
        with (
            patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True),
            pytest.raises(ValueError, match="model_path"),
        ):
            LlamaCppTextModel(config)

    def test_initialize_creates_llama_client(self) -> None:
        from models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/models/mymodel.gguf")
        mock_llama = MagicMock()
        with (
            patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True),
            patch("models.llama_cpp_text_model.Llama", return_value=mock_llama),
        ):
            model = LlamaCppTextModel(config)
            model.initialize()

        assert model.client is mock_llama
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_llama_model()
        original_client = model.client
        model.initialize()
        assert model.client is original_client

    def test_initialize_wraps_os_error_in_runtime_error(self) -> None:
        from models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/models/bad.gguf")
        with (
            patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True),
            patch(
                "models.llama_cpp_text_model.Llama",
                side_effect=OSError("file not found"),
            ),
        ):
            model = LlamaCppTextModel(config)
            with pytest.raises(RuntimeError, match="Could not load GGUF model"):
                model.initialize()

    def test_generate_returns_stripped_text(self) -> None:
        model = _make_llama_model()
        model.client.return_value = _llama_ok_response("  llama output  ")
        result = model.generate("Test prompt")
        assert result == "llama output"

    def test_generate_passes_temperature_and_max_tokens(self) -> None:
        model = _make_llama_model()
        model.client.return_value = _llama_ok_response("ok")
        model.generate("prompt", temperature=0.3, max_tokens=128)
        call_kwargs = model.client.call_args[1]
        assert call_kwargs["temperature"] == pytest.approx(0.3)
        assert call_kwargs["max_tokens"] == 128

    def test_generate_passes_top_k_and_top_p(self) -> None:
        model = _make_llama_model()
        model.client.return_value = _llama_ok_response("ok")
        model.generate("prompt", top_k=5, top_p=0.9)
        call_kwargs = model.client.call_args[1]
        assert call_kwargs["top_k"] == 5
        assert call_kwargs["top_p"] == pytest.approx(0.9)

    def test_generate_token_exhaustion_retries_once(self) -> None:
        model = _make_llama_model()
        model.client.side_effect = [
            _llama_exhausted_response(),
            _llama_ok_response("retry succeeded"),
        ]
        result = model.generate("prompt")
        assert result == "retry succeeded"
        assert model.client.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self) -> None:
        from models.base import TokenExhaustionError

        model = _make_llama_model()
        model.client.return_value = _llama_exhausted_response()
        with pytest.raises(TokenExhaustionError):
            model.generate("prompt")
        assert model.client.call_count == 2

    def test_generate_not_initialized_raises_runtime_error(self) -> None:
        model = _make_llama_model()
        model.client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("prompt")

    def test_generate_propagates_runtime_error(self) -> None:
        model = _make_llama_model()
        model.client.side_effect = RuntimeError("inference error")
        with pytest.raises(RuntimeError, match="inference error"):
            model.generate("prompt")

    def test_cleanup_sets_client_none(self) -> None:
        model = _make_llama_model()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_cleanup_handles_close_error(self) -> None:
        model = _make_llama_model()
        model.client.close.side_effect = OSError("close err")
        model.cleanup()  # must not raise
        assert model.client is None

    def test_device_to_gpu_layers_cpu_returns_zero(self, tmp_path: Path) -> None:
        from models.base import DeviceType
        from models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config(str(tmp_path / "m.gguf"))
        config.device = DeviceType.CPU
        with patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True):
            model = LlamaCppTextModel(config)
        assert model._device_to_gpu_layers() == 0

    def test_device_to_gpu_layers_cuda_returns_minus_one(self, tmp_path: Path) -> None:
        from models.base import DeviceType
        from models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config(str(tmp_path / "m.gguf"))
        config.device = DeviceType.CUDA
        with patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True):
            model = LlamaCppTextModel(config)
        assert model._device_to_gpu_layers() == -1

    def test_device_to_gpu_layers_mps_returns_minus_one(self, tmp_path: Path) -> None:
        from models.base import DeviceType
        from models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config(str(tmp_path / "m.gguf"))
        config.device = DeviceType.MPS
        with patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True):
            model = LlamaCppTextModel(config)
        assert model._device_to_gpu_layers() == -1

    def test_device_to_gpu_layers_extra_params_override(self, tmp_path: Path) -> None:
        from models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config(str(tmp_path / "m.gguf"))
        config.extra_params = {"n_gpu_layers": 32}
        with patch("models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True):
            model = LlamaCppTextModel(config)
        assert model._device_to_gpu_layers() == 32

    def test_get_default_config_returns_text_type(self, tmp_path: Path) -> None:
        from models.base import ModelType
        from models.llama_cpp_text_model import LlamaCppTextModel

        model_path = str(tmp_path / "model.gguf")
        config = LlamaCppTextModel.get_default_config(model_path)
        assert config.model_type == ModelType.TEXT
        assert config.provider == "llama_cpp"
        assert config.model_path == model_path


# ---------------------------------------------------------------------------
# Helpers for MLXTextModel
# ---------------------------------------------------------------------------


def _make_mlx_model(model_path: str = "mlx-community/Llama-3.2-1B-Instruct") -> Any:
    """Return an initialised MLXTextModel with mock model/tokenizer."""
    pytest.importorskip("mlx_lm")
    from models.mlx_text_model import MLXTextModel

    config = MLXTextModel.get_default_config(model_path=model_path)
    with patch("models.mlx_text_model.MLX_LM_AVAILABLE", True):
        model = MLXTextModel(config)
    model._initialized = True
    model._model = MagicMock()
    model._tokenizer = MagicMock()
    return model


# ---------------------------------------------------------------------------
# mlx_text_model.py — MLXTextModel
# ---------------------------------------------------------------------------


class TestMLXTextModel:
    @pytest.fixture(autouse=True)
    def _require_mlx_lm(self) -> None:
        pytest.importorskip("mlx_lm")

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.mlx_text_model import MLXTextModel

        config = ModelConfig(
            name="mlx-lm",
            model_type=ModelType.TEXT,
            provider="mlx",
            model_path="some/path",
        )
        with (
            patch("models.mlx_text_model.MLX_LM_AVAILABLE", False),
            pytest.raises(ImportError, match="mlx-lm"),
        ):
            MLXTextModel(config)

    def test_init_raises_value_error_for_wrong_model_type(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.mlx_text_model import MLXTextModel

        config = ModelConfig(
            name="mlx-lm",
            model_type=ModelType.VISION,
            provider="mlx",
            model_path="some/path",
        )
        with (
            patch("models.mlx_text_model.MLX_LM_AVAILABLE", True),
            pytest.raises(ValueError, match="TEXT"),
        ):
            MLXTextModel(config)

    def test_init_raises_value_error_for_empty_model_path(self) -> None:
        from models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config(model_path="")
        with (
            patch("models.mlx_text_model.MLX_LM_AVAILABLE", True),
            pytest.raises(ValueError, match="model_path"),
        ):
            MLXTextModel(config)

    def test_initialize_loads_model_and_tokenizer(self) -> None:
        from models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("my/model")
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        with (
            patch("models.mlx_text_model.MLX_LM_AVAILABLE", True),
            patch(
                "models.mlx_text_model.mlx_load",
                return_value=(mock_model, mock_tokenizer),
            ),
        ):
            model = MLXTextModel(config)
            model.initialize()

        assert model._model is mock_model
        assert model._tokenizer is mock_tokenizer
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_mlx_model()
        original_model_obj = model._model
        model.initialize()  # already initialized — should be a no-op
        assert model._model is original_model_obj

    def test_initialize_wraps_runtime_error(self) -> None:
        from models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("bad/path")
        with (
            patch("models.mlx_text_model.MLX_LM_AVAILABLE", True),
            patch(
                "models.mlx_text_model.mlx_load",
                side_effect=OSError("cannot load"),
            ),
        ):
            model = MLXTextModel(config)
            with pytest.raises(RuntimeError, match="Could not load MLX model"):
                model.initialize()

    def test_initialize_raises_if_load_returns_wrong_shape(self) -> None:
        from models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("some/model")
        with (
            patch("models.mlx_text_model.MLX_LM_AVAILABLE", True),
            patch(
                "models.mlx_text_model.mlx_load",
                return_value="not_a_tuple",
            ),
        ):
            model = MLXTextModel(config)
            with pytest.raises(RuntimeError, match="unexpected value"):
                model.initialize()

    def test_generate_returns_stripped_text(self) -> None:
        model = _make_mlx_model()
        with patch(
            "models.mlx_text_model.mlx_generate",
            return_value="  mlx result  ",
        ):
            result = model.generate("Hello MLX")
        assert result == "mlx result"

    def test_generate_passes_temperature_and_max_tokens(self) -> None:
        model = _make_mlx_model()
        captured: list[dict] = []

        def _fake_generate(m, t, p, **kwargs: Any) -> str:
            captured.append(kwargs)
            return "ok"

        with patch("models.mlx_text_model.mlx_generate", _fake_generate):
            model.generate("prompt", temperature=0.7, max_tokens=64)

        assert len(captured) == 1
        assert captured[0]["max_tokens"] == 64

    def test_generate_not_initialized_raises_runtime_error(self) -> None:
        model = _make_mlx_model()
        model._model = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("prompt")

    def test_generate_propagates_runtime_error(self) -> None:
        model = _make_mlx_model()
        with (
            patch(
                "models.mlx_text_model.mlx_generate",
                side_effect=RuntimeError("MLX error"),
            ),
            pytest.raises(RuntimeError, match="MLX error"),
        ):
            model.generate("prompt")

    def test_cleanup_clears_model_and_tokenizer(self) -> None:
        model = _make_mlx_model()
        model.cleanup()
        assert model._model is None
        assert model._tokenizer is None
        assert model._initialized is False

    def test_is_signature_mismatch_type_error_detects_unexpected_keyword(self) -> None:
        from models.mlx_text_model import MLXTextModel

        exc = TypeError("got an unexpected keyword argument 'temp'")
        assert MLXTextModel._is_signature_mismatch_type_error(exc) is True

    def test_is_signature_mismatch_type_error_false_for_other_errors(self) -> None:
        from models.mlx_text_model import MLXTextModel

        exc = TypeError("'NoneType' object is not callable")
        assert MLXTextModel._is_signature_mismatch_type_error(exc) is False

    def test_is_signature_mismatch_type_error_detects_required_positional(self) -> None:
        from models.mlx_text_model import MLXTextModel

        exc = TypeError("missing 1 required positional argument: 'x'")
        assert MLXTextModel._is_signature_mismatch_type_error(exc) is True

    def test_call_generate_caches_working_variant(self) -> None:
        model = _make_mlx_model()
        call_count = 0

        def _fake_generate(m, t, p, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            if "temp" in kwargs:
                return "result"
            raise TypeError("unexpected keyword argument 'temp'")

        with patch("models.mlx_text_model.mlx_generate", _fake_generate):
            # First call probes variants
            result1 = model._call_generate(
                prompt="test", max_tokens=100, temperature=0.5, top_p=0.9, top_k=5
            )
            # Second call uses cached variant (fewer probes)
            first_call_count = call_count
            model._call_generate(prompt="test", max_tokens=100, temperature=0.5, top_p=0.9, top_k=5)
            second_call_count = call_count - first_call_count

        assert result1 == "result"
        # Second call should use cached variant → only 1 invocation
        assert second_call_count == 1

    def test_get_default_config_returns_text_type(self) -> None:
        from models.base import ModelType
        from models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("some/model")
        assert config.model_type == ModelType.TEXT
        assert config.provider == "mlx"

    def test_get_default_config_stores_model_path(self) -> None:
        from models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("mlx-community/Llama-3.2-3B-Instruct-4bit")
        assert config.model_path == "mlx-community/Llama-3.2-3B-Instruct-4bit"
