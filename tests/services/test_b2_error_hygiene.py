"""Regression guards for Epic B.errors (B2) error-boundary hygiene.

Per §3 B2 of the hardening roadmap: replace bare excepts and silent
fallbacks with typed / categorized handling; log exception type and
category. These tests verify the invariant at each of the three
enumerated sites so a regression that drops the category from the log
message (or swallows the exception without logging type) would fail.

Covered sites:

- ``src/services/vision_processor.py`` (image-processing failure path)
- ``src/services/text_processor.py`` (description / folder-name /
  filename generation failure paths)
- ``src/models/model_manager.py`` (ollama CLI not found / timeout
  branches — console output is already present; B2 adds structured
  logger lines carrying the exception type)
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.base import ModelType
from services.text_processor import TextProcessor

pytestmark = [pytest.mark.ci, pytest.mark.unit, pytest.mark.integration]


# ---------------------------------------------------------------------------
# text_processor.py error paths — already-typed except tuples, B2 adds
# exception type to the log message using loguru's {} interpolation
# (f-strings in logger calls are a G2 violation — pre-format the
# message before loguru sees it, losing structured capture).
# ---------------------------------------------------------------------------


class TestTextProcessorErrorLogsIncludeType:
    """Each typed ``except`` in TextProcessor's three generate_* methods
    must log the exception class name and the error. Without the type
    tag, operators can't distinguish model-provider failures
    (``RuntimeError``) from filesystem surface (``OSError``) from
    attribute errors on an uninitialised model (``AttributeError``).
    """

    @pytest.fixture
    def mock_model(self) -> MagicMock:
        model = MagicMock()
        model.config.model_type = ModelType.TEXT
        model.is_initialized = True
        return model

    def _log_strings(self, mock_logger: MagicMock) -> str:
        """Join every ``logger.error`` / ``.exception`` call (template + args).

        Renders both positional args AND kwargs values so a future
        refactor from positional ``logger.error("t=%s", type(e).__name__)``
        to keyword ``logger.error("t=%s", exc_type=type(e).__name__)``
        doesn't silently make the regression guard vacuous (coderabbit
        PRRT_kwDOR_Rkws59NYZ9).
        """
        calls = list(mock_logger.error.call_args_list) + list(mock_logger.exception.call_args_list)
        rendered: list[str] = []
        for call in calls:
            args, kwargs = call
            rendered.append(" ".join(str(a) for a in args))
            rendered.extend(f"{k}={v}" for k, v in kwargs.items())
        return "\n".join(rendered)

    def _assert_template_has_type_tag(self, mock_logger: MagicMock) -> None:
        """Verify at least one log call's template contains the literal
        ``type=`` tag, so a regression that drops the categorisation
        token (even while keeping the type in an arg) would fail
        (coderabbit PRRT_kwDOR_Rkws59NYZ9).
        """
        calls = list(mock_logger.error.call_args_list) + list(mock_logger.exception.call_args_list)
        templates = [str(c.args[0]) for c in calls if c.args]
        assert any("type=" in t for t in templates), (
            f"no log template carries the 'type=' categorisation tag; got: {templates!r}"
        )

    def test_generate_description_logs_exception_type(self, mock_model: MagicMock) -> None:
        mock_model.generate.side_effect = RuntimeError("llama server unreachable")
        processor = TextProcessor(text_model=mock_model)

        with patch("services.text_processor.logger") as mock_logger:
            result = processor._generate_description("some content")

        # Fallback contract preserved.
        assert result.startswith("Content about")
        joined = self._log_strings(mock_logger)
        assert "RuntimeError" in joined, (
            f"expected log to include exception type 'RuntimeError', got: {joined!r}"
        )
        assert "llama server unreachable" in joined
        self._assert_template_has_type_tag(mock_logger)

    def test_generate_folder_name_logs_exception_type(self, mock_model: MagicMock) -> None:
        mock_model.generate.side_effect = OSError("model pipe broken")
        processor = TextProcessor(text_model=mock_model)

        with patch("services.text_processor.logger") as mock_logger:
            result = processor._generate_folder_name("irrelevant text")

        assert result == "documents"  # documented fallback
        joined = self._log_strings(mock_logger)
        assert "OSError" in joined, (
            f"expected log to include exception type 'OSError', got: {joined!r}"
        )
        self._assert_template_has_type_tag(mock_logger)

    def test_generate_filename_logs_exception_type(self, mock_model: MagicMock) -> None:
        mock_model.generate.side_effect = AttributeError("model not initialized")
        processor = TextProcessor(text_model=mock_model)

        with patch("services.text_processor.logger") as mock_logger:
            result = processor._generate_filename("text")

        # Fallback filename is non-empty (documented contract — see
        # _generate_filename's exception handler).
        assert result
        joined = self._log_strings(mock_logger)
        assert "AttributeError" in joined, (
            f"expected log to include exception type 'AttributeError', got: {joined!r}"
        )
        self._assert_template_has_type_tag(mock_logger)


# ---------------------------------------------------------------------------
# model_manager.py — typed FileNotFoundError / subprocess.TimeoutExpired
# already caught; B2 adds a structured ``logger.*`` call alongside the
# ``console.print`` so the failure appears in the log stream with the
# exception type (not just the user-facing console).
# ---------------------------------------------------------------------------


class TestModelManagerPullLogsTypedErrors:
    """``ModelManager.pull`` surfaces ollama CLI failures to the user via
    ``console.print`` but — pre-B2 — left no ``logger.*`` record. When
    operators review logs (the aggregated source of truth across runs),
    a quiet failure looked like no activity at all. B2 adds a
    structured log line carrying the exception type so the failure is
    visible in both channels.
    """

    @pytest.fixture
    def manager(self) -> object:
        from models.model_manager import ModelManager

        mgr = ModelManager()
        # Swap the Console for a silent MagicMock so stdout isn't
        # littered with ANSI codes during the test run.
        mgr._console = MagicMock()
        return mgr

    def test_pull_ollama_not_found_logs_type(
        self, manager: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch(
            "models.model_manager.subprocess.run",
            side_effect=FileNotFoundError("ollama: not found"),
        ):
            with caplog.at_level(logging.ERROR):
                ok = manager.pull_model("qwen2.5:3b")

        assert ok is False
        joined = "\n".join(
            str(r.msg) + " " + " ".join(str(a) for a in r.args or ()) for r in caplog.records
        )
        assert "FileNotFoundError" in joined, (
            f"expected log to include 'FileNotFoundError', got: {joined!r}"
        )

    def test_pull_ollama_timeout_logs_type(
        self, manager: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        timeout = subprocess.TimeoutExpired(cmd="ollama pull x", timeout=10)
        with patch("models.model_manager.subprocess.run", side_effect=timeout):
            with caplog.at_level(logging.ERROR):
                ok = manager.pull_model("qwen2.5:3b")

        assert ok is False
        joined = "\n".join(
            str(r.msg) + " " + " ".join(str(a) for a in r.args or ()) for r in caplog.records
        )
        assert "TimeoutExpired" in joined, (
            f"expected log to include 'TimeoutExpired', got: {joined!r}"
        )


# ---------------------------------------------------------------------------
# vision_processor.py — the bare ``except Exception`` is load-bearing
# (organizer iterates many images; one bad file must not crash the
# pipeline). B2 fix: enrich the log message with exception type so the
# category is visible even when the catch remains broad.
# ---------------------------------------------------------------------------


class TestVisionProcessorErrorLogsIncludeType:
    """Regression: the catch-all in ``VisionProcessor.process_image``
    must log ``type(exc).__name__`` so a stream of vision failures can
    be bucketed by operators without digging into per-record
    tracebacks.
    """

    def test_process_image_failure_logs_exception_type(self, tmp_path: Path) -> None:
        from models.base import ModelType
        from services.vision_processor import VisionProcessor

        sample = tmp_path / "x.jpg"
        sample.write_bytes(b"fake jpg")

        # Inject a mock vision model so the constructor succeeds; then
        # stub ``_generate_description`` to raise so the exception
        # reaches the outer try/except in ``process_file`` (the B2
        # site at src/services/vision_processor.py:208).
        mock_model = MagicMock()
        mock_model.config.model_type = ModelType.VISION
        mock_model.is_initialized = True

        processor = VisionProcessor(vision_model=mock_model)
        # Defensively close the circuit under its real lock (the
        # constructor already leaves it closed; this only matters if
        # future fixture/module-state leaks trip it). Attribute names
        # match ``VisionProcessor.__init__`` — ``_circuit_opened_at``
        # is the gating field (see ``_is_circuit_open``) (coderabbit
        # PRRT_kwDOR_Rkws59NYaB).
        with processor._circuit_lock:
            processor._circuit_opened_at = None
            processor._circuit_reason = None

        with (
            patch.object(
                processor,
                "_generate_description",
                side_effect=RuntimeError("vision inference failed"),
            ),
            patch("services.vision_processor.logger") as mock_logger,
        ):
            result = processor.process_file(sample)

        # Fallback contract: returns an error-tagged ProcessedImage rather
        # than raising — the organizer keeps going.
        assert result.folder_name == "errors"
        rendered: list[str] = []
        for call in list(mock_logger.error.call_args_list) + list(
            mock_logger.exception.call_args_list
        ):
            args, _kwargs = call
            rendered.append(" ".join(str(a) for a in args))
        joined = "\n".join(rendered)
        assert "RuntimeError" in joined, f"expected log to include 'RuntimeError', got: {joined!r}"
        # ``ProcessedImage.error`` preserves its existing contract of
        # carrying just ``str(e)`` — the type categorisation lives in
        # the log message only, where operators consume it. Downstream
        # reporters that key off the error field are undisturbed.
        assert "vision inference failed" in (result.error or "")
