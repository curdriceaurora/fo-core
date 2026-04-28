"""Integration tests for Claude, OpenAI, and Audio model implementations.

Covers:
- _claude_client.py: create_claude_client() (success, api_base_url warning, init errors)
- _claude_response.py: is_claude_token_exhausted(), extract_claude_text()
- _openai_client.py: create_openai_client(), get_openai_api_error()
- _vision_helpers.py: image_to_data_url(), bytes_to_data_url(), split_data_url()
- claude_text_model.py: ClaudeTextModel init/initialize/generate/cleanup/default_config
- claude_vision_model.py: ClaudeVisionModel init/initialize/generate/analyze_image/cleanup
- openai_text_model.py: OpenAITextModel init/initialize/generate/cleanup/default_config
- openai_vision_model.py: OpenAIVisionModel init/initialize/generate/analyze_image/cleanup
- audio_model.py: AudioModel init/initialize/generate/cleanup/default_config
- audio_transcriber.py: AudioTranscriber init/transcribe/detect_language/clear_cache
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers — mock response factories
# ---------------------------------------------------------------------------


def _claude_ok_response(text: str = "Claude response text") -> MagicMock:
    """Return a mock Anthropic Messages API response (stop_reason='end_turn')."""
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _claude_exhausted_response(text: str = "") -> MagicMock:
    """Return a mock response that triggers is_claude_token_exhausted."""
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "max_tokens"
    resp.content = [block]
    return resp


def _openai_ok_response(text: str = "OpenAI response text") -> MagicMock:
    """Return a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _openai_exhausted_response(text: str = "") -> MagicMock:
    """Return a mock OpenAI response that triggers is_openai_token_exhausted."""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "length"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tiny_png(tmp_path: Path) -> Path:
    """Write a minimal PNG file and return its path."""
    path = tmp_path / "image.png"
    # Minimal 1x1 PNG bytes
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return path


# ===========================================================================
# _claude_response.py
# ===========================================================================


class TestClaudeResponseHelpers:
    def test_extract_claude_text_returns_stripped_text(self) -> None:
        from models._claude_response import extract_claude_text

        resp = _claude_ok_response("  hello world  ")
        assert extract_claude_text(resp) == "hello world"

    def test_extract_claude_text_empty_content_returns_empty_string(self) -> None:
        from models._claude_response import extract_claude_text

        resp = MagicMock()
        resp.content = []
        assert extract_claude_text(resp) == ""

    def test_extract_claude_text_no_content_attr_returns_empty_string(self) -> None:
        from models._claude_response import extract_claude_text

        resp = MagicMock(spec=[])  # no attributes
        assert extract_claude_text(resp) == ""

    def test_is_claude_token_exhausted_false_on_end_turn(self) -> None:
        from models._claude_response import is_claude_token_exhausted

        resp = _claude_ok_response("A sufficiently long response here.")
        assert is_claude_token_exhausted(resp) is False

    def test_is_claude_token_exhausted_true_on_max_tokens_short_content(self) -> None:
        from models._claude_response import is_claude_token_exhausted

        resp = _claude_exhausted_response("")
        assert is_claude_token_exhausted(resp) is True

    def test_is_claude_token_exhausted_false_when_max_tokens_but_long_content(self) -> None:
        from models._claude_response import is_claude_token_exhausted

        resp = _claude_exhausted_response("This is a long enough response for the check.")
        assert is_claude_token_exhausted(resp) is False


# ===========================================================================
# _vision_helpers.py
# ===========================================================================


class TestVisionHelpers:
    def test_image_to_data_url_returns_data_url(self, tiny_png: Path) -> None:
        from models._vision_helpers import image_to_data_url

        url = image_to_data_url(tiny_png)
        assert url.startswith("data:image/png;base64,")

    def test_image_to_data_url_unknown_ext_uses_jpeg_mime(self, tmp_path: Path) -> None:
        from models._vision_helpers import image_to_data_url

        path = tmp_path / "img.unknownext"
        path.write_bytes(b"\x00\x01\x02")
        url = image_to_data_url(path)
        assert url.startswith("data:image/jpeg;base64,")

    def test_image_to_data_url_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        from models._vision_helpers import image_to_data_url

        with pytest.raises(FileNotFoundError):
            image_to_data_url(tmp_path / "nonexistent.png")

    def test_bytes_to_data_url_default_mime(self) -> None:
        from models._vision_helpers import bytes_to_data_url

        url = bytes_to_data_url(b"\x01\x02\x03")
        assert url.startswith("data:image/jpeg;base64,")

    def test_bytes_to_data_url_custom_mime(self) -> None:
        from models._vision_helpers import bytes_to_data_url

        url = bytes_to_data_url(b"\x01", mime_type="image/webp")
        assert url.startswith("data:image/webp;base64,")

    def test_split_data_url_round_trips(self) -> None:
        from models._vision_helpers import bytes_to_data_url, split_data_url

        data = b"test image bytes"
        url = bytes_to_data_url(data, mime_type="image/png")
        mime, b64 = split_data_url(url)
        assert mime == "image/png"
        import base64

        assert base64.b64decode(b64) == data

    def test_split_data_url_invalid_raises_value_error(self) -> None:
        from models._vision_helpers import split_data_url

        with pytest.raises(ValueError):
            split_data_url("not-a-data-url")

    def test_split_data_url_no_base64_marker_raises_value_error(self) -> None:
        from models._vision_helpers import split_data_url

        with pytest.raises(ValueError):
            split_data_url("data:image/png,notbase64")

    def test_split_data_url_empty_mime_falls_back_to_jpeg(self) -> None:
        from models._vision_helpers import split_data_url

        # Construct a data URL with an empty MIME type: "data:;base64,abc"
        mime, b64 = split_data_url("data:;base64,abc")
        assert mime == "image/jpeg"
        assert b64 == "abc"


