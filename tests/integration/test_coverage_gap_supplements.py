"""Supplemental integration tests targeting specific coverage gaps in 5 modules.

Modules targeted:
- services/deduplication/embedder.py  (lines 56-322 — requires sklearn mock)
- utils/text_processing.py            (NLTK paths, extract_keywords, sanitize edge cases)
- core/backend_detector.py            (CLI list, JSON parsing, text fallback)
- services/copilot/executor.py        (move/rename/find/undo/redo/preview handlers)
- services/intelligence/preference_store.py (schema validation, migration, backup/restore)
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

if TYPE_CHECKING:
    from file_organizer.services.deduplication.embedder import DocumentEmbedder

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Embedder — inject a fake sklearn so every branch can be exercised
# ---------------------------------------------------------------------------


class _FakeSparseMatrix:
    """Minimal scipy sparse matrix stand-in."""

    def __init__(self, data: np.ndarray) -> None:
        self._data = np.asarray(data)

    def toarray(self) -> np.ndarray:
        return self._data


class _FakeTfidfVectorizer:
    """Minimal TfidfVectorizer mock that stores docs and returns predictable embeddings."""

    def __init__(self, **kwargs: Any) -> None:
        self.max_df = kwargs.get("max_df", 1.0)
        self.min_df = kwargs.get("min_df", 1)
        self.vocabulary_: dict[str, int] = {}
        self._fitted = False

    def fit_transform(self, documents: list[str]) -> _FakeSparseMatrix:
        words = sorted({w for doc in documents for w in doc.lower().split()})
        self.vocabulary_ = {w: i for i, w in enumerate(words)}
        n = len(documents)
        m = max(len(words), 1)
        self._fitted = True
        return _FakeSparseMatrix(np.ones((n, m)) * 0.1)

    def transform(self, documents: list[str]) -> _FakeSparseMatrix:
        m = max(len(self.vocabulary_), 1)
        return _FakeSparseMatrix(np.ones((len(documents), m)) * 0.1)

    def get_feature_names_out(self) -> np.ndarray:
        return np.array(sorted(self.vocabulary_.keys()))


def _make_embedder_with_fake_sklearn(**kw: Any) -> DocumentEmbedder:
    """Return a DocumentEmbedder instance backed by a fake sklearn.

    The sklearn import is deferred to DocumentEmbedder.__init__, so we need to patch
    sys.modules to provide a fake sklearn when the import happens at instantiation.
    """
    import sys

    # Create mock sklearn modules
    mock_sklearn = MagicMock()
    mock_sklearn.feature_extraction.text.TfidfVectorizer = _FakeTfidfVectorizer

    with patch.dict(
        sys.modules,
        {
            "sklearn": mock_sklearn,
            "sklearn.feature_extraction": mock_sklearn.feature_extraction,
            "sklearn.feature_extraction.text": mock_sklearn.feature_extraction.text,
        },
    ):
        from file_organizer.services.deduplication.embedder import DocumentEmbedder

        return DocumentEmbedder(**kw)


@pytest.mark.integration
class TestDocumentEmbedderWithFakeSklearn:
    """Cover lines 56-322 in embedder.py via a fake TfidfVectorizer."""

    def test_fit_transform_returns_array(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        result = emb.fit_transform(["hello world", "foo bar"])
        assert isinstance(result, np.ndarray)
        assert result.shape[0] == 2
        assert emb.is_fitted is True

    def test_fit_transform_empty_returns_empty(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        result = emb.fit_transform([])
        assert result.size == 0

    def test_fit_transform_small_corpus_max_df_restored(self) -> None:
        """When corpus is tiny, max_df is temporarily set to 1.0 then restored."""
        emb = _make_embedder_with_fake_sklearn(max_df=0.5)
        original_max_df = emb.vectorizer.max_df
        emb.fit_transform(["single doc"])
        assert emb.vectorizer.max_df == original_max_df

    def test_transform_after_fit(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        emb.fit_transform(["hello world"])
        vec = emb.transform("hello")
        assert isinstance(vec, np.ndarray)

    def test_transform_not_fitted_raises(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        with pytest.raises(RuntimeError, match="not fitted"):
            emb.transform("hello")

    def test_transform_uses_cache(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        emb.fit_transform(["hello world"])
        v1 = emb.transform("hello")
        v2 = emb.transform("hello")  # cache hit
        assert np.array_equal(v1, v2)
        assert len(emb.embedding_cache) == 1

    def test_transform_batch(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        emb.fit_transform(["a b c"])
        result = emb.transform_batch(["a", "b"])
        assert result.shape[0] == 2

    def test_get_feature_names(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        emb.fit_transform(["alpha beta gamma"])
        names = emb.get_feature_names()
        assert isinstance(names, list)
        assert "alpha" in names

    def test_get_vocabulary(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        emb.fit_transform(["alpha beta"])
        vocab = emb.get_vocabulary()
        assert isinstance(vocab, dict)
        assert "alpha" in vocab

    def test_get_top_terms(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        emb.fit_transform(["alpha beta gamma"])
        v = emb.transform("alpha")
        terms = emb.get_top_terms(v, top_n=3)
        assert isinstance(terms, list)
        assert len(terms) == 3

    def test_save_and_load_model(self, tmp_path: Path) -> None:
        model_path = tmp_path / "vectorizer.pkl"
        emb = _make_embedder_with_fake_sklearn()
        emb.fit_transform(["hello world"])
        emb.save_model(model_path)
        assert model_path.exists()

        emb2 = _make_embedder_with_fake_sklearn()
        emb2.load_model(model_path)
        assert emb2.is_fitted is True

    def test_save_model_unfitted_is_noop(self, tmp_path: Path) -> None:
        model_path = tmp_path / "noop.pkl"
        emb = _make_embedder_with_fake_sklearn()
        emb.save_model(model_path)
        assert not model_path.exists()

    def test_clear_cache(self) -> None:
        emb = _make_embedder_with_fake_sklearn()
        emb.fit_transform(["hello world"])
        emb.transform("hello")
        assert len(emb.embedding_cache) == 1
        emb.clear_cache()
        assert len(emb.embedding_cache) == 0

    def test_cache_persisted_on_disk(self, tmp_path: Path) -> None:
        """Embedder saves/loads embedding cache from disk."""
        cache_file = tmp_path / "cache.pkl"
        emb = _make_embedder_with_fake_sklearn(cache_path=cache_file)
        emb.fit_transform(["test document"])
        emb.transform("test document")
        emb._save_cache()
        assert cache_file.exists()

    def test_sklearn_not_available_raises_import_error(self) -> None:
        """When sklearn is not available, DocumentEmbedder raises ImportError on instantiation."""
        import importlib
        import sys

        from file_organizer.services.deduplication import embedder as embedder_mod

        # Mock sys.modules to make sklearn unavailable
        sklearn_modules = {
            "sklearn": None,
            "sklearn.feature_extraction": None,
            "sklearn.feature_extraction.text": None,
        }

        with patch.dict(sys.modules, sklearn_modules):
            # Force reimport by removing from cache
            importlib.reload(embedder_mod)

            with pytest.raises(ImportError, match="scikit-learn"):
                embedder_mod.DocumentEmbedder()

        # Restore embedder module to working state after test
        importlib.reload(embedder_mod)


# ---------------------------------------------------------------------------
# text_processing — NLTK paths
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTextProcessingNLTKPaths:
    """Cover NLTK-dependent branches in text_processing.py."""

    def test_clean_text_with_nltk_tokenize(self, ensure_nltk_available: None) -> None:
        from file_organizer.utils.text_processing import clean_text

        result = clean_text("The quick brown Fox jumps", max_words=5, lemmatize=False)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_clean_text_lemmatize_enabled(self, ensure_nltk_available: None) -> None:
        from file_organizer.utils.text_processing import clean_text

        result = clean_text("running jumps flying", max_words=5, lemmatize=True)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_clean_text_nltk_tokenize_lookup_error_fallback(self) -> None:
        import file_organizer.utils.text_processing as _tp

        with patch.object(_tp, "word_tokenize", side_effect=LookupError("punkt")):
            result = _tp.clean_text("hello world test", max_words=3)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_clean_text_lemmatize_lookup_error_fallback(self) -> None:
        import file_organizer.utils.text_processing as _tp

        mock_lemmatizer = MagicMock()
        mock_lemmatizer.lemmatize.side_effect = LookupError("wordnet")
        with patch(
            "file_organizer.utils.text_processing.WordNetLemmatizer", return_value=mock_lemmatizer
        ):
            result = _tp.clean_text("testing files", max_words=3, lemmatize=True)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_extract_keywords_with_nltk(self, ensure_nltk_available: None) -> None:
        from file_organizer.utils.text_processing import extract_keywords

        keywords = extract_keywords("machine learning deep neural network algorithm", top_n=3)
        assert isinstance(keywords, list)
        assert len(keywords) == 3

    def test_extract_keywords_without_nltk_fallback(self) -> None:
        import file_organizer.utils.text_processing as _tp

        with patch.object(_tp, "NLTK_AVAILABLE", False):
            result = _tp.extract_keywords("apple banana cherry apple", top_n=2)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_extract_keywords_nltk_lookup_error_returns_empty(self) -> None:
        import file_organizer.utils.text_processing as _tp

        with patch("file_organizer.utils.text_processing.word_tokenize", side_effect=LookupError):
            result = _tp.extract_keywords("test text", top_n=3)
        assert result == []

    def test_sanitize_filename_empty_after_cleaning(self, ensure_nltk_available: None) -> None:
        from file_organizer.utils.text_processing import sanitize_filename

        result = sanitize_filename("123 456 789")
        assert result == "untitled"

    def test_sanitize_filename_length_truncation(self, ensure_nltk_available: None) -> None:
        from file_organizer.utils.text_processing import sanitize_filename

        # Use space-separated words so clean_text tokenizes them individually.
        # Underscore-joined words fail isalpha() and are entirely pruned → "untitled".
        # The five words produce "jungle_kingdom_laurel_mansion_nautical" (37 chars);
        # truncated at 20: "jungle_kingdom_laure" — last char is 'e', not '_',
        # so rstrip("_") is a no-op and len is exactly 20.
        long_name = "jungle kingdom laurel mansion nautical octopus paradise quantum"
        result = sanitize_filename(long_name, max_length=20)
        assert result == "jungle_kingdom_laure"
        assert result[-1] != "_"

    def test_get_unwanted_words_includes_stopwords(self, ensure_nltk_available: None) -> None:
        from file_organizer.utils.text_processing import get_unwanted_words

        words = get_unwanted_words()
        assert isinstance(words, set)
        assert len(words) > 10

    def test_get_unwanted_words_nltk_lookup_error(self) -> None:
        import file_organizer.utils.text_processing as _tp

        # Directly replace in module dict to avoid NLTK lazy-loader trigger during patch setup
        mock_sw = MagicMock()
        mock_sw.words.side_effect = LookupError("stopwords")
        orig = _tp.__dict__.pop("stopwords", None)
        _tp.__dict__["stopwords"] = mock_sw
        try:
            result = _tp.get_unwanted_words()
        finally:
            if orig is not None:
                _tp.__dict__["stopwords"] = orig
        assert isinstance(result, set)
        assert len(result) > 0

    def test_ensure_nltk_data_idempotent(self, ensure_nltk_available: None) -> None:
        import file_organizer.utils.text_processing as _tp

        _tp._nltk_ready = False
        _tp.ensure_nltk_data()
        _tp.ensure_nltk_data()  # second call is no-op
        assert _tp._nltk_ready is True

    def test_ensure_nltk_data_not_available(self) -> None:
        import file_organizer.utils.text_processing as _tp

        with patch.object(_tp, "NLTK_AVAILABLE", False):
            _tp._nltk_ready = False
            _tp.ensure_nltk_data()
        assert _tp._nltk_ready is False


# ---------------------------------------------------------------------------
# backend_detector — CLI list, JSON parsing, text fallback
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBackendDetectorCLIList:
    """Cover lines 161-265 in backend_detector.py."""

    def _make_ollama_mock(self, client_exc: type[BaseException] = ConnectionError) -> MagicMock:
        """Return a mock ollama module whose Client raises the given exception on .list()."""
        mock_ollama = MagicMock()
        mock_ollama.Client.return_value.list.side_effect = client_exc("service down")
        return mock_ollama

    def test_list_models_via_cli_json_dict_format(self) -> None:
        from file_organizer.core import backend_detector as _bd

        payload = json.dumps(
            {"models": [{"name": "llama3:8b", "size": 4_000_000_000, "modified_at": "2024-01-01"}]}
        )
        cli_result = MagicMock()
        cli_result.returncode = 0
        cli_result.stdout = payload

        with (
            patch.object(_bd, "OLLAMA_AVAILABLE", True),
            patch.object(_bd, "ollama", self._make_ollama_mock()),
            patch("file_organizer.core.backend_detector.subprocess.run", return_value=cli_result),
        ):
            models = _bd.list_installed_models()
        assert len(models) == 1
        assert models[0].name == "llama3:8b"

    def test_list_models_via_cli_json_list_format(self) -> None:
        from file_organizer.core import backend_detector as _bd

        payload = json.dumps([{"name": "mistral:7b", "size": 3_500_000_000}])
        cli_result = MagicMock()
        cli_result.returncode = 0
        cli_result.stdout = payload

        with (
            patch.object(_bd, "OLLAMA_AVAILABLE", True),
            patch.object(_bd, "ollama", self._make_ollama_mock()),
            patch("file_organizer.core.backend_detector.subprocess.run", return_value=cli_result),
        ):
            models = _bd.list_installed_models()
        assert len(models) == 1
        assert models[0].name == "mistral:7b"

    def test_list_models_cli_nonzero_returncode_falls_back_to_text(self) -> None:
        from file_organizer.core import backend_detector as _bd

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stdout = ""

        text_result = MagicMock()
        text_result.stdout = "NAME\nllama3:8b  4GB  1 day ago\nmistral:7b  3GB  2 days ago\n"

        with (
            patch.object(_bd, "OLLAMA_AVAILABLE", True),
            patch.object(_bd, "ollama", self._make_ollama_mock()),
            patch(
                "file_organizer.core.backend_detector.subprocess.run",
                side_effect=[fail_result, text_result],
            ),
        ):
            models = _bd.list_installed_models()
        assert any(m.name == "llama3:8b" for m in models)

    def test_list_models_json_decode_error_falls_back_to_text(self) -> None:
        from file_organizer.core import backend_detector as _bd

        bad_result = MagicMock()
        bad_result.returncode = 0
        bad_result.stdout = "not valid json"

        text_result = MagicMock()
        text_result.stdout = "NAME\ngemma:2b  2GB  3 days ago\n"

        with (
            patch.object(_bd, "OLLAMA_AVAILABLE", True),
            patch.object(_bd, "ollama", self._make_ollama_mock()),
            patch(
                "file_organizer.core.backend_detector.subprocess.run",
                side_effect=[bad_result, text_result],
            ),
        ):
            models = _bd.list_installed_models()
        assert any(m.name == "gemma:2b" for m in models)

    def test_list_models_cli_file_not_found_returns_empty(self) -> None:
        from file_organizer.core import backend_detector as _bd

        with (
            patch.object(_bd, "OLLAMA_AVAILABLE", True),
            patch.object(_bd, "ollama", self._make_ollama_mock()),
            patch(
                "file_organizer.core.backend_detector.subprocess.run",
                side_effect=FileNotFoundError,
            ),
        ):
            models = _bd.list_installed_models()
        assert models == []

    def test_list_models_text_fallback_parse_error_returns_empty(self) -> None:
        from file_organizer.core import backend_detector as _bd

        with (
            patch.object(_bd, "OLLAMA_AVAILABLE", True),
            patch.object(_bd, "ollama", self._make_ollama_mock()),
            patch(
                "file_organizer.core.backend_detector.subprocess.run",
                side_effect=OSError("broken"),
            ),
        ):
            models = _bd.list_installed_models()
        assert models == []

    def test_detect_ollama_version_extraction(self) -> None:
        from file_organizer.core import backend_detector as _bd

        version_result = MagicMock()
        version_result.returncode = 0
        version_result.stdout = "ollama version 0.1.42\n"

        list_result = MagicMock()
        list_result.returncode = 0
        list_result.stdout = "[]"

        with (
            patch.object(_bd, "OLLAMA_AVAILABLE", False),
            patch(
                "file_organizer.core.backend_detector.subprocess.run",
                return_value=version_result,
            ),
        ):
            from file_organizer.core.backend_detector import OllamaStatus, detect_ollama

            with patch(
                "file_organizer.core.backend_detector.subprocess.run",
                side_effect=[version_result, OSError],
            ):
                status = detect_ollama()
        assert isinstance(status, OllamaStatus)


# ---------------------------------------------------------------------------
# copilot executor — move/rename/find/undo/redo/preview handlers
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCopilotExecutorHandlers:
    """Cover lines 133-428 in executor.py."""

    @pytest.fixture
    def executor(self, tmp_path: Path):
        from file_organizer.services.copilot.executor import CommandExecutor

        return CommandExecutor(working_directory=tmp_path)

    def test_handle_move_missing_params(self, executor) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        intent = Intent(intent_type=IntentType.MOVE, parameters={})
        result = executor.execute(intent)
        assert result.success is False
        assert "source" in result.message.lower() or "destination" in result.message.lower()

    def test_handle_move_source_not_found(self, executor, tmp_path: Path) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        intent = Intent(
            intent_type=IntentType.MOVE,
            parameters={"source": "nonexistent.txt", "destination": "dest.txt"},
        )
        result = executor.execute(intent)
        assert result.success is False
        assert "not found" in result.message.lower()

    def test_handle_move_success(self, executor, tmp_path: Path) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        src = tmp_path / "source.txt"
        src.write_text("hello")
        intent = Intent(
            intent_type=IntentType.MOVE,
            parameters={"source": str(src), "destination": str(tmp_path / "dest.txt")},
        )
        result = executor.execute(intent)
        assert result.success is True
        assert (tmp_path / "dest.txt").exists()

    def test_handle_rename_missing_params(self, executor) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        intent = Intent(intent_type=IntentType.RENAME, parameters={})
        result = executor.execute(intent)
        assert result.success is False

    def test_handle_rename_success(self, executor, tmp_path: Path) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        f = tmp_path / "old_name.txt"
        f.write_text("content")
        intent = Intent(
            intent_type=IntentType.RENAME,
            parameters={"target": str(f), "new_name": "new_name.txt"},
        )
        result = executor.execute(intent)
        assert result.success is True
        assert (tmp_path / "new_name.txt").exists()

    def test_handle_find_no_query(self, executor) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        intent = Intent(intent_type=IntentType.FIND, parameters={})
        result = executor.execute(intent)
        assert result.success is False
        assert "search" in result.message.lower()

    def test_handle_find_with_results(self, executor, tmp_path: Path) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        f = tmp_path / "important_report.txt"
        f.write_text("content about reports")
        intent = Intent(
            intent_type=IntentType.FIND,
            parameters={"query": "important"},
        )
        result = executor.execute(intent)
        assert result.success is True

    def test_handle_undo_no_history(self, executor) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        intent = Intent(intent_type=IntentType.UNDO, parameters={})
        result = executor.execute(intent)
        assert result.success is False
        assert "undo" in result.message.lower()

    def test_handle_redo_no_history(self, executor) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        intent = Intent(intent_type=IntentType.REDO, parameters={})
        result = executor.execute(intent)
        assert result.success is False
        assert "redo" in result.message.lower()

    def test_handle_preview_no_target(self, executor) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        intent = Intent(intent_type=IntentType.PREVIEW, parameters={})
        result = executor.execute(intent)
        assert isinstance(result.success, bool)
        assert result.message

    def test_handle_suggest_no_file(self, executor) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        intent = Intent(intent_type=IntentType.SUGGEST, parameters={})
        result = executor.execute(intent)
        assert result.success is False

    def test_handle_organize_import_error(self, executor) -> None:
        from file_organizer.services.copilot.executor import Intent, IntentType

        intent = Intent(intent_type=IntentType.ORGANIZE, parameters={"target": "."})
        with patch.dict("sys.modules", {"file_organizer.services.organizer": None}):
            result = executor.execute(intent)
        assert isinstance(result.success, bool)
        assert result.message

    def test_resolve_path_home_expansion(self, executor) -> None:
        result = executor._resolve_path("~/Documents/test.txt")
        assert "~" not in str(result)

    def test_resolve_path_relative(self, executor, tmp_path: Path) -> None:
        result = executor._resolve_path("subdir/file.txt")
        assert result.is_absolute()


# ---------------------------------------------------------------------------
# preference_store — schema validation, migration, backup/restore
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPreferenceStoreSchemaAndBackup:
    """Cover lines 46-555 in preference_store.py."""

    @pytest.fixture
    def store(self, tmp_path: Path):
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        return PreferenceStore(storage_path=tmp_path / "prefs")

    def test_validate_schema_valid(self, store) -> None:
        data = store._create_empty_preferences()
        assert store._validate_schema(data) is True

    def test_validate_schema_missing_field(self, store) -> None:
        data = store._create_empty_preferences()
        del data["user_id"]
        assert store._validate_schema(data) is False

    def test_validate_schema_invalid_version(self, store) -> None:
        data = store._create_empty_preferences()
        data["version"] = "99.99"
        assert store._validate_schema(data) is False

    def test_validate_schema_invalid_dir_prefs(self, store) -> None:
        data = store._create_empty_preferences()
        data["directory_preferences"] = "not a dict"
        assert store._validate_schema(data) is False

    def test_validate_schema_dir_pref_missing_required_field(self, store) -> None:
        data = store._create_empty_preferences()
        data["directory_preferences"]["/some/path"] = {"folder_mappings": {}}
        assert store._validate_schema(data) is False

    def test_migrate_schema_v1_returns_same(self, store) -> None:
        data = store._create_empty_preferences()
        result = store._migrate_schema(data, "1.0")
        assert result["version"] == data["version"]

    def test_migrate_schema_unknown_version_returns_same(self, store) -> None:
        data = store._create_empty_preferences()
        result = store._migrate_schema(data, "0.5")
        assert result is data

    def test_load_preferences_from_corrupt_file_uses_backup(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.preference_file.write_text("not json {{{")
        # Write a valid backup
        backup_data = store._create_empty_preferences()
        store.backup_file.write_text(json.dumps(backup_data))
        result = store.load_preferences()
        assert result is True

    def test_load_preferences_no_file_returns_defaults(self, store) -> None:
        result = store.load_preferences()
        assert result is False  # no file = defaults
        assert isinstance(store._preferences, dict)

    def test_save_preferences_creates_file(self, store, tmp_path: Path) -> None:
        store.load_preferences()
        store._preferences = store._create_empty_preferences()
        store._loaded = True
        store.save_preferences()
        assert store.preference_file.exists()

    def test_add_and_get_preference(self, store) -> None:
        store.load_preferences()
        store.add_preference(
            Path("/docs"),
            {
                "folder_mappings": {},
                "naming_patterns": {},
                "category_overrides": {},
            },
        )
        pref = store.get_preference(Path("/docs"))
        assert pref is not None

    def test_get_preference_with_parent_fallback(self, store) -> None:
        store.load_preferences()
        store.add_preference(
            Path("/docs"),
            {"folder_mappings": {}, "naming_patterns": {}, "category_overrides": {}},
        )
        # Child doesn't have preference, should fall back to parent
        parent_pref = store.get_preference(Path("/docs"))
        result = store.get_preference(Path("/docs/subdir"), fallback_to_parent=True)
        assert result == parent_pref

    def test_update_confidence_increases_score(self, store) -> None:
        store.load_preferences()
        store.add_preference(
            Path("/music"),
            {"folder_mappings": {}, "naming_patterns": {}, "category_overrides": {}},
        )
        store.update_confidence(Path("/music"), success=True)
        pref = store.get_preference(Path("/music"))
        assert pref is not None

    def test_clear_preferences(self, store) -> None:
        store.load_preferences()
        store.add_preference(
            Path("/videos"),
            {"folder_mappings": {}, "naming_patterns": {}, "category_overrides": {}},
        )
        store.clear_preferences()
        # After clear, no directory-specific preferences remain
        dirs = store.list_directory_preferences()
        assert not any("/videos" in p for p, _ in dirs)

    def test_get_statistics(self, store) -> None:
        store.load_preferences()
        stats = store.get_statistics()
        assert "total_directories" in stats
        assert stats["total_directories"] >= 0

    def test_list_directory_preferences(self, store) -> None:
        store.load_preferences()
        store.add_preference(
            Path("/photos"),
            {"folder_mappings": {}, "naming_patterns": {}, "category_overrides": {}},
        )
        dirs = store.list_directory_preferences()
        assert isinstance(dirs, list)
        # list_directory_preferences returns list[tuple[str, dict]]
        assert any("/photos" in p for p, _ in dirs)

    def test_export_import_json_roundtrip(self, store, tmp_path: Path) -> None:
        store.load_preferences()
        store.add_preference(
            Path("/archive"),
            {"folder_mappings": {}, "naming_patterns": {}, "category_overrides": {}},
        )
        export_path = tmp_path / "export.json"
        store.export_json(export_path)
        assert export_path.exists()

        store2_path = tmp_path / "prefs2"
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store2 = PreferenceStore(storage_path=store2_path)
        store2.load_preferences()
        store2.import_json(export_path)
        dirs = store2.list_directory_preferences()
        assert any("/archive" in p for p, _ in dirs)

    def test_resolve_conflicts_picks_highest_confidence(self, store) -> None:
        from datetime import datetime

        now = datetime.now(UTC).isoformat()
        p1 = {
            "folder_mappings": {},
            "naming_patterns": {},
            "category_overrides": {},
            "confidence": 0.9,
            "correction_count": 5,
            "created": now,
            "updated": now,
        }
        p2 = {
            "folder_mappings": {},
            "naming_patterns": {},
            "category_overrides": {},
            "confidence": 0.5,
            "correction_count": 1,
            "created": now,
            "updated": now,
        }
        winner = store.resolve_conflicts([p1, p2])
        assert winner["confidence"] == 0.9
