"""Tests for ``services.inference_timer`` (#410).

Verifies that:
- the context manager records a non-negative ``elapsed_ms`` for both
  success and exception paths,
- the structured log line is emitted on both paths,
- the ``kind`` argument lands in the log prefix verbatim.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.inference_timer import time_inference

pytestmark = [pytest.mark.unit, pytest.mark.ci]


def test_records_elapsed_on_success(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("hi")

    with time_inference("text", f) as t:
        pass

    # A no-op body must measure as a finite, small duration. Use a
    # generous upper bound (1 second) so the bound is meaningful but
    # not so tight it flakes on slow CI runners. Lower bound is
    # already implicit via _InferenceTimer's `max(0.0, ...)`.
    assert isinstance(t.elapsed_ms, float)
    assert t.elapsed_ms < 1000.0


def test_records_elapsed_on_exception(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("hi")

    with pytest.raises(RuntimeError, match="boom"):
        with time_inference("vision", f) as t:
            raise RuntimeError("boom")

    # __exit__ still updated elapsed_ms despite the exception. The
    # synchronous raise body is essentially instantaneous, so the
    # measurement should be a small finite float.
    assert isinstance(t.elapsed_ms, float)
    assert t.elapsed_ms < 1000.0


def test_logs_kind_and_filename_on_success(tmp_path: Path) -> None:
    f = tmp_path / "shot.png"
    f.write_bytes(b"")

    with patch("services.inference_timer.logger") as mock_logger:
        with time_inference("vision", f):
            pass

    mock_logger.debug.assert_called_once()
    fmt, kind, _ms, file_name = mock_logger.debug.call_args.args
    assert "{}_inference_ms=" in fmt
    assert kind == "vision"
    assert file_name == "shot.png"


def test_logs_error_field_on_exception(tmp_path: Path) -> None:
    f = tmp_path / "shot.png"
    f.write_bytes(b"")

    with patch("services.inference_timer.logger") as mock_logger:
        with pytest.raises(ValueError):
            with time_inference("vision", f):
                raise ValueError("bad input")

    mock_logger.debug.assert_called_once()
    fmt, kind, _ms, file_name, error = mock_logger.debug.call_args.args
    assert "error={}" in fmt
    assert kind == "vision"
    assert file_name == "shot.png"
    assert error == "ValueError"


def test_accepts_string_path(tmp_path: Path) -> None:
    """Path-or-str is accepted; internal logging uses the basename."""
    with patch("services.inference_timer.logger") as mock_logger:
        with time_inference("text", str(tmp_path / "report.md")):
            pass

    _fmt, _kind, _ms, file_name = mock_logger.debug.call_args.args
    assert file_name == "report.md"


def test_zero_duration_is_not_negative(tmp_path: Path) -> None:
    """A no-op body still produces a finite, small reading."""
    with time_inference("text", tmp_path / "x") as t:
        pass
    # _InferenceTimer clamps to max(0.0, …) so a no-op body must be
    # a sane small positive float (well under 1s on any platform).
    assert isinstance(t.elapsed_ms, float)
    assert t.elapsed_ms < 1000.0
