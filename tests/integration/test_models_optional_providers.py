"""Integration tests for optional-provider model modules.

Covers (all mock-based — no real SDK calls required):
- _llama_cpp_helpers.py      (helpers: is_llama_cpp_token_exhausted, extract_llama_cpp_text)
- llama_cpp_text_model.py    (LlamaCppTextModel init/initialize/generate/cleanup/device_layers)
- mlx_text_model.py          (MLXTextModel init/initialize/generate/cleanup/_call_generate)
- claude_vision_model.py     (ClaudeVisionModel init/initialize/generate/analyze_image/cleanup)
- claude_text_model.py       (ClaudeTextModel init/initialize/generate/cleanup/default_config)
- openai_text_model.py       (OpenAITextModel init/initialize/generate/cleanup/default_config)
- openai_vision_model.py     (OpenAIVisionModel init/initialize/generate/analyze_image/cleanup)
- _claude_response.py        (is_claude_token_exhausted, extract_claude_text edge cases)
- _claude_client.py          (create_claude_client: api_key, base_url, error paths)
- _openai_client.py          (create_openai_client: api_key, base_url, error paths)

All tests patch optional SDK modules so no real packages need to be installed.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Shared mock-response factories
# ---------------------------------------------------------------------------


def _claude_ok(text: str = "Claude response") -> MagicMock:
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _claude_exhausted(text: str = "") -> MagicMock:
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "max_tokens"
    resp.content = [block]
    return resp


def _openai_ok(text: str = "OpenAI response") -> MagicMock:
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _openai_exhausted(text: str = "") -> MagicMock:
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "length"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _llama_ok(text: str = "Llama response") -> dict:
    return {"choices": [{"text": text, "finish_reason": "stop"}]}


def _llama_exhausted(text: str = "") -> dict:
    return {"choices": [{"text": text, "finish_reason": "length"}]}


# ---------------------------------------------------------------------------
# Stub SDK modules so imports inside modules under test don't fail
# ---------------------------------------------------------------------------


class _FakeAPIError(Exception):
    """Stub for openai.APIError / anthropic.APIError used in except clauses."""


_ANTHROPIC_MOD = MagicMock()
_ANTHROPIC_MOD.APIError = _FakeAPIError
_ANTHROPIC_MOD.APIStatusError = _FakeAPIError
_ANTHROPIC_MOD.APIConnectionError = _FakeAPIError

_OPENAI_MOD = MagicMock()
_OPENAI_MOD.APIError = _FakeAPIError

_LLAMA_CPP_MOD = MagicMock()
_MLX_LM_MOD = MagicMock()


def _inject_stub_modules() -> None:
    """Inject lightweight stub modules into sys.modules for optional SDKs."""
    if "anthropic" not in sys.modules:
        sys.modules["anthropic"] = _ANTHROPIC_MOD
    if "openai" not in sys.modules:
        sys.modules["openai"] = _OPENAI_MOD
    if "llama_cpp" not in sys.modules:
        sys.modules["llama_cpp"] = _LLAMA_CPP_MOD
    if "mlx_lm" not in sys.modules:
        sys.modules["mlx_lm"] = _MLX_LM_MOD


_inject_stub_modules()


# ===========================================================================
# _llama_cpp_helpers.py
# ===========================================================================


class TestLlamaCppHelpersExtended:
    """Extended tests for _llama_cpp_helpers.py edge cases."""

    def test_extract_text_with_whitespace_only_returns_empty(self) -> None:
        from file_organizer.models._llama_cpp_helpers import extract_llama_cpp_text

        resp = {"choices": [{"text": "   \n\t  ", "finish_reason": "stop"}]}
        assert extract_llama_cpp_text(resp) == ""

    def test_extract_text_none_choices_value_returns_empty(self) -> None:
        from file_organizer.models._llama_cpp_helpers import extract_llama_cpp_text

        assert extract_llama_cpp_text({"choices": None}) == ""

    def test_extract_text_multi_choice_uses_first(self) -> None:
        from file_organizer.models._llama_cpp_helpers import extract_llama_cpp_text

        resp = {
            "choices": [
                {"text": "first", "finish_reason": "stop"},
                {"text": "second", "finish_reason": "stop"},
            ]
        }
        assert extract_llama_cpp_text(resp) == "first"

    def test_is_exhausted_none_finish_reason_returns_false(self) -> None:
        from file_organizer.models._llama_cpp_helpers import is_llama_cpp_token_exhausted

        resp = {"choices": [{"text": "", "finish_reason": None}]}
        assert is_llama_cpp_token_exhausted(resp) is False

    def test_is_exhausted_length_with_exact_min_length_returns_false(self) -> None:
        from file_organizer.models._llama_cpp_helpers import is_llama_cpp_token_exhausted
        from file_organizer.models.base import MIN_USEFUL_RESPONSE_LENGTH

        text = "x" * MIN_USEFUL_RESPONSE_LENGTH
        resp = {"choices": [{"text": text, "finish_reason": "length"}]}
        # Exactly at the boundary: len == min_length → NOT exhausted (< not <=)
        assert is_llama_cpp_token_exhausted(resp) is False

    def test_is_exhausted_length_one_below_min_returns_true(self) -> None:
        from file_organizer.models._llama_cpp_helpers import is_llama_cpp_token_exhausted
        from file_organizer.models.base import MIN_USEFUL_RESPONSE_LENGTH

        text = "x" * (MIN_USEFUL_RESPONSE_LENGTH - 1)
        resp = {"choices": [{"text": text, "finish_reason": "length"}]}
        assert is_llama_cpp_token_exhausted(resp) is True

    def test_is_exhausted_stop_with_short_text_returns_false(self) -> None:
        from file_organizer.models._llama_cpp_helpers import is_llama_cpp_token_exhausted

        resp = {"choices": [{"text": "", "finish_reason": "stop"}]}
        assert is_llama_cpp_token_exhausted(resp) is False

    def test_is_exhausted_custom_min_length(self) -> None:
        from file_organizer.models._llama_cpp_helpers import is_llama_cpp_token_exhausted

        resp = {"choices": [{"text": "short", "finish_reason": "length"}]}
        # With min_length=3, "short" (5 chars) is long enough → False
        assert is_llama_cpp_token_exhausted(resp, min_length=3) is False
        # With min_length=100 → True
        assert is_llama_cpp_token_exhausted(resp, min_length=100) is True


# ===========================================================================
# llama_cpp_text_model.py  (mock-based, no llama_cpp package needed)
# ===========================================================================


def _make_llama_model_mocked(model_path: str = "/models/test.gguf") -> Any:
    """Return LlamaCppTextModel with patched availability and a mock client."""
    from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

    config = LlamaCppTextModel.get_default_config(model_path=model_path)
    with patch("file_organizer.models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True):
        model = LlamaCppTextModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


class TestLlamaCppTextModelMocked:
    """Full mock-based tests — no llama_cpp package required."""

    @pytest.fixture(autouse=True)
    def _patch_availability(self) -> None:
        patcher = patch("file_organizer.models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", True)
        patcher.start()
        self._patcher = patcher
        yield
        patcher.stop()

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = ModelConfig(
            name="llama-cpp",
            model_type=ModelType.TEXT,
            provider="llama_cpp",
            model_path="/tmp/test.gguf",
        )
        with (
            patch("file_organizer.models.llama_cpp_text_model.LLAMA_CPP_AVAILABLE", False),
            pytest.raises(ImportError, match="llama-cpp-python"),
        ):
            LlamaCppTextModel(config)

    def test_init_raises_value_error_for_wrong_model_type(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = ModelConfig(
            name="llama-cpp",
            model_type=ModelType.VISION,
            provider="llama_cpp",
            model_path="/tmp/test.gguf",
        )
        with pytest.raises(ValueError, match="TEXT"):
            LlamaCppTextModel(config)

    def test_init_raises_value_error_for_empty_model_path(self) -> None:
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config(model_path="")
        with pytest.raises(ValueError, match="model_path"):
            LlamaCppTextModel(config)

    def test_initialize_creates_llama_client(self) -> None:
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/models/mymodel.gguf")
        mock_llama_instance = MagicMock()
        with patch(
            "file_organizer.models.llama_cpp_text_model.Llama", return_value=mock_llama_instance
        ):
            model = LlamaCppTextModel(config)
            model.initialize()
        assert model.client is mock_llama_instance
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_llama_model_mocked()
        original_client = model.client
        model.initialize()
        assert model.client is original_client

    def test_initialize_wraps_os_error_in_runtime_error(self) -> None:
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/models/bad.gguf")
        with (
            patch(
                "file_organizer.models.llama_cpp_text_model.Llama",
                side_effect=OSError("file not found"),
            ),
            pytest.raises(RuntimeError, match="Could not load GGUF model"),
        ):
            model = LlamaCppTextModel(config)
            model.initialize()

    def test_initialize_wraps_value_error(self) -> None:
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/models/bad.gguf")
        with (
            patch(
                "file_organizer.models.llama_cpp_text_model.Llama",
                side_effect=ValueError("invalid params"),
            ),
            pytest.raises(RuntimeError, match="Could not load GGUF model"),
        ):
            model = LlamaCppTextModel(config)
            model.initialize()

    def test_generate_returns_stripped_text(self) -> None:
        model = _make_llama_model_mocked()
        model.client.return_value = _llama_ok("  llama output  ")
        result = model.generate("Test prompt")
        assert result == "llama output"

    def test_generate_passes_temperature_and_max_tokens(self) -> None:
        model = _make_llama_model_mocked()
        model.client.return_value = _llama_ok("ok")
        model.generate("prompt", temperature=0.3, max_tokens=128)
        call_kwargs = model.client.call_args[1]
        assert call_kwargs["temperature"] == pytest.approx(0.3)
        assert call_kwargs["max_tokens"] == 128

    def test_generate_passes_top_k_and_top_p(self) -> None:
        model = _make_llama_model_mocked()
        model.client.return_value = _llama_ok("ok")
        model.generate("prompt", top_k=5, top_p=0.9)
        call_kwargs = model.client.call_args[1]
        assert call_kwargs["top_k"] == 5
        assert call_kwargs["top_p"] == pytest.approx(0.9)

    def test_generate_token_exhaustion_retries_once(self) -> None:
        model = _make_llama_model_mocked()
        model.client.side_effect = [
            _llama_exhausted(),
            _llama_ok("retry succeeded"),
        ]
        result = model.generate("prompt")
        assert result == "retry succeeded"
        assert model.client.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self) -> None:
        from file_organizer.models.base import TokenExhaustionError

        model = _make_llama_model_mocked()
        model.client.return_value = _llama_exhausted()
        with pytest.raises(TokenExhaustionError):
            model.generate("prompt")
        assert model.client.call_count == 2

    def test_generate_not_initialized_raises_runtime_error(self) -> None:
        model = _make_llama_model_mocked()
        model.client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("prompt")

    def test_generate_propagates_os_error(self) -> None:
        model = _make_llama_model_mocked()
        model.client.side_effect = OSError("disk read error")
        with pytest.raises(OSError, match="disk read error"):
            model.generate("prompt")

    def test_generate_propagates_runtime_error(self) -> None:
        model = _make_llama_model_mocked()
        model.client.side_effect = RuntimeError("inference error")
        with pytest.raises(RuntimeError, match="inference error"):
            model.generate("prompt")

    def test_cleanup_sets_client_none(self) -> None:
        model = _make_llama_model_mocked()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_cleanup_handles_close_os_error(self) -> None:
        model = _make_llama_model_mocked()
        model.client.close.side_effect = OSError("close error")
        model.cleanup()  # must not raise
        assert model.client is None

    def test_cleanup_handles_close_runtime_error(self) -> None:
        model = _make_llama_model_mocked()
        model.client.close.side_effect = RuntimeError("close runtime error")
        model.cleanup()  # must not raise
        assert model.client is None

    def test_cleanup_when_client_is_none(self) -> None:
        model = _make_llama_model_mocked()
        model.client = None
        model.cleanup()  # must not raise
        assert model.client is None

    def test_device_to_gpu_layers_cpu_returns_zero(self) -> None:
        from file_organizer.models.base import DeviceType
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/tmp/m.gguf")
        config.device = DeviceType.CPU
        model = LlamaCppTextModel(config)
        assert model._device_to_gpu_layers() == 0

    def test_device_to_gpu_layers_cuda_returns_minus_one(self) -> None:
        from file_organizer.models.base import DeviceType
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/tmp/m.gguf")
        config.device = DeviceType.CUDA
        model = LlamaCppTextModel(config)
        assert model._device_to_gpu_layers() == -1

    def test_device_to_gpu_layers_mps_returns_minus_one(self) -> None:
        from file_organizer.models.base import DeviceType
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/tmp/m.gguf")
        config.device = DeviceType.MPS
        model = LlamaCppTextModel(config)
        assert model._device_to_gpu_layers() == -1

    def test_device_to_gpu_layers_metal_returns_minus_one(self) -> None:
        from file_organizer.models.base import DeviceType
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/tmp/m.gguf")
        config.device = DeviceType.METAL
        model = LlamaCppTextModel(config)
        assert model._device_to_gpu_layers() == -1

    def test_device_to_gpu_layers_extra_params_override(self) -> None:
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/tmp/m.gguf")
        config.extra_params = {"n_gpu_layers": 32}
        model = LlamaCppTextModel(config)
        assert model._device_to_gpu_layers() == 32

    def test_get_default_config_returns_text_type(self) -> None:
        from file_organizer.models.base import ModelType
        from file_organizer.models.llama_cpp_text_model import LlamaCppTextModel

        config = LlamaCppTextModel.get_default_config("/tmp/model.gguf")
        assert config.model_type == ModelType.TEXT
        assert config.provider == "llama_cpp"
        assert config.model_path == "/tmp/model.gguf"


# ===========================================================================
# mlx_text_model.py  (mock-based, no mlx_lm package needed)
# ===========================================================================


def _make_mlx_model_mocked(model_path: str = "mlx-community/test-model") -> Any:
    """Return MLXTextModel with patched availability and mock model/tokenizer."""
    from file_organizer.models.mlx_text_model import MLXTextModel

    config = MLXTextModel.get_default_config(model_path=model_path)
    with patch("file_organizer.models.mlx_text_model.MLX_LM_AVAILABLE", True):
        model = MLXTextModel(config)
    model._initialized = True
    model._model = MagicMock()
    model._tokenizer = MagicMock()
    return model


class TestMLXTextModelMocked:
    """Full mock-based tests — no mlx_lm package required."""

    @pytest.fixture(autouse=True)
    def _patch_availability(self) -> None:
        patcher = patch("file_organizer.models.mlx_text_model.MLX_LM_AVAILABLE", True)
        patcher.start()
        self._patcher = patcher
        yield
        patcher.stop()

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.mlx_text_model import MLXTextModel

        config = ModelConfig(
            name="mlx-lm",
            model_type=ModelType.TEXT,
            provider="mlx",
            model_path="some/path",
        )
        with (
            patch("file_organizer.models.mlx_text_model.MLX_LM_AVAILABLE", False),
            pytest.raises(ImportError, match="mlx-lm"),
        ):
            MLXTextModel(config)

    def test_init_raises_value_error_for_wrong_model_type(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.mlx_text_model import MLXTextModel

        config = ModelConfig(
            name="mlx-lm",
            model_type=ModelType.VISION,
            provider="mlx",
            model_path="some/path",
        )
        with pytest.raises(ValueError, match="TEXT"):
            MLXTextModel(config)

    def test_init_raises_value_error_for_empty_model_path(self) -> None:
        from file_organizer.models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config(model_path="")
        with pytest.raises(ValueError, match="model_path"):
            MLXTextModel(config)

    def test_initialize_loads_model_and_tokenizer(self) -> None:
        from file_organizer.models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("my/model")
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        with (
            patch(
                "file_organizer.models.mlx_text_model.mlx_load",
                return_value=(mock_model, mock_tokenizer),
            ),
        ):
            model = MLXTextModel(config)
            model.initialize()
        assert model._model is mock_model
        assert model._tokenizer is mock_tokenizer
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_mlx_model_mocked()
        original = model._model
        model.initialize()
        assert model._model is original

    def test_initialize_wraps_os_error(self) -> None:
        from file_organizer.models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("bad/path")
        with (
            patch(
                "file_organizer.models.mlx_text_model.mlx_load",
                side_effect=OSError("cannot load"),
            ),
            pytest.raises(RuntimeError, match="Could not load MLX model"),
        ):
            model = MLXTextModel(config)
            model.initialize()

    def test_initialize_raises_if_load_returns_non_tuple(self) -> None:
        from file_organizer.models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("some/model")
        with (
            patch(
                "file_organizer.models.mlx_text_model.mlx_load",
                return_value="not_a_tuple",
            ),
            pytest.raises(RuntimeError, match="unexpected value"),
        ):
            model = MLXTextModel(config)
            model.initialize()

    def test_initialize_raises_if_load_returns_single_element_tuple(self) -> None:
        from file_organizer.models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("some/model")
        with (
            patch(
                "file_organizer.models.mlx_text_model.mlx_load",
                return_value=(MagicMock(),),
            ),
            pytest.raises(RuntimeError, match="unexpected value"),
        ):
            model = MLXTextModel(config)
            model.initialize()

    def test_generate_returns_stripped_text(self) -> None:
        model = _make_mlx_model_mocked()
        with patch(
            "file_organizer.models.mlx_text_model.mlx_generate",
            return_value="  mlx result  ",
        ):
            result = model.generate("Hello MLX")
        assert result == "mlx result"

    def test_generate_passes_max_tokens(self) -> None:
        model = _make_mlx_model_mocked()
        captured: list[dict] = []

        def _fake_generate(m: Any, t: Any, p: Any, **kwargs: Any) -> str:
            captured.append(kwargs)
            return "ok"

        with patch("file_organizer.models.mlx_text_model.mlx_generate", _fake_generate):
            model.generate("prompt", temperature=0.7, max_tokens=64)

        assert len(captured) == 1
        assert captured[0]["max_tokens"] == 64

    def test_generate_not_initialized_raises_runtime_error(self) -> None:
        model = _make_mlx_model_mocked()
        model._model = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("prompt")

    def test_generate_not_initialized_tokenizer_none_raises(self) -> None:
        model = _make_mlx_model_mocked()
        model._tokenizer = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("prompt")

    def test_generate_propagates_runtime_error(self) -> None:
        model = _make_mlx_model_mocked()
        with (
            patch(
                "file_organizer.models.mlx_text_model.mlx_generate",
                side_effect=RuntimeError("MLX error"),
            ),
            pytest.raises(RuntimeError, match="MLX error"),
        ):
            model.generate("prompt")

    def test_cleanup_clears_model_and_tokenizer(self) -> None:
        model = _make_mlx_model_mocked()
        model.cleanup()
        assert model._model is None
        assert model._tokenizer is None
        assert model._initialized is False

    def test_is_signature_mismatch_detects_unexpected_keyword(self) -> None:
        from file_organizer.models.mlx_text_model import MLXTextModel

        exc = TypeError("got an unexpected keyword argument 'temp'")
        assert MLXTextModel._is_signature_mismatch_type_error(exc) is True

    def test_is_signature_mismatch_false_for_non_signature_error(self) -> None:
        from file_organizer.models.mlx_text_model import MLXTextModel

        exc = TypeError("'NoneType' object is not callable")
        assert MLXTextModel._is_signature_mismatch_type_error(exc) is False

    def test_is_signature_mismatch_detects_required_positional(self) -> None:
        from file_organizer.models.mlx_text_model import MLXTextModel

        exc = TypeError("missing 1 required positional argument: 'x'")
        assert MLXTextModel._is_signature_mismatch_type_error(exc) is True

    def test_is_signature_mismatch_detects_takes_no_keyword_arguments(self) -> None:
        from file_organizer.models.mlx_text_model import MLXTextModel

        exc = TypeError("takes no keyword arguments")
        assert MLXTextModel._is_signature_mismatch_type_error(exc) is True

    def test_call_generate_caches_working_variant(self) -> None:
        model = _make_mlx_model_mocked()
        call_count = 0

        def _fake_gen(m: Any, t: Any, p: Any, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            if "temp" in kwargs:
                return "result"
            raise TypeError("got an unexpected keyword argument 'temp'")

        with patch("file_organizer.models.mlx_text_model.mlx_generate", _fake_gen):
            result1 = model._call_generate(
                prompt="test", max_tokens=100, temperature=0.5, top_p=0.9, top_k=5
            )
            first_count = call_count
            model._call_generate(prompt="test", max_tokens=100, temperature=0.5, top_p=0.9, top_k=5)
            second_count = call_count - first_count

        assert result1 == "result"
        # Second call hits cached variant directly → exactly 1 invocation
        assert second_count == 1

    def test_call_generate_falls_through_all_variants_raises_last_error(self) -> None:
        model = _make_mlx_model_mocked()

        def _always_fail(m: Any, t: Any, p: Any, **kwargs: Any) -> str:
            raise TypeError("got an unexpected keyword argument 'anything'")

        with (
            patch("file_organizer.models.mlx_text_model.mlx_generate", _always_fail),
            pytest.raises(TypeError),
        ):
            model._call_generate(prompt="test", max_tokens=100, temperature=0.5, top_p=0.9, top_k=5)

    def test_get_default_config_returns_text_type_and_mlx_provider(self) -> None:
        from file_organizer.models.base import ModelType
        from file_organizer.models.mlx_text_model import MLXTextModel

        config = MLXTextModel.get_default_config("mlx-community/Llama-3.2-1B")
        assert config.model_type == ModelType.TEXT
        assert config.provider == "mlx"
        assert config.model_path == "mlx-community/Llama-3.2-1B"


# ===========================================================================
# _claude_response.py
# ===========================================================================


class TestClaudeResponseHelpersExtended:
    """Extended edge-case tests for _claude_response.py."""

    def test_extract_claude_text_strips_text(self) -> None:
        from file_organizer.models._claude_response import extract_claude_text

        resp = _claude_ok("  hello  ")
        assert extract_claude_text(resp) == "hello"

    def test_extract_claude_text_empty_content_list_returns_empty(self) -> None:
        from file_organizer.models._claude_response import extract_claude_text

        resp = MagicMock()
        resp.content = []
        assert extract_claude_text(resp) == ""

    def test_extract_claude_text_none_content_attr_returns_empty(self) -> None:
        from file_organizer.models._claude_response import extract_claude_text

        resp = MagicMock()
        resp.content = None
        assert extract_claude_text(resp) == ""

    def test_extract_claude_text_missing_content_attr_returns_empty(self) -> None:
        from file_organizer.models._claude_response import extract_claude_text

        resp = MagicMock(spec=[])
        assert extract_claude_text(resp) == ""

    def test_extract_claude_text_block_with_none_text_returns_empty(self) -> None:
        from file_organizer.models._claude_response import extract_claude_text

        block = MagicMock()
        block.text = None
        resp = MagicMock()
        resp.content = [block]
        assert extract_claude_text(resp) == ""

    def test_is_token_exhausted_true_when_max_tokens_and_empty_content(self) -> None:
        from file_organizer.models._claude_response import is_claude_token_exhausted

        resp = _claude_exhausted("")
        assert is_claude_token_exhausted(resp) is True

    def test_is_token_exhausted_false_when_end_turn(self) -> None:
        from file_organizer.models._claude_response import is_claude_token_exhausted

        resp = _claude_ok("Decent response")
        assert is_claude_token_exhausted(resp) is False

    def test_is_token_exhausted_false_when_max_tokens_but_long_response(self) -> None:
        from file_organizer.models._claude_response import is_claude_token_exhausted

        resp = _claude_exhausted("This response is long enough to not be considered exhausted.")
        assert is_claude_token_exhausted(resp) is False

    def test_is_token_exhausted_none_stop_reason_returns_false(self) -> None:
        from file_organizer.models._claude_response import is_claude_token_exhausted

        resp = MagicMock()
        resp.stop_reason = None
        resp.content = []
        assert is_claude_token_exhausted(resp) is False

    def test_is_token_exhausted_with_custom_min_length(self) -> None:
        from file_organizer.models._claude_response import is_claude_token_exhausted

        block = MagicMock()
        block.text = "hi"
        resp = MagicMock()
        resp.stop_reason = "max_tokens"
        resp.content = [block]
        # "hi" (2 chars) < 100 → True
        assert is_claude_token_exhausted(resp, min_length=100) is True
        # "hi" (2 chars) >= 1 → False
        assert is_claude_token_exhausted(resp, min_length=1) is False


# ===========================================================================
# _claude_client.py
# ===========================================================================


class TestClaudeClientFactoryMocked:
    """Mock-based tests for _claude_client.py — no anthropic package needed."""

    @pytest.fixture(autouse=True)
    def _patch_anthropic_available(self) -> None:
        patcher = patch("file_organizer.models._claude_client.ANTHROPIC_AVAILABLE", True)
        patcher.start()
        yield
        patcher.stop()

    def test_create_client_returns_anthropic_instance(self) -> None:
        from file_organizer.models._claude_client import create_claude_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="claude-3-5-sonnet", model_type=ModelType.TEXT, provider="claude")
        mock_client = MagicMock()
        with patch("file_organizer.models._claude_client.Anthropic", return_value=mock_client):
            result = create_claude_client(config, "text")
        assert result is mock_client

    def test_create_client_passes_api_key_when_set(self) -> None:
        from file_organizer.models._claude_client import create_claude_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(
            name="claude-3-5-sonnet",
            model_type=ModelType.TEXT,
            provider="claude",
            api_key="sk-ant-test",
        )
        mock_cls = MagicMock(return_value=MagicMock())
        with patch("file_organizer.models._claude_client.Anthropic", mock_cls):
            create_claude_client(config, "text")
        mock_cls.assert_called_once_with(api_key="sk-ant-test")

    def test_create_client_no_api_key_calls_without_kwargs(self) -> None:
        from file_organizer.models._claude_client import create_claude_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="claude-3-5-sonnet", model_type=ModelType.TEXT, provider="claude")
        mock_cls = MagicMock(return_value=MagicMock())
        with patch("file_organizer.models._claude_client.Anthropic", mock_cls):
            create_claude_client(config, "text")
        mock_cls.assert_called_once_with()

    def test_create_client_logs_warning_and_ignores_base_url(self) -> None:
        from file_organizer.models._claude_client import create_claude_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(
            name="claude-3-5-sonnet",
            model_type=ModelType.TEXT,
            provider="claude",
            api_base_url="http://custom.endpoint",
        )
        with patch("file_organizer.models._claude_client.Anthropic", return_value=MagicMock()):
            # Should not raise — base_url is silently ignored
            result = create_claude_client(config, "text")
        assert result is not None

    def test_create_client_reraises_value_error_from_init(self) -> None:
        from file_organizer.models._claude_client import create_claude_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="claude-3-5-sonnet", model_type=ModelType.TEXT, provider="claude")
        with (
            patch(
                "file_organizer.models._claude_client.Anthropic",
                side_effect=ValueError("bad api key"),
            ),
            pytest.raises(ValueError, match="bad api key"),
        ):
            create_claude_client(config, "text")

    def test_create_client_reraises_type_error_from_init(self) -> None:
        from file_organizer.models._claude_client import create_claude_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="claude-3-5-sonnet", model_type=ModelType.TEXT, provider="claude")
        with (
            patch(
                "file_organizer.models._claude_client.Anthropic",
                side_effect=TypeError("bad type"),
            ),
            pytest.raises(TypeError, match="bad type"),
        ):
            create_claude_client(config, "text")

    def test_create_client_raises_import_error_when_unavailable(self) -> None:
        from file_organizer.models._claude_client import create_claude_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="claude-3-5-sonnet", model_type=ModelType.TEXT, provider="claude")
        with (
            patch("file_organizer.models._claude_client.ANTHROPIC_AVAILABLE", False),
            pytest.raises(ImportError, match="anthropic"),
        ):
            create_claude_client(config, "text")

    def test_create_client_works_for_vision_label(self) -> None:
        from file_organizer.models._claude_client import create_claude_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(
            name="claude-3-5-sonnet", model_type=ModelType.VISION, provider="claude"
        )
        mock_client = MagicMock()
        with patch("file_organizer.models._claude_client.Anthropic", return_value=mock_client):
            result = create_claude_client(config, "vision")
        assert result is mock_client


# ===========================================================================
# _openai_client.py
# ===========================================================================


class TestOpenAIClientFactoryMocked:
    """Mock-based tests for _openai_client.py — no openai package needed."""

    @pytest.fixture(autouse=True)
    def _patch_openai_available(self) -> None:
        patcher = patch("file_organizer.models._openai_client.OPENAI_AVAILABLE", True)
        patcher.start()
        yield
        patcher.stop()

    def test_create_client_returns_openai_instance(self) -> None:
        from file_organizer.models._openai_client import create_openai_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        mock_client = MagicMock()
        with patch("file_organizer.models._openai_client.OpenAI", return_value=mock_client):
            result = create_openai_client(config, "text")
        assert result is mock_client

    def test_create_client_passes_api_key_and_base_url(self) -> None:
        from file_organizer.models._openai_client import create_openai_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(
            name="gpt-4o-mini",
            model_type=ModelType.TEXT,
            provider="openai",
            api_key="sk-test",
            api_base_url="http://localhost:1234/v1",
        )
        mock_cls = MagicMock(return_value=MagicMock())
        with patch("file_organizer.models._openai_client.OpenAI", mock_cls):
            create_openai_client(config, "text")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="http://localhost:1234/v1")

    def test_create_client_only_base_url_no_api_key(self) -> None:
        from file_organizer.models._openai_client import create_openai_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(
            name="gpt-4o-mini",
            model_type=ModelType.TEXT,
            provider="openai",
            api_base_url="http://localhost:1234/v1",
        )
        mock_cls = MagicMock(return_value=MagicMock())
        with patch("file_organizer.models._openai_client.OpenAI", mock_cls):
            create_openai_client(config, "text")
        mock_cls.assert_called_once_with(base_url="http://localhost:1234/v1")

    def test_create_client_no_key_no_base_calls_with_no_kwargs(self) -> None:
        from file_organizer.models._openai_client import create_openai_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        mock_cls = MagicMock(return_value=MagicMock())
        with patch("file_organizer.models._openai_client.OpenAI", mock_cls):
            create_openai_client(config, "text")
        mock_cls.assert_called_once_with()

    def test_create_client_reraises_value_error_from_init(self) -> None:
        from file_organizer.models._openai_client import create_openai_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        with (
            patch(
                "file_organizer.models._openai_client.OpenAI",
                side_effect=ValueError("bad"),
            ),
            pytest.raises(ValueError, match="bad"),
        ):
            create_openai_client(config, "text")

    def test_create_client_raises_import_error_when_unavailable(self) -> None:
        from file_organizer.models._openai_client import create_openai_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        with (
            patch("file_organizer.models._openai_client.OPENAI_AVAILABLE", False),
            pytest.raises(ImportError, match="openai"),
        ):
            create_openai_client(config, "text")

    def test_get_openai_api_error_returns_exception_subclass(self) -> None:
        from file_organizer.models._openai_client import get_openai_api_error

        err_type = get_openai_api_error()
        assert issubclass(err_type, BaseException)

    def test_create_client_reraises_os_error_from_init(self) -> None:
        from file_organizer.models._openai_client import create_openai_client
        from file_organizer.models.base import ModelConfig, ModelType

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        with (
            patch(
                "file_organizer.models._openai_client.OpenAI",
                side_effect=OSError("connection refused"),
            ),
            pytest.raises(OSError, match="connection refused"),
        ):
            create_openai_client(config, "text")


# ===========================================================================
# claude_text_model.py
# ===========================================================================


def _make_claude_text_mocked() -> Any:
    from file_organizer.models.claude_text_model import ClaudeTextModel

    config = ClaudeTextModel.get_default_config("claude-3-5-haiku-20241022")
    with patch("file_organizer.models.claude_text_model.ANTHROPIC_AVAILABLE", True):
        model = ClaudeTextModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


class TestClaudeTextModelMocked:
    """Mock-based tests — no anthropic package needed."""

    @pytest.fixture(autouse=True)
    def _patch_anthropic(self) -> None:
        patcher = patch("file_organizer.models.claude_text_model.ANTHROPIC_AVAILABLE", True)
        patcher.start()
        yield
        patcher.stop()

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.claude_text_model import ClaudeTextModel

        config = ModelConfig(name="x", model_type=ModelType.TEXT, provider="claude")
        with (
            patch("file_organizer.models.claude_text_model.ANTHROPIC_AVAILABLE", False),
            pytest.raises(ImportError, match="anthropic"),
        ):
            ClaudeTextModel(config)

    def test_init_raises_value_error_for_wrong_type(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.claude_text_model import ClaudeTextModel

        config = ModelConfig(name="x", model_type=ModelType.VISION, provider="claude")
        with pytest.raises(ValueError, match="TEXT"):
            ClaudeTextModel(config)

    def test_initialize_creates_client(self) -> None:
        from file_organizer.models.claude_text_model import ClaudeTextModel

        config = ClaudeTextModel.get_default_config()
        mock_client = MagicMock()
        with patch(
            "file_organizer.models.claude_text_model.create_claude_client",
            return_value=mock_client,
        ):
            model = ClaudeTextModel(config)
            model.initialize()
        assert model.client is mock_client
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_claude_text_mocked()
        original = model.client
        model.initialize()
        assert model.client is original

    def test_generate_returns_stripped_text(self) -> None:
        model = _make_claude_text_mocked()
        model.client.messages.create.return_value = _claude_ok("  hello claude  ")
        result = model.generate("Say hello")
        assert result == "hello claude"

    def test_generate_passes_temperature_and_max_tokens(self) -> None:
        model = _make_claude_text_mocked()
        model.client.messages.create.return_value = _claude_ok("ok")
        model.generate("prompt", temperature=0.1, max_tokens=512)
        call_kwargs = model.client.messages.create.call_args[1]
        assert call_kwargs["temperature"] == pytest.approx(0.1)
        assert call_kwargs["max_tokens"] == 512

    def test_generate_token_exhaustion_retries_once(self) -> None:
        model = _make_claude_text_mocked()
        model.client.messages.create.side_effect = [
            _claude_exhausted(),
            _claude_ok("retry text"),
        ]
        result = model.generate("prompt")
        assert result == "retry text"
        assert model.client.messages.create.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self) -> None:
        from file_organizer.models.base import TokenExhaustionError

        model = _make_claude_text_mocked()
        model.client.messages.create.return_value = _claude_exhausted()
        with pytest.raises(TokenExhaustionError):
            model.generate("prompt")
        assert model.client.messages.create.call_count == 2

    def test_generate_not_initialized_raises_runtime_error(self) -> None:
        model = _make_claude_text_mocked()
        model.client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("prompt")

    def test_generate_propagates_runtime_error(self) -> None:
        model = _make_claude_text_mocked()
        model.client.messages.create.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            model.generate("prompt")

    def test_cleanup_releases_client(self) -> None:
        model = _make_claude_text_mocked()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_cleanup_handles_close_error(self) -> None:
        model = _make_claude_text_mocked()
        model.client.close.side_effect = OSError("close failed")
        model.cleanup()  # must not raise
        assert model.client is None

    def test_cleanup_when_client_is_none(self) -> None:
        model = _make_claude_text_mocked()
        model.client = None
        model.cleanup()  # must not raise

    def test_get_default_config_returns_text_provider_claude(self) -> None:
        from file_organizer.models.base import ModelType
        from file_organizer.models.claude_text_model import ClaudeTextModel

        config = ClaudeTextModel.get_default_config("claude-3-opus-20240229")
        assert config.model_type == ModelType.TEXT
        assert config.provider == "claude"
        assert config.name == "claude-3-opus-20240229"


# ===========================================================================
# claude_vision_model.py
# ===========================================================================


def _make_claude_vision_mocked(tmp_path: Any = None) -> Any:
    from file_organizer.models.claude_vision_model import ClaudeVisionModel

    config = ClaudeVisionModel.get_default_config()
    with patch("file_organizer.models.claude_vision_model.ANTHROPIC_AVAILABLE", True):
        model = ClaudeVisionModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


class TestClaudeVisionModelMocked:
    """Mock-based tests — no anthropic package needed."""

    @pytest.fixture(autouse=True)
    def _patch_anthropic(self) -> None:
        patcher = patch("file_organizer.models.claude_vision_model.ANTHROPIC_AVAILABLE", True)
        patcher.start()
        yield
        patcher.stop()

    @pytest.fixture()
    def tiny_png(self, tmp_path: Any) -> Any:
        path = tmp_path / "image.png"
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return path

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.claude_vision_model import ClaudeVisionModel

        config = ModelConfig(name="x", model_type=ModelType.VISION, provider="claude")
        with (
            patch("file_organizer.models.claude_vision_model.ANTHROPIC_AVAILABLE", False),
            pytest.raises(ImportError, match="anthropic"),
        ):
            ClaudeVisionModel(config)

    def test_init_raises_value_error_for_text_type(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.claude_vision_model import ClaudeVisionModel

        config = ModelConfig(name="x", model_type=ModelType.TEXT, provider="claude")
        with pytest.raises(ValueError, match="VISION"):
            ClaudeVisionModel(config)

    def test_init_accepts_video_type(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.claude_vision_model import ClaudeVisionModel

        config = ModelConfig(name="x", model_type=ModelType.VIDEO, provider="claude")
        # Should not raise
        model = ClaudeVisionModel(config)
        assert model is not None

    def test_initialize_creates_client(self) -> None:
        from file_organizer.models.claude_vision_model import ClaudeVisionModel

        config = ClaudeVisionModel.get_default_config()
        mock_client = MagicMock()
        with patch(
            "file_organizer.models.claude_vision_model.create_claude_client",
            return_value=mock_client,
        ):
            model = ClaudeVisionModel(config)
            model.initialize()
        assert model.client is mock_client
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_claude_vision_mocked()
        original = model.client
        model.initialize()
        assert model.client is original

    def test_generate_with_image_data_returns_stripped_text(self) -> None:
        model = _make_claude_vision_mocked()
        model.client.messages.create.return_value = _claude_ok("  image description  ")
        result = model.generate("Describe this", image_data=b"\x89PNG")
        assert result == "image description"

    def test_generate_with_image_path_returns_text(self, tiny_png: Any) -> None:
        model = _make_claude_vision_mocked()
        model.client.messages.create.return_value = _claude_ok("described")
        result = model.generate("Describe", image_path=tiny_png)
        assert result == "described"

    def test_generate_raises_value_error_when_neither_provided(self) -> None:
        model = _make_claude_vision_mocked()
        with pytest.raises(ValueError, match="exactly one"):
            model.generate("Describe")

    def test_generate_raises_value_error_when_both_provided(self, tiny_png: Any) -> None:
        model = _make_claude_vision_mocked()
        with pytest.raises(ValueError, match="exactly one"):
            model.generate("Describe", image_path=tiny_png, image_data=b"\x89PNG")

    def test_generate_raises_runtime_error_when_not_initialized(self) -> None:
        model = _make_claude_vision_mocked()
        model.client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("desc", image_data=b"\x89PNG")

    def test_generate_token_exhaustion_retries_once(self) -> None:
        model = _make_claude_vision_mocked()
        model.client.messages.create.side_effect = [
            _claude_exhausted(),
            _claude_ok("retry vision"),
        ]
        result = model.generate("Describe", image_data=b"\x89PNG")
        assert result == "retry vision"
        assert model.client.messages.create.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self) -> None:
        from file_organizer.models.base import TokenExhaustionError

        model = _make_claude_vision_mocked()
        model.client.messages.create.return_value = _claude_exhausted()
        with pytest.raises(TokenExhaustionError):
            model.generate("Describe", image_data=b"\x89PNG")

    def test_analyze_image_calls_generate_with_correct_prompt(self, tiny_png: Any) -> None:
        model = _make_claude_vision_mocked()
        model.client.messages.create.return_value = _claude_ok("categorized")
        result = model.analyze_image(tiny_png, task="categorize")
        assert result == "categorized"

    def test_analyze_image_uses_custom_prompt_kwarg(self, tiny_png: Any) -> None:
        model = _make_claude_vision_mocked()
        model.client.messages.create.return_value = _claude_ok("custom result")
        result = model.analyze_image(tiny_png, task="describe", custom_prompt="Custom prompt here")
        assert result == "custom result"

    def test_cleanup_releases_client(self) -> None:
        model = _make_claude_vision_mocked()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_cleanup_handles_close_error(self) -> None:
        model = _make_claude_vision_mocked()
        model.client.close.side_effect = OSError("close failed")
        model.cleanup()  # must not raise
        assert model.client is None

    def test_get_default_config_returns_vision_provider_claude(self) -> None:
        from file_organizer.models.base import ModelType
        from file_organizer.models.claude_vision_model import ClaudeVisionModel

        config = ClaudeVisionModel.get_default_config()
        assert config.model_type == ModelType.VISION
        assert config.provider == "claude"
        assert config.name == "claude-3-5-sonnet-20241022"


# ===========================================================================
# openai_text_model.py
# ===========================================================================


def _make_openai_text_mocked() -> Any:
    from file_organizer.models.openai_text_model import OpenAITextModel

    config = OpenAITextModel.get_default_config("gpt-4o-mini")
    with patch("file_organizer.models.openai_text_model.OPENAI_AVAILABLE", True):
        model = OpenAITextModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


class TestOpenAITextModelMocked:
    """Mock-based tests — no openai package needed."""

    @pytest.fixture(autouse=True)
    def _patch_openai(self) -> None:
        patcher = patch("file_organizer.models.openai_text_model.OPENAI_AVAILABLE", True)
        patcher.start()
        yield
        patcher.stop()

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.openai_text_model import OpenAITextModel

        config = ModelConfig(name="gpt-4o", model_type=ModelType.TEXT, provider="openai")
        with (
            patch("file_organizer.models.openai_text_model.OPENAI_AVAILABLE", False),
            pytest.raises(ImportError, match="openai"),
        ):
            OpenAITextModel(config)

    def test_init_raises_value_error_for_wrong_type(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.openai_text_model import OpenAITextModel

        config = ModelConfig(name="gpt-4o", model_type=ModelType.VISION, provider="openai")
        with pytest.raises(ValueError, match="TEXT"):
            OpenAITextModel(config)

    def test_initialize_creates_client(self) -> None:
        from file_organizer.models.openai_text_model import OpenAITextModel

        config = OpenAITextModel.get_default_config()
        mock_client = MagicMock()
        with patch(
            "file_organizer.models.openai_text_model.create_openai_client",
            return_value=mock_client,
        ):
            model = OpenAITextModel(config)
            model.initialize()
        assert model.client is mock_client
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_openai_text_mocked()
        original = model.client
        model.initialize()
        assert model.client is original

    def test_generate_returns_stripped_text(self) -> None:
        model = _make_openai_text_mocked()
        model.client.chat.completions.create.return_value = _openai_ok("  openai result  ")
        result = model.generate("Test prompt")
        assert result == "openai result"

    def test_generate_passes_temperature_and_max_tokens(self) -> None:
        model = _make_openai_text_mocked()
        model.client.chat.completions.create.return_value = _openai_ok("ok")
        model.generate("prompt", temperature=0.2, max_tokens=256)
        call_kwargs = model.client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == pytest.approx(0.2)
        assert call_kwargs["max_tokens"] == 256

    def test_generate_empty_choices_returns_empty_string(self) -> None:
        model = _make_openai_text_mocked()
        resp = MagicMock()
        resp.choices = []
        model.client.chat.completions.create.return_value = resp
        result = model.generate("prompt")
        assert result == ""

    def test_generate_token_exhaustion_retries_once(self) -> None:
        model = _make_openai_text_mocked()
        model.client.chat.completions.create.side_effect = [
            _openai_exhausted(),
            _openai_ok("retry text"),
        ]
        result = model.generate("prompt")
        assert result == "retry text"
        assert model.client.chat.completions.create.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self) -> None:
        from file_organizer.models.base import TokenExhaustionError

        model = _make_openai_text_mocked()
        model.client.chat.completions.create.return_value = _openai_exhausted()
        with pytest.raises(TokenExhaustionError):
            model.generate("prompt")

    def test_generate_not_initialized_raises_runtime_error(self) -> None:
        model = _make_openai_text_mocked()
        model.client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("prompt")

    def test_generate_propagates_runtime_error(self) -> None:
        model = _make_openai_text_mocked()
        model.client.chat.completions.create.side_effect = RuntimeError("API error")
        with (
            patch("file_organizer.models.openai_text_model.OpenAIAPIError", Exception),
            pytest.raises(RuntimeError, match="API error"),
        ):
            model.generate("prompt")

    def test_cleanup_releases_client(self) -> None:
        model = _make_openai_text_mocked()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_cleanup_handles_close_error(self) -> None:
        model = _make_openai_text_mocked()
        model.client.close.side_effect = OSError("close failed")
        model.cleanup()  # must not raise
        assert model.client is None

    def test_cleanup_when_client_none(self) -> None:
        model = _make_openai_text_mocked()
        model.client = None
        model.cleanup()  # must not raise

    def test_get_default_config_returns_text_provider_openai(self) -> None:
        from file_organizer.models.base import ModelType
        from file_organizer.models.openai_text_model import OpenAITextModel

        config = OpenAITextModel.get_default_config("gpt-4o")
        assert config.model_type == ModelType.TEXT
        assert config.provider == "openai"
        assert config.name == "gpt-4o"


# ===========================================================================
# openai_vision_model.py
# ===========================================================================


def _make_openai_vision_mocked() -> Any:
    from file_organizer.models.openai_vision_model import OpenAIVisionModel

    config = OpenAIVisionModel.get_default_config("gpt-4o-mini")
    with patch("file_organizer.models.openai_vision_model.OPENAI_AVAILABLE", True):
        model = OpenAIVisionModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


class TestOpenAIVisionModelMocked:
    """Mock-based tests — no openai package needed."""

    @pytest.fixture(autouse=True)
    def _patch_openai(self) -> None:
        patcher = patch("file_organizer.models.openai_vision_model.OPENAI_AVAILABLE", True)
        patcher.start()
        yield
        patcher.stop()

    @pytest.fixture()
    def tiny_png(self, tmp_path: Any) -> Any:
        path = tmp_path / "image.png"
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return path

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.openai_vision_model import OpenAIVisionModel

        config = ModelConfig(name="gpt-4o", model_type=ModelType.VISION, provider="openai")
        with (
            patch("file_organizer.models.openai_vision_model.OPENAI_AVAILABLE", False),
            pytest.raises(ImportError, match="openai"),
        ):
            OpenAIVisionModel(config)

    def test_init_raises_value_error_for_text_type(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.openai_vision_model import OpenAIVisionModel

        config = ModelConfig(name="gpt-4o", model_type=ModelType.TEXT, provider="openai")
        with pytest.raises(ValueError, match="VISION"):
            OpenAIVisionModel(config)

    def test_init_accepts_video_type(self) -> None:
        from file_organizer.models.base import ModelConfig, ModelType
        from file_organizer.models.openai_vision_model import OpenAIVisionModel

        config = ModelConfig(name="gpt-4o", model_type=ModelType.VIDEO, provider="openai")
        model = OpenAIVisionModel(config)
        assert model is not None

    def test_initialize_creates_client(self) -> None:
        from file_organizer.models.openai_vision_model import OpenAIVisionModel

        config = OpenAIVisionModel.get_default_config()
        mock_client = MagicMock()
        with patch(
            "file_organizer.models.openai_vision_model.create_openai_client",
            return_value=mock_client,
        ):
            model = OpenAIVisionModel(config)
            model.initialize()
        assert model.client is mock_client
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_openai_vision_mocked()
        original = model.client
        model.initialize()
        assert model.client is original

    def test_generate_with_image_data_returns_stripped_text(self) -> None:
        model = _make_openai_vision_mocked()
        model.client.chat.completions.create.return_value = _openai_ok("  described image  ")
        result = model.generate("Describe", image_data=b"\x89PNG")
        assert result == "described image"

    def test_generate_with_image_path_returns_text(self, tiny_png: Any) -> None:
        model = _make_openai_vision_mocked()
        model.client.chat.completions.create.return_value = _openai_ok("image text")
        result = model.generate("Describe", image_path=tiny_png)
        assert result == "image text"

    def test_generate_raises_value_error_when_neither_provided(self) -> None:
        model = _make_openai_vision_mocked()
        with pytest.raises(ValueError, match="exactly one"):
            model.generate("Describe")

    def test_generate_raises_value_error_when_both_provided(self, tiny_png: Any) -> None:
        model = _make_openai_vision_mocked()
        with pytest.raises(ValueError, match="exactly one"):
            model.generate("Describe", image_path=tiny_png, image_data=b"\x89PNG")

    def test_generate_raises_runtime_error_when_not_initialized(self) -> None:
        model = _make_openai_vision_mocked()
        model.client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("desc", image_data=b"\x89PNG")

    def test_generate_empty_choices_returns_empty_string(self) -> None:
        model = _make_openai_vision_mocked()
        resp = MagicMock()
        resp.choices = []
        model.client.chat.completions.create.return_value = resp
        result = model.generate("Describe", image_data=b"\x89PNG")
        assert result == ""

    def test_generate_token_exhaustion_retries_once(self) -> None:
        model = _make_openai_vision_mocked()
        model.client.chat.completions.create.side_effect = [
            _openai_exhausted(),
            _openai_ok("retry vision text"),
        ]
        result = model.generate("Describe", image_data=b"\x89PNG")
        assert result == "retry vision text"
        assert model.client.chat.completions.create.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self) -> None:
        from file_organizer.models.base import TokenExhaustionError

        model = _make_openai_vision_mocked()
        model.client.chat.completions.create.return_value = _openai_exhausted()
        with pytest.raises(TokenExhaustionError):
            model.generate("Describe", image_data=b"\x89PNG")

    def test_analyze_image_calls_generate_with_task_prompt(self, tiny_png: Any) -> None:
        model = _make_openai_vision_mocked()
        model.client.chat.completions.create.return_value = _openai_ok("categorized")
        result = model.analyze_image(tiny_png, task="categorize")
        assert result == "categorized"

    def test_analyze_image_uses_custom_prompt_kwarg(self, tiny_png: Any) -> None:
        model = _make_openai_vision_mocked()
        model.client.chat.completions.create.return_value = _openai_ok("custom result")
        result = model.analyze_image(tiny_png, task="describe", custom_prompt="My custom prompt")
        assert result == "custom result"

    def test_cleanup_releases_client(self) -> None:
        model = _make_openai_vision_mocked()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_cleanup_handles_close_error(self) -> None:
        model = _make_openai_vision_mocked()
        model.client.close.side_effect = OSError("close failed")
        model.cleanup()  # must not raise
        assert model.client is None

    def test_get_default_config_returns_vision_provider_openai(self) -> None:
        from file_organizer.models.base import ModelType
        from file_organizer.models.openai_vision_model import OpenAIVisionModel

        config = OpenAIVisionModel.get_default_config("gpt-4o")
        assert config.model_type == ModelType.VISION
        assert config.provider == "openai"
        assert config.name == "gpt-4o"
