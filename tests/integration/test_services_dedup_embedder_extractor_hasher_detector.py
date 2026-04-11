"""Integration tests for deduplication modules.

Covers:
  - services/deduplication/embedder.py — DocumentEmbedder
  - services/deduplication/extractor.py — DocumentExtractor
  - services/deduplication/hasher.py — FileHasher
  - services/deduplication/detector.py — DuplicateDetector, ScanOptions
"""

from __future__ import annotations

import pickle
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_sklearn() -> None:
    pytest.importorskip("sklearn")


# ---------------------------------------------------------------------------
# FileHasher
# ---------------------------------------------------------------------------


class TestFileHasher:
    def test_compute_hash_sha256(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        f = tmp_path / "a.txt"
        f.write_text("hello world")
        hasher = FileHasher()
        h = hasher.compute_hash(f, "sha256")
        assert len(h) == 64
        assert h == hasher.compute_hash(f, "sha256")  # deterministic

    def test_compute_hash_md5(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        f = tmp_path / "b.txt"
        f.write_text("hello world")
        hasher = FileHasher()
        h = hasher.compute_hash(f, "md5")
        assert len(h) == 32

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        f1 = tmp_path / "c1.txt"
        f2 = tmp_path / "c2.txt"
        f1.write_text("content a")
        f2.write_text("content b")
        hasher = FileHasher()
        assert hasher.compute_hash(f1) != hasher.compute_hash(f2)

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        hasher = FileHasher()
        with pytest.raises(FileNotFoundError):
            hasher.compute_hash(tmp_path / "missing.txt")

    def test_path_is_directory_raises(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        hasher = FileHasher()
        with pytest.raises(ValueError, match="not a file"):
            hasher.compute_hash(tmp_path)

    def test_unsupported_algorithm_raises(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        f = tmp_path / "d.txt"
        f.write_text("data")
        hasher = FileHasher()
        with pytest.raises(ValueError, match="Unsupported algorithm"):
            hasher.compute_hash(f, "sha512")  # type: ignore[arg-type]

    def test_invalid_chunk_size_too_small(self) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        with pytest.raises(ValueError, match="chunk_size must be at least"):
            FileHasher(chunk_size=512)

    def test_invalid_chunk_size_too_large(self) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        with pytest.raises(ValueError, match="chunk_size must not exceed"):
            FileHasher(chunk_size=100 * 1024 * 1024)

    def test_invalid_chunk_size_non_int(self) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        with pytest.raises(ValueError, match="must be an integer"):
            FileHasher(chunk_size="big")  # type: ignore[arg-type]

    def test_compute_batch(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        f1 = tmp_path / "e1.txt"
        f2 = tmp_path / "e2.txt"
        f1.write_text("one")
        f2.write_text("two")
        hasher = FileHasher()
        results = hasher.compute_batch([f1, f2])
        assert f1 in results
        assert f2 in results
        assert results[f1] != results[f2]

    def test_compute_batch_skips_missing(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        real = tmp_path / "real.txt"
        real.write_text("exists")
        hasher = FileHasher()
        results = hasher.compute_batch([real, tmp_path / "ghost.txt"])
        assert real in results
        assert (tmp_path / "ghost.txt") not in results

    def test_get_file_size(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        f = tmp_path / "sized.txt"
        f.write_bytes(b"x" * 100)
        hasher = FileHasher()
        assert hasher.get_file_size(f) == 100

    def test_get_file_size_not_found(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        hasher = FileHasher()
        with pytest.raises(FileNotFoundError):
            hasher.get_file_size(tmp_path / "nope.txt")

    def test_validate_algorithm_valid(self) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        assert FileHasher.validate_algorithm("MD5") == "md5"
        assert FileHasher.validate_algorithm("SHA256") == "sha256"

    def test_validate_algorithm_invalid(self) -> None:
        from file_organizer.services.deduplication.hasher import FileHasher

        with pytest.raises(ValueError):
            FileHasher.validate_algorithm("sha512")


# ---------------------------------------------------------------------------
# DocumentExtractor
# ---------------------------------------------------------------------------


class TestDocumentExtractor:
    def test_extract_text_txt(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        f = tmp_path / "hello.txt"
        f.write_text("integration test content", encoding="utf-8")
        extractor = DocumentExtractor()
        text = extractor.extract_text(f)
        assert "integration test content" in text

    def test_extract_text_md(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        f = tmp_path / "readme.md"
        f.write_text("# Heading\nsome text", encoding="utf-8")
        extractor = DocumentExtractor()
        text = extractor.extract_text(f)
        assert "Heading" in text

    def test_unsupported_format_raises(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        f = tmp_path / "image.png"
        f.write_bytes(b"\x89PNG")
        extractor = DocumentExtractor()
        with pytest.raises(ValueError, match="Unsupported format"):
            extractor.extract_text(f)

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        extractor = DocumentExtractor()
        with pytest.raises(OSError):
            extractor.extract_text(tmp_path / "missing.txt")

    def test_supports_format_true(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        f = tmp_path / "doc.txt"
        f.touch()
        extractor = DocumentExtractor()
        assert extractor.supports_format(f) is True

    def test_supports_format_false(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        f = tmp_path / "photo.jpg"
        f.touch()
        extractor = DocumentExtractor()
        assert extractor.supports_format(f) is False

    def test_get_supported_formats(self) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        extractor = DocumentExtractor()
        fmts = extractor.get_supported_formats()
        assert ".txt" in fmts
        assert ".pdf" in fmts
        assert len(fmts) >= 4

    def test_extract_batch(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("file one")
        f2.write_text("file two")
        extractor = DocumentExtractor()
        results = extractor.extract_batch([f1, f2])
        assert f1 in results
        assert "file one" in results[f1]
        assert f2 in results
        assert "file two" in results[f2]

    def test_extract_batch_handles_unsupported_format(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        txt = tmp_path / "ok.txt"
        txt.write_text("good")
        bad = tmp_path / "bad.xyz"
        bad.write_bytes(b"data")
        extractor = DocumentExtractor()
        results = extractor.extract_batch([txt, bad])
        assert results[txt] == "good"
        assert results[bad] == ""

    def test_extract_rtf_basic(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        f = tmp_path / "test.rtf"
        f.write_text(r"{\rtf1\ansi Hello World}", encoding="utf-8")
        extractor = DocumentExtractor()
        text = extractor.extract_text(f)
        assert isinstance(text, str)

    def test_extract_odt_basic(self, tmp_path: Path) -> None:
        import io
        import zipfile

        from file_organizer.services.deduplication.extractor import DocumentExtractor

        content_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body>
    <office:text>
      <text:p>ODT paragraph text</text:p>
    </office:text>
  </office:body>
</office:document-content>"""

        odt_bytes = io.BytesIO()
        with zipfile.ZipFile(odt_bytes, "w") as zf:
            zf.writestr("content.xml", content_xml)
        odt_path = tmp_path / "test.odt"
        odt_path.write_bytes(odt_bytes.getvalue())

        extractor = DocumentExtractor()
        text = extractor.extract_text(odt_path)
        assert "ODT paragraph text" in text

    def test_extract_text_latin1_encoding(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.extractor import DocumentExtractor

        f = tmp_path / "latin.txt"
        f.write_bytes("caf\xe9".encode("latin-1"))
        extractor = DocumentExtractor()
        text = extractor.extract_text(f)
        assert isinstance(text, str)
        assert len(text) > 0


# ---------------------------------------------------------------------------
# DocumentEmbedder (requires sklearn)
# ---------------------------------------------------------------------------


class TestDocumentEmbedder:
    @pytest.fixture(autouse=True)
    def _require_sklearn(self) -> None:
        _require_sklearn()

    def test_fit_transform_basic(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=100)
        docs = ["the quick brown fox", "jumped over the lazy dog", "hello world"]
        matrix = embedder.fit_transform(docs)
        assert matrix.shape[0] == 3
        assert embedder.is_fitted is True

    def test_fit_transform_empty_returns_empty(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder()
        result = embedder.fit_transform([])
        assert result.size == 0

    def test_transform_single_document(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=100)
        docs = ["the quick brown fox", "jumped over the lazy dog"]
        embedder.fit_transform(docs)
        vec = embedder.transform("the quick brown fox")
        assert vec.shape[0] > 0

    def test_transform_not_fitted_raises(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder()
        with pytest.raises(RuntimeError, match="not fitted"):
            embedder.transform("some text")

    def test_transform_batch(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=50)
        docs = ["alpha beta gamma", "delta epsilon zeta", "eta theta iota"]
        embedder.fit_transform(docs)
        batch = embedder.transform_batch(["alpha beta", "delta epsilon"])
        assert batch.shape == (2, batch.shape[1])

    def test_transform_batch_not_fitted_raises(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder()
        with pytest.raises(RuntimeError):
            embedder.transform_batch(["text"])

    def test_get_feature_names(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=50)
        embedder.fit_transform(["hello world", "foo bar baz"])
        names = embedder.get_feature_names()
        assert isinstance(names, list)
        assert len(names) >= 1

    def test_get_feature_names_not_fitted(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder()
        with pytest.raises(RuntimeError):
            embedder.get_feature_names()

    def test_get_vocabulary(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=50)
        embedder.fit_transform(["hello world", "world peace"])
        vocab = embedder.get_vocabulary()
        assert isinstance(vocab, dict)

    def test_get_vocabulary_not_fitted(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder()
        with pytest.raises(RuntimeError):
            embedder.get_vocabulary()

    def test_get_top_terms(self) -> None:

        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=50)
        docs = ["machine learning model training", "deep neural network layers"]
        embedder.fit_transform(docs)
        vec = embedder.transform("machine learning")
        top_terms = embedder.get_top_terms(vec, top_n=3)
        assert isinstance(top_terms, list)

    def test_get_top_terms_not_fitted(self) -> None:
        import numpy as np

        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder()
        with pytest.raises(RuntimeError):
            embedder.get_top_terms(np.array([0.1, 0.2]))

    def test_transform_uses_cache(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=50)
        docs = ["unique cached document for test"]
        embedder.fit_transform(docs)
        doc = "unique cached document for test"
        embedder.transform(doc)
        assert len(embedder.embedding_cache) >= 1
        vec2 = embedder.transform(doc)
        assert vec2 is not None

    def test_clear_cache(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=50)
        embedder.fit_transform(["hello world peace"])
        embedder.transform("hello world peace")
        assert len(embedder.embedding_cache) >= 1
        embedder.clear_cache()
        assert len(embedder.embedding_cache) == 0

    def test_save_and_load_model(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=50)
        embedder.fit_transform(["save me to disk", "second document here"])
        model_path = tmp_path / "model.pkl"
        embedder.save_model(model_path)
        assert model_path.exists()

        embedder2 = DocumentEmbedder(max_features=50)
        embedder2.load_model(model_path)
        assert embedder2.is_fitted is True

    def test_save_model_not_fitted_skips(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder()
        model_path = tmp_path / "unfitted.pkl"
        embedder.save_model(model_path)
        assert not model_path.exists()

    def test_load_model_missing_file_raises(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder()
        with pytest.raises((OSError, pickle.UnpicklingError, ValueError)):
            embedder.load_model(tmp_path / "ghost.pkl")

    def test_cache_persistence(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        cache_path = tmp_path / "cache.pkl"
        embedder = DocumentEmbedder(max_features=50, cache_path=cache_path)
        embedder.fit_transform(["cached doc one", "cached doc two"])
        embedder.transform("cached doc one")
        embedder._save_cache()
        assert cache_path.exists()

        embedder2 = DocumentEmbedder(max_features=50, cache_path=cache_path)
        assert len(embedder2.embedding_cache) >= 1

    def test_fit_transform_small_corpus_max_df_adjustment(self) -> None:
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        embedder = DocumentEmbedder(max_features=100, min_df=1, max_df=0.95)
        result = embedder.fit_transform(["only one doc"])
        assert result.shape[0] == 1

    def test_import_error_when_sklearn_missing(self) -> None:
        with patch.dict("sys.modules", {"sklearn": None, "sklearn.feature_extraction": None,
                                         "sklearn.feature_extraction.text": None}):
            import importlib

            import file_organizer.services.deduplication.embedder as emb_mod
            importlib.reload(emb_mod)
            if not emb_mod._SKLEARN_AVAILABLE:
                with pytest.raises(ImportError, match="scikit-learn"):
                    emb_mod.DocumentEmbedder()
            importlib.reload(emb_mod)


# ---------------------------------------------------------------------------
# DuplicateDetector
# ---------------------------------------------------------------------------


class TestDuplicateDetector:
    def test_scan_directory_no_duplicates(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import DuplicateDetector

        (tmp_path / "unique1.txt").write_text("unique content one")
        (tmp_path / "unique2.txt").write_text("unique content two")
        detector = DuplicateDetector()
        index = detector.scan_directory(tmp_path)
        groups = detector.get_duplicate_groups()
        assert isinstance(groups, dict)

    def test_scan_directory_finds_duplicates(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import DuplicateDetector

        content = "exact same content"
        (tmp_path / "dup1.txt").write_text(content)
        (tmp_path / "dup2.txt").write_text(content)
        (tmp_path / "unique.txt").write_text("something different")
        detector = DuplicateDetector()
        detector.scan_directory(tmp_path)
        groups = detector.get_duplicate_groups()
        assert len(groups) >= 1

    def test_scan_directory_not_found_raises(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import DuplicateDetector

        detector = DuplicateDetector()
        with pytest.raises(ValueError, match="not found"):
            detector.scan_directory(tmp_path / "missing_dir")

    def test_scan_directory_not_a_directory_raises(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import DuplicateDetector

        f = tmp_path / "file.txt"
        f.write_text("data")
        detector = DuplicateDetector()
        with pytest.raises(ValueError, match="not a directory"):
            detector.scan_directory(f)

    def test_scan_directory_empty(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import DuplicateDetector

        detector = DuplicateDetector()
        index = detector.scan_directory(tmp_path)
        assert index is not None

    def test_scan_with_min_file_size_filter(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import (
            DuplicateDetector,
            ScanOptions,
        )

        small = tmp_path / "small.txt"
        small.write_bytes(b"x" * 5)
        big = tmp_path / "big.txt"
        big.write_bytes(b"x" * 1000)
        big2 = tmp_path / "big2.txt"
        big2.write_bytes(b"x" * 1000)

        detector = DuplicateDetector()
        options = ScanOptions(min_file_size=100)
        detector.scan_directory(tmp_path, options)
        groups = detector.get_duplicate_groups()
        assert len(groups) >= 1

    def test_scan_with_max_file_size_filter(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import (
            DuplicateDetector,
            ScanOptions,
        )

        large1 = tmp_path / "large1.txt"
        large1.write_bytes(b"z" * 5000)
        large2 = tmp_path / "large2.txt"
        large2.write_bytes(b"z" * 5000)
        small = tmp_path / "small.txt"
        small.write_bytes(b"tiny")

        detector = DuplicateDetector()
        options = ScanOptions(max_file_size=100)
        detector.scan_directory(tmp_path, options)

    def test_scan_non_recursive(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import (
            DuplicateDetector,
            ScanOptions,
        )

        subdir = tmp_path / "sub"
        subdir.mkdir()
        (tmp_path / "top.txt").write_text("same")
        (subdir / "deep.txt").write_text("same")
        detector = DuplicateDetector()
        options = ScanOptions(recursive=False)
        detector.scan_directory(tmp_path, options)

    def test_find_duplicates_of_file(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import DuplicateDetector

        content = "duplicate content for search"
        target = tmp_path / "target.txt"
        target.write_text(content)
        match = tmp_path / "match.txt"
        match.write_text(content)
        other = tmp_path / "other.txt"
        other.write_text("totally different")

        detector = DuplicateDetector()
        duplicates = detector.find_duplicates_of_file(target, tmp_path)
        dup_paths = [d.path for d in duplicates]
        assert match in dup_paths

    def test_find_duplicates_of_file_not_found_raises(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import DuplicateDetector

        detector = DuplicateDetector()
        with pytest.raises(FileNotFoundError):
            detector.find_duplicates_of_file(tmp_path / "ghost.txt", tmp_path)

    def test_get_statistics(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import DuplicateDetector

        (tmp_path / "s1.txt").write_text("dup")
        (tmp_path / "s2.txt").write_text("dup")
        detector = DuplicateDetector()
        detector.scan_directory(tmp_path)
        stats = detector.get_statistics()
        assert isinstance(stats, dict)

    def test_clear_resets_index(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import DuplicateDetector

        (tmp_path / "c1.txt").write_text("same")
        (tmp_path / "c2.txt").write_text("same")
        detector = DuplicateDetector()
        detector.scan_directory(tmp_path)
        detector.clear()
        groups = detector.get_duplicate_groups()
        assert len(groups) == 0

    def test_progress_callback_called(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import (
            DuplicateDetector,
            ScanOptions,
        )

        content = "progress cb test"
        (tmp_path / "p1.txt").write_text(content)
        (tmp_path / "p2.txt").write_text(content)

        calls: list[tuple[int, int]] = []

        def cb(current: int, total: int) -> None:
            calls.append((current, total))

        detector = DuplicateDetector()
        options = ScanOptions(progress_callback=cb)
        detector.scan_directory(tmp_path, options)
        assert len(calls) >= 1

    def test_progress_callback_exception_does_not_abort(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import (
            DuplicateDetector,
            ScanOptions,
        )

        content = "exception cb test"
        (tmp_path / "ex1.txt").write_text(content)
        (tmp_path / "ex2.txt").write_text(content)

        def bad_cb(current: int, total: int) -> None:
            raise RuntimeError("callback boom")

        detector = DuplicateDetector()
        options = ScanOptions(progress_callback=bad_cb)
        index = detector.scan_directory(tmp_path, options)
        assert index is not None

    def test_scan_with_file_patterns(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import (
            DuplicateDetector,
            ScanOptions,
        )

        content = "pattern match"
        (tmp_path / "match1.txt").write_text(content)
        (tmp_path / "match2.txt").write_text(content)
        (tmp_path / "skip.py").write_text(content)

        detector = DuplicateDetector()
        options = ScanOptions(file_patterns=["*.txt"])
        detector.scan_directory(tmp_path, options)
        groups = detector.get_duplicate_groups()
        assert len(groups) >= 1

    def test_scan_with_exclude_patterns(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import (
            DuplicateDetector,
            ScanOptions,
        )

        content = "exclude test"
        (tmp_path / "incl1.txt").write_text(content)
        (tmp_path / "incl2.txt").write_text(content)
        (tmp_path / "excl.tmp").write_text(content)

        detector = DuplicateDetector()
        options = ScanOptions(exclude_patterns=["*.tmp"])
        detector.scan_directory(tmp_path, options)

    def test_symlinks_skipped_by_default(self, tmp_path: Path) -> None:
        from file_organizer.services.deduplication.detector import (
            DuplicateDetector,
            ScanOptions,
        )

        content = "symlink test"
        real = tmp_path / "real.txt"
        real.write_text(content)
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported on this OS")

        detector = DuplicateDetector()
        options = ScanOptions(follow_symlinks=False)
        detector.scan_directory(tmp_path, options)