# ===========================================================================
# _claude_client.py
# ===========================================================================


class TestClaudeClientFactory:
    @pytest.fixture(autouse=True)
    def _require_anthropic(self) -> None:
        pytest.importorskip("anthropic")

    def test_create_claude_client_returns_client(self) -> None:
        from models._claude_client import create_claude_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="claude-3-5-sonnet", model_type=ModelType.TEXT, provider="claude")
        mock_client = MagicMock()
        with patch("models._claude_client.Anthropic", return_value=mock_client):
            client = create_claude_client(config, "text")
        assert client is mock_client

    def test_create_claude_client_passes_api_key(self) -> None:
        from models._claude_client import create_claude_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(
            name="claude-3-5-sonnet",
            model_type=ModelType.TEXT,
            provider="claude",
            api_key="sk-test-key",
        )
        mock_cls = MagicMock(return_value=MagicMock())
        with patch("models._claude_client.Anthropic", mock_cls):
            create_claude_client(config, "text")
        mock_cls.assert_called_once_with(api_key="sk-test-key")

    def test_create_claude_client_no_api_key_calls_without_key(self) -> None:
        from models._claude_client import create_claude_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="claude-3-5-sonnet", model_type=ModelType.TEXT, provider="claude")
        mock_cls = MagicMock(return_value=MagicMock())
        with patch("models._claude_client.Anthropic", mock_cls):
            create_claude_client(config, "text")
        mock_cls.assert_called_once_with()

    def test_create_claude_client_logs_warning_for_base_url(self) -> None:
        from models._claude_client import create_claude_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(
            name="claude-3-5-sonnet",
            model_type=ModelType.TEXT,
            provider="claude",
            api_base_url="http://custom.endpoint",
        )
        with patch("models._claude_client.Anthropic", return_value=MagicMock()):
            # Should NOT raise; api_base_url is silently ignored with a warning
            create_claude_client(config, "text")

    def test_create_claude_client_reraises_init_error(self) -> None:
        from models._claude_client import create_claude_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="claude-3-5-sonnet", model_type=ModelType.TEXT, provider="claude")
        with (
            patch(
                "models._claude_client.Anthropic",
                side_effect=ValueError("bad init"),
            ),
            pytest.raises(ValueError, match="bad init"),
        ):
            create_claude_client(config, "text")

    def test_create_claude_client_raises_import_error_when_unavailable(self) -> None:
        from models._claude_client import create_claude_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="claude-3-5-sonnet", model_type=ModelType.TEXT, provider="claude")
        with (
            patch("models._claude_client.ANTHROPIC_AVAILABLE", False),
            pytest.raises(ImportError, match="anthropic"),
        ):
            create_claude_client(config, "text")


# ===========================================================================
# _openai_client.py
# ===========================================================================


