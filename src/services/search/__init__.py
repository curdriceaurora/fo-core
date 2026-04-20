"""Search services: BM25 index, vector index, embedding cache, and hybrid retriever."""

# HybridRetriever requires numpy (via VectorIndex). Guard so a default
# install without the search extra can still import this package.
try:
    from services.search.hybrid_retriever import HybridRetriever, read_text_safe
except ImportError:
    pass  # numpy not available; search subsystem disabled

__all__ = ["HybridRetriever", "read_text_safe"]
