"""Tests for core.error_taxonomy.classify_error (#411).

Each ``ErrorCategory`` bucket gets a dedicated positive test, plus
None for clean / happy-path results so the summary renderer can skip
non-failures cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.error_taxonomy import RECOMMENDATIONS, classify_error

pytestmark = pytest.mark.unit


@dataclass
class _Stub:
    error: str | None = None
    source: str | None = None
    confidence: float = 1.0


class TestClassifyError:
    def test_clean_result_returns_none(self) -> None:
        assert classify_error(_Stub(error=None, source="vision")) is None

    def test_vision_timeout_via_fallback_source(self) -> None:
        # #406 path: dispatcher rescued the file via the EXIF fallback,
        # error is None but source carries the bucket signal.
        assert classify_error(_Stub(error=None, source="fallback_exif")) == "vision_timeout"
        assert classify_error(_Stub(error=None, source="fallback_filename")) == "vision_timeout"

    def test_vision_timeout_via_error_string(self) -> None:
        # Dispatcher's "timed out after Ns" sentinel — vision file
        # whose error survived without the fallback patching `source`.
        assert (
            classify_error(_Stub(error="Timed out after 30s", confidence=0.0)) == "vision_timeout"
        )

    def test_read_error_permission_denied(self) -> None:
        assert (
            classify_error(_Stub(error="Permission denied: /etc/shadow", confidence=0.0))
            == "read_error"
        )

    def test_read_error_symlink_rejection(self) -> None:
        assert (
            classify_error(_Stub(error="SafeDir refused to read symlink", confidence=0.0))
            == "read_error"
        )

    def test_read_error_reader_failed_to_read(self) -> None:
        # FileReadError messages emitted by every reader in
        # utils/readers/ follow the "Failed to read <FORMAT>..."
        # template (DOCX, PDF, RTF, ebook, ...). Previously these
        # bucketed into inference_error because the token list
        # didn't catch them — CodeRabbit P2 catch on PR #427.
        for msg in (
            "Failed to read DOCX file foo.docx: parser error",
            "Failed to read PDF file bar.pdf: encrypted",
            "Failed to read RTF baz.rtf: invalid header",
            "Failed to read ebook file qux.epub: missing manifest",
        ):
            assert classify_error(_Stub(error=msg, confidence=0.0)) == "read_error", msg

    def test_read_error_size_cap(self) -> None:
        assert (
            classify_error(_Stub(error="exceeds max_file_size of 100MB", confidence=0.0))
            == "read_error"
        )

    def test_unsupported_type(self) -> None:
        assert (
            classify_error(_Stub(error="Unsupported file type: .xyz", confidence=0.0))
            == "unsupported_type"
        )

    def test_inference_error_via_zero_confidence(self) -> None:
        # Provider 500 / AttributeError surfaced as confidence==0.0
        # with an error that doesn't match the filesystem patterns.
        assert (
            classify_error(_Stub(error="ollama returned HTTP 500", confidence=0.0))
            == "inference_error"
        )

    def test_other_bucket_catches_unknown_errors(self) -> None:
        # Non-zero confidence + unfamiliar error wording — bucket as `other`.
        assert classify_error(_Stub(error="ghost in the machine", confidence=0.85)) == "other"

    def test_duck_typed_object_without_source_attr(self) -> None:
        # Older ProcessedFile shapes lack `source`; helper must tolerate it.
        class _NoSource:
            error = "Permission denied"
            confidence = 0.0

        assert classify_error(_NoSource()) == "read_error"


class TestRecommendations:
    def test_every_category_has_a_recommendation(self) -> None:
        # Acceptance criterion: each surfaced bucket carries an
        # actionable next-step string for the >10% trigger.
        for category in (
            "vision_timeout",
            "read_error",
            "unsupported_type",
            "inference_error",
            "other",
        ):
            tip = RECOMMENDATIONS[category]  # type: ignore[index]
            assert isinstance(tip, str)
            assert len(tip) > 10  # non-trivial guidance
