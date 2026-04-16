"""Integration tests for BM25 index.

Covers:
  - services/search/bm25_index.py — BM25Index, _tokenise
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.search.bm25_index import BM25Index, _tokenise

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# _tokenise
# ---------------------------------------------------------------------------


class TestTokenise:
    @pytest.fixture(autouse=True)
    def _require_rank_bm25(self) -> None:
        pytest.importorskip("rank_bm25")

    def test_basic_split(self) -> None:
        assert _tokenise("hello world") == ["hello", "world"]

    def test_lowercase(self) -> None:
        assert _tokenise("HELLO WORLD") == ["hello", "world"]

    def test_non_alphanumeric_stripped(self) -> None:
        result = _tokenise("file-name.txt")
        assert "file" in result
        assert "name" in result
        assert "txt" in result

    def test_empty_string(self) -> None:
        assert _tokenise("") == []

    def test_digits_preserved(self) -> None:
        result = _tokenise("report2024")
        assert "report2024" in result

    def test_multiple_separators(self) -> None:
        result = _tokenise("  hello   world  ")
        assert result == ["hello", "world"]


# ---------------------------------------------------------------------------
# BM25Index
# ---------------------------------------------------------------------------


class TestBM25IndexInit:
    @pytest.fixture(autouse=True)
    def _require_rank_bm25(self) -> None:
        pytest.importorskip("rank_bm25")

    def test_created(self) -> None:
        idx = BM25Index()
        assert idx is not None

    def test_initially_empty(self) -> None:
        idx = BM25Index()
        assert idx.size == 0

    def test_search_before_index_returns_empty(self) -> None:
        idx = BM25Index()
        assert idx.search("anything") == []


class TestBM25IndexIndex:
    @pytest.fixture(autouse=True)
    def _require_rank_bm25(self) -> None:
        pytest.importorskip("rank_bm25")

    def test_index_sets_size(self, tmp_path: Path) -> None:
        idx = BM25Index()
        paths = [tmp_path / f"f{i}.txt" for i in range(3)]
        idx.index(["doc one", "doc two", "doc three"], paths)
        assert idx.size == 3

    def test_mismatched_lengths_raises(self, tmp_path: Path) -> None:
        idx = BM25Index()
        with pytest.raises(ValueError, match="equal length"):
            idx.index(["a", "b"], [tmp_path / "only_one.txt"])

    def test_single_document(self, tmp_path: Path) -> None:
        idx = BM25Index()
        idx.index(["only one doc"], [tmp_path / "one.txt"])
        assert idx.size == 1

    def test_reindex_replaces_old(self, tmp_path: Path) -> None:
        idx = BM25Index()
        idx.index(["old doc"], [tmp_path / "old.txt"])
        idx.index(["new doc one", "new doc two"], [tmp_path / "a.txt", tmp_path / "b.txt"])
        assert idx.size == 2


class TestBM25IndexSearch:
    @pytest.fixture(autouse=True)
    def _require_rank_bm25(self) -> None:
        pytest.importorskip("rank_bm25")

    def test_returns_list(self, tmp_path: Path) -> None:
        idx = BM25Index()
        idx.index(["finance quarterly report"], [tmp_path / "report.txt"])
        result = idx.search("finance")
        # 1 indexed doc matching the query → 1 result tuple
        assert len(result) == 1
        path, score = result[0]
        assert path == tmp_path / "report.txt"

    def test_relevant_doc_returned(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"d{i}.txt" for i in range(6)]
        docs = [
            "quarterly finance invoice payment",
            "cooking pasta dinner recipes",
            "project management planning",
            "music concerts events",
            "sports news results",
            "travel destinations tourism",
        ]
        idx = BM25Index()
        idx.index(docs, paths)
        results = idx.search("finance")
        assert len(results) >= 1
        result_paths = [p for p, _ in results]
        assert paths[0] in result_paths

    def test_top_k_limit(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"doc{i}.txt" for i in range(10)]
        docs = [f"finance report document {i}" for i in range(10)]
        idx = BM25Index()
        idx.index(docs, paths)
        results = idx.search("finance", top_k=3)
        assert len(results) == 3

    def test_scores_are_floats(self, tmp_path: Path) -> None:
        idx = BM25Index()
        idx.index(["finance report"], [tmp_path / "f.txt"])
        results = idx.search("finance")
        assert len(results) >= 1
        for _, score in results:
            assert isinstance(score, float)

    def test_empty_query_returns_empty(self, tmp_path: Path) -> None:
        idx = BM25Index()
        idx.index(["finance report"], [tmp_path / "f.txt"])
        assert idx.search("") == []

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        idx = BM25Index()
        idx.index(["finance report"], [tmp_path / "f.txt"])
        result = idx.search("xyzzy")
        assert result == []

    def test_results_sorted_descending(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"d{i}.txt" for i in range(5)]
        docs = [
            "finance finance finance report",
            "finance invoice",
            "meeting notes project",
            "finance",
            "cooking recipe",
        ]
        idx = BM25Index()
        idx.index(docs, paths)
        results = idx.search("finance", top_k=5)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)