class TestOpenAIClientFactory:
    @pytest.fixture(autouse=True)
    def _require_openai(self) -> None:
        pytest.importorskip("openai")

    def test_create_openai_client_returns_client(self) -> None:
        from models._openai_client import create_openai_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        mock_client = MagicMock()
        with patch("models._openai_client.OpenAI", return_value=mock_client):
            client = create_openai_client(config, "text")
        assert client is mock_client

    def test_create_openai_client_passes_api_key_and_base_url(self) -> None:
        from models._openai_client import create_openai_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(
            name="gpt-4o-mini",
            model_type=ModelType.TEXT,
            provider="openai",
            api_key="sk-test",
            api_base_url="http://localhost:1234/v1",
        )
        mock_cls = MagicMock(return_value=MagicMock())
        with patch("models._openai_client.OpenAI", mock_cls):
            create_openai_client(config, "text")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="http://localhost:1234/v1")

    def test_create_openai_client_no_key_or_base(self) -> None:
        from models._openai_client import create_openai_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        mock_cls = MagicMock(return_value=MagicMock())
        with patch("models._openai_client.OpenAI", mock_cls):
            create_openai_client(config, "text")
        mock_cls.assert_called_once_with()

    def test_create_openai_client_reraises_init_error(self) -> None:
        from models._openai_client import create_openai_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        with (
            patch(
                "models._openai_client.OpenAI",
                side_effect=ValueError("bad"),
            ),
            pytest.raises(ValueError, match="bad"),
        ):
            create_openai_client(config, "text")

    def test_create_openai_client_raises_import_error_when_unavailable(self) -> None:
        from models._openai_client import create_openai_client
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        with (
            patch("models._openai_client.OPENAI_AVAILABLE", False),
            pytest.raises(ImportError, match="openai"),
        ):
            create_openai_client(config, "text")

    def test_get_openai_api_error_returns_exception_type(self) -> None:
        from models._openai_client import get_openai_api_error

        err_type = get_openai_api_error()
        assert issubclass(err_type, BaseException)


# ===========================================================================
# claude_text_model.py — ClaudeTextModel
# ===========================================================================


def _make_claude_text_model() -> Any:
    """Return an initialised ClaudeTextModel with a mock client."""
    pytest.importorskip("anthropic")
    from models.claude_text_model import ClaudeTextModel

    config = ClaudeTextModel.get_default_config("claude-3-5-haiku-20241022")
    with patch("models.claude_text_model.ANTHROPIC_AVAILABLE", True):
        model = ClaudeTextModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


class TestClaudeTextModel:
    @pytest.fixture(autouse=True)
    def _require_anthropic(self) -> None:
        pytest.importorskip("anthropic")

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.claude_text_model import ClaudeTextModel

        config = ModelConfig(name="x", model_type=ModelType.TEXT, provider="claude")
        with (
            patch("models.claude_text_model.ANTHROPIC_AVAILABLE", False),
            pytest.raises(ImportError, match="anthropic"),
        ):
            ClaudeTextModel(config)

    def test_init_raises_value_error_for_wrong_type(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.claude_text_model import ClaudeTextModel

        config = ModelConfig(name="x", model_type=ModelType.VISION, provider="claude")
        with (
            patch("models.claude_text_model.ANTHROPIC_AVAILABLE", True),
            pytest.raises(ValueError, match="TEXT"),
        ):
            ClaudeTextModel(config)

    def test_initialize_creates_client(self) -> None:
        from models.claude_text_model import ClaudeTextModel

        config = ClaudeTextModel.get_default_config()
        mock_client = MagicMock()
        with (
            patch("models.claude_text_model.ANTHROPIC_AVAILABLE", True),
            patch(
                "models.claude_text_model.create_claude_client",
                return_value=mock_client,
            ),
        ):
            model = ClaudeTextModel(config)
            model.initialize()
        assert model.client is mock_client
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_claude_text_model()
        original_client = model.client
        model.initialize()  # already initialized
        assert model.client is original_client

    def test_generate_returns_stripped_text(self) -> None:
        model = _make_claude_text_model()
        model.client.messages.create.return_value = _claude_ok_response("  hello claude  ")
        result = model.generate("Say hello")
        assert result == "hello claude"

    def test_generate_passes_temperature_and_max_tokens(self) -> None:
        model = _make_claude_text_model()
        model.client.messages.create.return_value = _claude_ok_response("ok")
        model.generate("prompt", temperature=0.1, max_tokens=512)
        call_kwargs = model.client.messages.create.call_args[1]
        assert call_kwargs["temperature"] == pytest.approx(0.1)
        assert call_kwargs["max_tokens"] == 512

    def test_generate_token_exhaustion_retries_once(self) -> None:

        model = _make_claude_text_model()
        model.client.messages.create.side_effect = [
            _claude_exhausted_response(),
            _claude_ok_response("retry succeeded"),
        ]
        result = model.generate("prompt")
        assert result == "retry succeeded"
        assert model.client.messages.create.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self) -> None:
        from models.base import TokenExhaustionError

        model = _make_claude_text_model()
        model.client.messages.create.return_value = _claude_exhausted_response()
        with pytest.raises(TokenExhaustionError):
            model.generate("prompt")
        assert model.client.messages.create.call_count == 2

    def test_generate_not_initialized_raises_runtime_error(self) -> None:
        model = _make_claude_text_model()
        model.client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("prompt")

    def test_generate_propagates_runtime_error(self) -> None:
        model = _make_claude_text_model()
        model.client.messages.create.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            model.generate("prompt")

    def test_cleanup_sets_client_none(self) -> None:
        model = _make_claude_text_model()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_cleanup_handles_close_error(self) -> None:
        model = _make_claude_text_model()
        model.client.close.side_effect = OSError("close failed")
        model.cleanup()  # should not raise
        assert model.client is None

    def test_get_default_config_returns_text_type(self) -> None:
        from models.base import ModelType
        from models.claude_text_model import ClaudeTextModel

        config = ClaudeTextModel.get_default_config()
        assert config.model_type == ModelType.TEXT
        assert config.provider == "claude"

    def test_get_default_config_custom_model_name(self) -> None:
        from models.claude_text_model import ClaudeTextModel

        config = ClaudeTextModel.get_default_config("claude-3-opus-20240229")
        assert config.name == "claude-3-opus-20240229"


