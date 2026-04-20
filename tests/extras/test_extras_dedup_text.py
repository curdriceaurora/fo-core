"""Smoke canary for the dedup-text extra (scikit-learn)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def _require_dedup_text() -> None:
    pytest.importorskip("sklearn")


def test_sklearn_importable() -> None:
    from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401


def test_document_embedder_importable() -> None:
    from services.deduplication.embedder import DocumentEmbedder  # noqa: F401


def test_semantic_analyzer_importable() -> None:
    from services.deduplication.semantic import SemanticAnalyzer  # noqa: F401
