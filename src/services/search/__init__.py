"""Search services: BM25 index, vector index, embedding cache, and hybrid retriever."""

import logging

logger = logging.getLogger(__name__)

# HybridRetriever requires numpy (via VectorIndex). Guard so a default
# install without the search extra can still import this package.
try:
    from services.search.hybrid_retriever import HybridRetriever, read_text_safe
except ImportError as e:
    logger.debug("services.search disabled (missing optional dep): %s", e)
    HybridRetriever = None  # type: ignore[assignment,misc]
    read_text_safe = None  # type: ignore[assignment]

__all__ = ["HybridRetriever", "read_text_safe"]