# ===========================================================================
# claude_vision_model.py — ClaudeVisionModel
# ===========================================================================


def _make_claude_vision_model() -> Any:
    """Return an initialised ClaudeVisionModel with a mock client."""
    pytest.importorskip("anthropic")
    from models.claude_vision_model import ClaudeVisionModel

    config = ClaudeVisionModel.get_default_config("claude-3-5-sonnet-20241022")
    with patch("models.claude_vision_model.ANTHROPIC_AVAILABLE", True):
        model = ClaudeVisionModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


class TestClaudeVisionModel:
    @pytest.fixture(autouse=True)
    def _require_anthropic(self) -> None:
        pytest.importorskip("anthropic")

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.claude_vision_model import ClaudeVisionModel

        config = ModelConfig(name="x", model_type=ModelType.VISION, provider="claude")
        with (
            patch("models.claude_vision_model.ANTHROPIC_AVAILABLE", False),
            pytest.raises(ImportError, match="anthropic"),
        ):
            ClaudeVisionModel(config)

    def test_init_raises_value_error_for_wrong_type(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.claude_vision_model import ClaudeVisionModel

        config = ModelConfig(name="x", model_type=ModelType.TEXT, provider="claude")
        with (
            patch("models.claude_vision_model.ANTHROPIC_AVAILABLE", True),
            pytest.raises(ValueError, match="VISION or VIDEO"),
        ):
            ClaudeVisionModel(config)

    def test_initialize_creates_client(self) -> None:
        from models.claude_vision_model import ClaudeVisionModel

        config = ClaudeVisionModel.get_default_config()
        mock_client = MagicMock()
        with (
            patch("models.claude_vision_model.ANTHROPIC_AVAILABLE", True),
            patch(
                "models.claude_vision_model.create_claude_client",
                return_value=mock_client,
            ),
        ):
            model = ClaudeVisionModel(config)
            model.initialize()
        assert model.client is mock_client
        assert model._initialized is True

    def test_generate_with_image_data_returns_text(self) -> None:
        model = _make_claude_vision_model()
        model.client.messages.create.return_value = _claude_ok_response("A cat photo")
        result = model.generate("Describe this", image_data=b"\xff\xd8\xff" + b"\x00" * 10)
        assert result == "A cat photo"

    def test_generate_with_image_path_returns_text(self, tiny_png: Path) -> None:
        model = _make_claude_vision_model()
        model.client.messages.create.return_value = _claude_ok_response("A PNG image")
        result = model.generate("Describe", image_path=tiny_png)
        assert result == "A PNG image"

    def test_generate_raises_if_neither_provided(self) -> None:
        model = _make_claude_vision_model()
        with pytest.raises(ValueError, match="exactly one"):
            model.generate("Describe")

    def test_generate_raises_if_both_provided(self, tiny_png: Path) -> None:
        model = _make_claude_vision_model()
        with pytest.raises(ValueError, match="exactly one"):
            model.generate("Describe", image_path=tiny_png, image_data=b"bytes")

    def test_generate_raises_file_not_found(self, tmp_path: Path) -> None:
        model = _make_claude_vision_model()
        with pytest.raises(FileNotFoundError):
            model.generate("Describe", image_path=tmp_path / "missing.png")

    def test_generate_token_exhaustion_retries(self, tiny_png: Path) -> None:
        model = _make_claude_vision_model()
        model.client.messages.create.side_effect = [
            _claude_exhausted_response(),
            _claude_ok_response("retry ok"),
        ]
        result = model.generate("Describe", image_path=tiny_png)
        assert result == "retry ok"
        assert model.client.messages.create.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self, tiny_png: Path) -> None:
        from models.base import TokenExhaustionError

        model = _make_claude_vision_model()
        model.client.messages.create.return_value = _claude_exhausted_response()
        with pytest.raises(TokenExhaustionError):
            model.generate("Describe", image_path=tiny_png)

    def test_analyze_image_describe_task(self, tiny_png: Path) -> None:
        model = _make_claude_vision_model()
        model.client.messages.create.return_value = _claude_ok_response("A landscape")
        result = model.analyze_image(tiny_png, task="describe")
        assert result == "A landscape"

    def test_analyze_image_custom_prompt_overrides_task(self, tiny_png: Path) -> None:
        model = _make_claude_vision_model()
        model.client.messages.create.return_value = _claude_ok_response("Custom result")
        model.analyze_image(tiny_png, task="describe", custom_prompt="My custom")
        # The prompt is passed inside messages; just verify the call happened
        model.client.messages.create.assert_called_once()

    def test_cleanup_sets_client_none(self) -> None:
        model = _make_claude_vision_model()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_get_default_config_returns_vision_type(self) -> None:
        from models.base import ModelType
        from models.claude_vision_model import ClaudeVisionModel

        config = ClaudeVisionModel.get_default_config()
        assert config.model_type == ModelType.VISION
        assert config.provider == "claude"

    def test_build_image_block_returns_correct_structure(self) -> None:
        from models._vision_helpers import bytes_to_data_url
        from models.claude_vision_model import _build_image_block

        url = bytes_to_data_url(b"\x00\x01\x02", mime_type="image/png")
        block = _build_image_block(url)
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "image/png"


