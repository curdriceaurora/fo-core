"""Integration tests for services/deduplication/__init__.py import guards.

Covers the two ImportError guard blocks that set optional exports to None
when optional dependencies (imagededup, numpy/sklearn) are unavailable.

Guard 1 (lines 16-19):
    try:
        from .image_dedup import ImageDeduplicator
    except ImportError:
        ImageDeduplicator = None

Guard 2 (lines 23-32):
    try:
        from .document_dedup import DocumentDeduplicator
        from .embedder import DocumentEmbedder
        from .semantic import SemanticAnalyzer
    except ImportError as e:
        if "numpy" not in str(e) and "sklearn" not in str(e) and "scikit" not in str(e):
            raise
        DocumentDeduplicator = DocumentEmbedder = SemanticAnalyzer = None

Strategy: use monkeypatch.setitem / monkeypatch.delitem on sys.modules so all
changes are restored automatically on teardown (T12-safe).  After each test a
reload restores the package to its working state.
"""

from __future__ import annotations

import importlib
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.ci]


class TestDeduplicationInitImportGuards:
    """Cover uncovered except-branches in services/deduplication/__init__.py."""

    def test_image_dedup_missing_sets_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When image_dedup submodule is unavailable, ImageDeduplicator is set to None.

        Setting sys.modules['services.deduplication.image_dedup'] = None causes
        the import machinery to raise ModuleNotFoundError, which the bare
        'except ImportError' clause catches and handles by assigning None.
        DocumentDeduplicator and related exports are unaffected.
        """
        import services.deduplication as dedup_mod

        monkeypatch.setitem(sys.modules, "services.deduplication.image_dedup", None)

        importlib.reload(dedup_mod)

        assert dedup_mod.ImageDeduplicator is None
        # The document-dedup exports must not be affected by this guard.
        assert dedup_mod.DocumentDeduplicator is not None
        assert dedup_mod.DocumentEmbedder is not None
        assert dedup_mod.SemanticAnalyzer is not None

        # Restore the module to a working state for subsequent tests.
        monkeypatch.delitem(sys.modules, "services.deduplication.image_dedup")
        importlib.reload(dedup_mod)

    def test_document_dedup_missing_numpy_sets_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When numpy is unavailable all three document-dedup exports are set to None.

        Blocking numpy causes the import chain
        document_dedup → embedder → numpy (or semantic → numpy) to raise
        ModuleNotFoundError whose message contains 'numpy'.  The guard
        catches this and sets DocumentDeduplicator, DocumentEmbedder,
        and SemanticAnalyzer to None.
        """
        import services.deduplication as dedup_mod

        # Remove submodule cache entries so they are reimported during reload
        # and encounter the blocked numpy.  monkeypatch.delitem restores them.
        for key in (
            "services.deduplication.document_dedup",
            "services.deduplication.embedder",
            "services.deduplication.semantic",
        ):
            if key in sys.modules:
                monkeypatch.delitem(sys.modules, key)

        # Block numpy — the error message will contain "numpy".
        monkeypatch.setitem(sys.modules, "numpy", None)

        importlib.reload(dedup_mod)

        assert dedup_mod.DocumentDeduplicator is None
        assert dedup_mod.DocumentEmbedder is None
        assert dedup_mod.SemanticAnalyzer is None

        # Restore the module; monkeypatch teardown un-patches numpy first,
        # so this reload sees the real numpy.
        importlib.reload(dedup_mod)

    def test_document_dedup_unknown_import_error_reraises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ImportError whose message lacks numpy/sklearn/scikit propagates to caller.

        Setting sys.modules['services.deduplication.document_dedup'] = None
        causes ModuleNotFoundError('import of services.deduplication.document_dedup
        halted; None in sys.modules').  The message contains none of the
        expected substrings, so the guard re-raises.
        """
        import services.deduplication as dedup_mod

        monkeypatch.setitem(sys.modules, "services.deduplication.document_dedup", None)

        with pytest.raises(ImportError):
            importlib.reload(dedup_mod)

        # Restore for subsequent tests.
        monkeypatch.delitem(sys.modules, "services.deduplication.document_dedup")
        importlib.reload(dedup_mod)
