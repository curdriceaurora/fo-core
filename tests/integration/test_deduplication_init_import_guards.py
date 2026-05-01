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

Strategy: use ``patch.dict`` context manager on sys.modules so the
restoring reload always runs *after* sys.modules is restored.
``patch.dict.__exit__`` fires synchronously before the next statement,
avoiding the monkeypatch teardown-ordering problem (T12-safe).
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.ci]


class TestDeduplicationInitImportGuards:
    """Cover uncovered except-branches in services/deduplication/__init__.py."""

    def test_image_dedup_missing_sets_none(self) -> None:
        """When image_dedup submodule is unavailable, ImageDeduplicator is set to None.

        Setting sys.modules['services.deduplication.image_dedup'] = None causes
        the import machinery to raise ModuleNotFoundError, which the bare
        'except ImportError' clause catches and handles by assigning None.
        The document-dedup exports (DocumentDeduplicator, DocumentEmbedder,
        SemanticAnalyzer) are guarded by a *separate* try/except block and must
        remain unchanged by this guard — snapshot their values before the reload
        and assert they are the same after.
        """
        import services.deduplication as dedup_mod

        # Snapshot document-dedup exports before patching so we can assert they
        # are unaffected (avoids asserting they are not-None, which would fail
        # in lean environments without numpy/sklearn installed).
        pre_doc_dedup = dedup_mod.DocumentDeduplicator
        pre_embedder = dedup_mod.DocumentEmbedder
        pre_semantic = dedup_mod.SemanticAnalyzer

        with patch.dict(sys.modules, {"services.deduplication.image_dedup": None}):
            importlib.reload(dedup_mod)
            assert dedup_mod.ImageDeduplicator is None
            # Document-dedup exports must be unaffected by the image guard.
            assert dedup_mod.DocumentDeduplicator is pre_doc_dedup
            assert dedup_mod.DocumentEmbedder is pre_embedder
            assert dedup_mod.SemanticAnalyzer is pre_semantic
        # patch.dict exited → entry restored → reload brings module back to full state
        importlib.reload(dedup_mod)

    def test_document_dedup_missing_numpy_sets_none(self) -> None:
        """When numpy is unavailable all three document-dedup exports are set to None.

        Strategy: *pop* (evict) the submodules from sys.modules rather than
        setting them to None.  Setting to None produces an error message of the
        form "import of X halted; None in sys.modules" which does NOT contain
        "numpy"/"sklearn" — so the guard would re-raise instead of setting
        exports to None.  Evicting forces Python to re-import the submodule
        (embedder.py or semantic.py), which then fails trying to import numpy
        with the right message for the guard to catch.
        """
        import services.deduplication as dedup_mod

        submodule_keys = [
            "services.deduplication.document_dedup",
            "services.deduplication.embedder",
            "services.deduplication.semantic",
        ]
        saved = {k: sys.modules.pop(k) for k in submodule_keys if k in sys.modules}
        try:
            with patch.dict(sys.modules, {"numpy": None}):
                importlib.reload(dedup_mod)
                assert dedup_mod.DocumentDeduplicator is None
                assert dedup_mod.DocumentEmbedder is None
                assert dedup_mod.SemanticAnalyzer is None
        finally:
            sys.modules.update(saved)
        # patch.dict exited → numpy restored → reload sees real numpy
        importlib.reload(dedup_mod)

    def test_document_dedup_unknown_import_error_reraises(self) -> None:
        """ImportError whose message lacks numpy/sklearn/scikit propagates to caller.

        Setting sys.modules['services.deduplication.document_dedup'] = None
        causes ModuleNotFoundError('import of services.deduplication.document_dedup
        halted; None in sys.modules').  The message contains none of the
        expected substrings, so the guard re-raises.
        """
        import services.deduplication as dedup_mod

        with patch.dict(sys.modules, {"services.deduplication.document_dedup": None}):
            with pytest.raises(ImportError):
                importlib.reload(dedup_mod)
        # patch.dict exited → submodule entry restored → module back to working state
        importlib.reload(dedup_mod)