# ===========================================================================
# openai_text_model.py — OpenAITextModel
# ===========================================================================


def _make_openai_text_model() -> Any:
    """Return an initialised OpenAITextModel with a mock client."""
    pytest.importorskip("openai")
    from models.openai_text_model import OpenAITextModel

    config = OpenAITextModel.get_default_config("gpt-4o-mini")
    with patch("models.openai_text_model.OPENAI_AVAILABLE", True):
        model = OpenAITextModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


class TestOpenAITextModel:
    @pytest.fixture(autouse=True)
    def _require_openai(self) -> None:
        pytest.importorskip("openai")

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.openai_text_model import OpenAITextModel

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        with (
            patch("models.openai_text_model.OPENAI_AVAILABLE", False),
            pytest.raises(ImportError, match="openai"),
        ):
            OpenAITextModel(config)

    def test_init_raises_value_error_for_wrong_type(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.openai_text_model import OpenAITextModel

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.VISION, provider="openai")
        with (
            patch("models.openai_text_model.OPENAI_AVAILABLE", True),
            pytest.raises(ValueError, match="TEXT"),
        ):
            OpenAITextModel(config)

    def test_initialize_creates_client(self) -> None:
        from models.openai_text_model import OpenAITextModel

        config = OpenAITextModel.get_default_config()
        mock_client = MagicMock()
        with (
            patch("models.openai_text_model.OPENAI_AVAILABLE", True),
            patch(
                "models.openai_text_model.create_openai_client",
                return_value=mock_client,
            ),
        ):
            model = OpenAITextModel(config)
            model.initialize()
        assert model.client is mock_client
        assert model._initialized is True

    def test_initialize_is_idempotent(self) -> None:
        model = _make_openai_text_model()
        original_client = model.client
        model.initialize()
        assert model.client is original_client

    def test_generate_returns_stripped_text(self) -> None:
        model = _make_openai_text_model()
        model.client.chat.completions.create.return_value = _openai_ok_response("  openai result  ")
        result = model.generate("Say hello")
        assert result == "openai result"

    def test_generate_passes_temperature_and_max_tokens(self) -> None:
        model = _make_openai_text_model()
        model.client.chat.completions.create.return_value = _openai_ok_response("ok")
        model.generate("prompt", temperature=0.2, max_tokens=256)
        call_kwargs = model.client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == pytest.approx(0.2)
        assert call_kwargs["max_tokens"] == 256

    def test_generate_token_exhaustion_retries(self) -> None:
        model = _make_openai_text_model()
        model.client.chat.completions.create.side_effect = [
            _openai_exhausted_response(),
            _openai_ok_response("retry ok"),
        ]
        result = model.generate("prompt")
        assert result == "retry ok"
        assert model.client.chat.completions.create.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self) -> None:
        from models.base import TokenExhaustionError

        model = _make_openai_text_model()
        model.client.chat.completions.create.return_value = _openai_exhausted_response()
        with pytest.raises(TokenExhaustionError):
            model.generate("prompt")

    def test_generate_empty_choices_returns_empty_string(self) -> None:
        model = _make_openai_text_model()
        resp = MagicMock()
        resp.choices = []
        model.client.chat.completions.create.return_value = resp
        result = model.generate("prompt")
        assert result == ""

    def test_generate_not_initialized_raises_runtime_error(self) -> None:
        model = _make_openai_text_model()
        model.client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate("prompt")

    def test_cleanup_sets_client_none(self) -> None:
        model = _make_openai_text_model()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_cleanup_handles_close_error(self) -> None:
        model = _make_openai_text_model()
        model.client.close.side_effect = RuntimeError("close err")
        model.cleanup()  # must not raise
        assert model.client is None

    def test_get_default_config_returns_text_type(self) -> None:
        from models.base import ModelType
        from models.openai_text_model import OpenAITextModel

        config = OpenAITextModel.get_default_config()
        assert config.model_type == ModelType.TEXT
        assert config.provider == "openai"

    def test_get_default_config_custom_model_name(self) -> None:
        from models.openai_text_model import OpenAITextModel

        config = OpenAITextModel.get_default_config("gpt-4o")
        assert config.name == "gpt-4o"


# ===========================================================================
# openai_vision_model.py — OpenAIVisionModel
# ===========================================================================


def _make_openai_vision_model() -> Any:
    """Return an initialised OpenAIVisionModel with a mock client."""
    pytest.importorskip("openai")
    from models.openai_vision_model import OpenAIVisionModel

    config = OpenAIVisionModel.get_default_config("gpt-4o-mini")
    with patch("models.openai_vision_model.OPENAI_AVAILABLE", True):
        model = OpenAIVisionModel(config)
    model._initialized = True
    model.client = MagicMock()
    return model


class TestOpenAIVisionModel:
    @pytest.fixture(autouse=True)
    def _require_openai(self) -> None:
        pytest.importorskip("openai")

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.openai_vision_model import OpenAIVisionModel

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.VISION, provider="openai")
        with (
            patch("models.openai_vision_model.OPENAI_AVAILABLE", False),
            pytest.raises(ImportError, match="openai"),
        ):
            OpenAIVisionModel(config)

    def test_init_raises_value_error_for_wrong_type(self) -> None:
        from models.base import ModelConfig, ModelType
        from models.openai_vision_model import OpenAIVisionModel

        config = ModelConfig(name="gpt-4o-mini", model_type=ModelType.TEXT, provider="openai")
        with (
            patch("models.openai_vision_model.OPENAI_AVAILABLE", True),
            pytest.raises(ValueError, match="VISION or VIDEO"),
        ):
            OpenAIVisionModel(config)

    def test_generate_with_image_data_returns_text(self) -> None:
        model = _make_openai_vision_model()
        model.client.chat.completions.create.return_value = _openai_ok_response("A JPEG image")
        result = model.generate("Describe", image_data=b"\xff\xd8\xff" + b"\x00" * 10)
        assert result == "A JPEG image"

    def test_generate_with_image_path_returns_text(self, tiny_png: Path) -> None:
        model = _make_openai_vision_model()
        model.client.chat.completions.create.return_value = _openai_ok_response("A PNG file")
        result = model.generate("Describe", image_path=tiny_png)
        assert result == "A PNG file"

    def test_generate_raises_if_neither_provided(self) -> None:
        model = _make_openai_vision_model()
        with pytest.raises(ValueError, match="exactly one"):
            model.generate("Describe")

    def test_generate_raises_if_both_provided(self, tiny_png: Path) -> None:
        model = _make_openai_vision_model()
        with pytest.raises(ValueError, match="exactly one"):
            model.generate("Describe", image_path=tiny_png, image_data=b"bytes")

    def test_generate_raises_file_not_found(self, tmp_path: Path) -> None:
        model = _make_openai_vision_model()
        with pytest.raises(FileNotFoundError):
            model.generate("Describe", image_path=tmp_path / "missing.jpg")

    def test_generate_token_exhaustion_retries(self, tiny_png: Path) -> None:
        model = _make_openai_vision_model()
        model.client.chat.completions.create.side_effect = [
            _openai_exhausted_response(),
            _openai_ok_response("retry vision ok"),
        ]
        result = model.generate("Describe", image_path=tiny_png)
        assert result == "retry vision ok"
        assert model.client.chat.completions.create.call_count == 2

    def test_generate_raises_token_exhaustion_on_double_failure(self, tiny_png: Path) -> None:
        from models.base import TokenExhaustionError

        model = _make_openai_vision_model()
        model.client.chat.completions.create.return_value = _openai_exhausted_response()
        with pytest.raises(TokenExhaustionError):
            model.generate("Describe", image_path=tiny_png)

    def test_generate_empty_choices_returns_empty_string(self, tiny_png: Path) -> None:
        model = _make_openai_vision_model()
        resp = MagicMock()
        resp.choices = []
        model.client.chat.completions.create.return_value = resp
        result = model.generate("Describe", image_path=tiny_png)
        assert result == ""

    def test_analyze_image_describe_task(self, tiny_png: Path) -> None:
        model = _make_openai_vision_model()
        model.client.chat.completions.create.return_value = _openai_ok_response("Scenic view")
        result = model.analyze_image(tiny_png, task="describe")
        assert result == "Scenic view"

    def test_analyze_image_custom_prompt(self, tiny_png: Path) -> None:
        model = _make_openai_vision_model()
        model.client.chat.completions.create.return_value = _openai_ok_response("Custom")
        model.analyze_image(tiny_png, custom_prompt="Tell me everything")
        model.client.chat.completions.create.assert_called_once()

    def test_cleanup_sets_client_none(self) -> None:
        model = _make_openai_vision_model()
        model.client.close = MagicMock()
        model.cleanup()
        assert model.client is None
        assert model._initialized is False

    def test_get_default_config_returns_vision_type(self) -> None:
        from models.base import ModelType
        from models.openai_vision_model import OpenAIVisionModel

        config = OpenAIVisionModel.get_default_config()
        assert config.model_type == ModelType.VISION
        assert config.provider == "openai"


# ===========================================================================
# audio_model.py — AudioModel
# ===========================================================================


class TestAudioModel:
    def test_init_raises_value_error_for_wrong_type(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelConfig, ModelType

        config = ModelConfig(name="whisper", model_type=ModelType.TEXT)
        with pytest.raises(ValueError, match="AUDIO"):
            AudioModel(config)

    def test_init_succeeds_for_audio_type(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelType

        config = AudioModel.get_default_config()
        model = AudioModel(config)
        assert model.config.model_type == ModelType.AUDIO

    def test_initialize_sets_initialized_flag(self) -> None:
        from models.audio_model import AudioModel

        config = AudioModel.get_default_config()
        model = AudioModel(config)
        model.initialize()
        assert model._initialized is True

    def test_generate_propagates_filenotfound_for_missing_audio(
        self, tmp_path: Path
    ) -> None:
        # Step 2A wired generate() to faster-whisper. After initialize(),
        # passing a non-existent audio path now propagates the underlying
        # FileNotFoundError from AudioTranscriber.transcribe rather than
        # the previous NotImplementedError("Phase 3") stub.
        # Use tmp_path to construct a guaranteed-missing path so the
        # FileNotFoundError assertion stays reliable under xdist
        # parallelization (the bare "does-not-exist.wav" relative form is
        # vulnerable to a same-named file in the worker's cwd).
        from models.audio_model import AudioModel

        missing_audio = tmp_path / "does-not-exist.wav"

        config = AudioModel.get_default_config()
        model = AudioModel(config)
        model.initialize()
        try:
            with pytest.raises(FileNotFoundError):
                model.generate(str(missing_audio))
        finally:
            model.safe_cleanup()

    def test_cleanup_clears_initialized(self) -> None:
        from models.audio_model import AudioModel

        config = AudioModel.get_default_config()
        model = AudioModel(config)
        model.initialize()
        model.cleanup()
        assert model._initialized is False

    def test_get_default_config_returns_audio_type(self) -> None:
        from models.audio_model import AudioModel
        from models.base import ModelType

        config = AudioModel.get_default_config()
        assert config.model_type == ModelType.AUDIO

    def test_get_default_config_custom_model_name(self) -> None:
        from models.audio_model import AudioModel

        config = AudioModel.get_default_config("tiny-whisper")
        assert config.name == "tiny-whisper"

    def test_repr_includes_model_name(self) -> None:
        from models.audio_model import AudioModel

        config = AudioModel.get_default_config("test-model")
        model = AudioModel(config)
        assert "test-model" in repr(model)


# ===========================================================================
# audio_transcriber.py — AudioTranscriber
# ===========================================================================


class TestAudioTranscriber:
    @pytest.fixture(autouse=True)
    def _require_faster_whisper(self) -> None:
        pytest.importorskip("faster_whisper")

    def test_init_raises_import_error_when_unavailable(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        with (
            patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", False),
            pytest.raises(ImportError, match="faster-whisper"),
        ):
            AudioTranscriber()

    def test_init_succeeds_with_valid_params(self) -> None:
        from models.audio_transcriber import AudioTranscriber, ModelSize

        with patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True):
            t = AudioTranscriber(model_size=ModelSize.TINY, device="cpu")
        assert t.model_size == "tiny"
        assert t.device == "cpu"

    def test_init_raises_value_error_for_invalid_model_size(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        with (
            patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True),
            pytest.raises(ValueError, match="Invalid model size"),
        ):
            AudioTranscriber(model_size="invalid_size", device="cpu")

    def test_init_raises_value_error_for_invalid_compute_type(self) -> None:
        from models.audio_transcriber import AudioTranscriber, ModelSize

        with (
            patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True),
            pytest.raises(ValueError, match="Invalid compute type"),
        ):
            AudioTranscriber(model_size=ModelSize.TINY, device="cpu", compute_type="invalid")

    def test_get_supported_formats(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        formats = AudioTranscriber.get_supported_formats()
        assert "wav" in formats
        assert "mp3" in formats
        assert len(formats) >= 4

    def test_transcribe_raises_file_not_found(self, tmp_path: Path) -> None:
        from models.audio_transcriber import AudioTranscriber, ModelSize

        with patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True):
            t = AudioTranscriber(model_size=ModelSize.TINY, device="cpu")

        with pytest.raises(FileNotFoundError):
            t.transcribe(tmp_path / "missing.wav")

    def test_detect_language_raises_file_not_found(self, tmp_path: Path) -> None:
        from models.audio_transcriber import AudioTranscriber, ModelSize

        with patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True):
            t = AudioTranscriber(model_size=ModelSize.TINY, device="cpu")

        with pytest.raises(FileNotFoundError):
            t.detect_language(tmp_path / "missing.wav")

    def test_transcribe_returns_transcription_result(self, tmp_path: Path) -> None:
        from models.audio_transcriber import (
            AudioTranscriber,
            ModelSize,
            TranscriptionOptions,
            TranscriptionResult,
        )

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"RIFF" + b"\x00" * 40)

        # Build a mock segment
        mock_seg = MagicMock()
        mock_seg.text = "Hello world"
        mock_seg.start = 0.0
        mock_seg.end = 1.5
        mock_seg.avg_logprob = -0.3
        mock_seg.words = []

        mock_info = MagicMock()
        mock_info.language = "en"
        mock_info.language_probability = 0.99
        mock_info.duration = 1.5

        mock_whisper_model = MagicMock()
        mock_whisper_model.transcribe.return_value = (iter([mock_seg]), mock_info)

        with patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True):
            t = AudioTranscriber(model_size=ModelSize.TINY, device="cpu")

        t.model = mock_whisper_model
        t._model_loaded = True

        options = TranscriptionOptions(word_timestamps=False)
        result = t.transcribe(audio_file, options=options)

        assert isinstance(result, TranscriptionResult)
        assert result.text == "Hello world"
        assert result.language == "en"
        assert result.language_confidence == pytest.approx(0.99)

    def test_detect_language_returns_language_detection(self, tmp_path: Path) -> None:
        from models.audio_transcriber import (
            AudioTranscriber,
            LanguageDetection,
            ModelSize,
        )

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"RIFF" + b"\x00" * 40)

        mock_info = MagicMock()
        mock_info.language = "en"
        mock_info.language_probability = 0.95

        mock_whisper_model = MagicMock()
        mock_whisper_model.transcribe.return_value = (iter([]), mock_info)

        with patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True):
            t = AudioTranscriber(model_size=ModelSize.TINY, device="cpu")

        t.model = mock_whisper_model
        t._model_loaded = True

        result = t.detect_language(audio_file)
        assert isinstance(result, LanguageDetection)
        assert result.language == "en"
        assert result.language_name == "English"
        assert result.confidence == pytest.approx(0.95)

    def test_clear_cache_resets_state(self, tmp_path: Path) -> None:
        from models.audio_transcriber import AudioTranscriber, ModelSize

        with patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True):
            t = AudioTranscriber(model_size=ModelSize.TINY, device="cpu")

        t.model = MagicMock()
        t._model_loaded = True
        t.clear_cache()

        assert t.model is None
        assert t._model_loaded is False

    def test_clear_all_caches_empties_class_cache(self) -> None:
        from models.audio_transcriber import AudioTranscriber

        AudioTranscriber._model_cache["fake_key"] = MagicMock()
        AudioTranscriber.clear_all_caches()
        assert len(AudioTranscriber._model_cache) == 0

    def test_load_model_raises_runtime_error_on_failure(self) -> None:
        from models.audio_transcriber import AudioTranscriber, ModelSize

        with patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True):
            t = AudioTranscriber(model_size=ModelSize.TINY, device="cpu")

        with (
            patch(
                "models.audio_transcriber.WhisperModel",
                side_effect=RuntimeError("load failed"),
            ),
            pytest.raises(RuntimeError, match="Model loading failed"),
        ):
            t._load_model()

    def test_detect_device_returns_cpu_when_no_gpu(self) -> None:
        from models.audio_transcriber import AudioTranscriber, ModelSize

        with patch("models.audio_transcriber._FASTER_WHISPER_AVAILABLE", True):
            t = AudioTranscriber(model_size=ModelSize.TINY, device="cpu")

        # _detect_device with explicit "cpu" returns "cpu"
        assert t._detect_device("cpu") == "cpu"

    def test_model_size_enum_values(self) -> None:
        from models.audio_transcriber import ModelSize

        assert ModelSize.TINY.value == "tiny"
        assert ModelSize.BASE.value == "base"
        assert ModelSize.LARGE_V3.value == "large-v3"

    def test_compute_type_enum_values(self) -> None:
        from models.audio_transcriber import ComputeType

        assert ComputeType.FLOAT16.value == "float16"
        assert ComputeType.INT8.value == "int8"
        assert ComputeType.AUTO.value == "auto"
