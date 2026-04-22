"""Test lazy FileOrganizer export from core package.

Epic A.foundation moved ``FileOrganizer`` behind a ``__getattr__`` lazy
loader so importing ``core.path_guard`` from sibling packages (e.g.
``services/misplacement_detector``) doesn't transitively pull in
``core.organizer`` during ``services/__init__.py`` initialization — that
cycle previously caused a hard ``ImportError``.
"""

from __future__ import annotations

import pytest


@pytest.mark.ci
def test_lazy_import_of_organizer_class() -> None:
    """``from core import FileOrganizer`` resolves via ``__getattr__`` and
    returns the class. Verifies the lazy re-export is wired correctly.
    """
    import core

    organizer_cls = core.FileOrganizer
    from core.organizer import FileOrganizer as DirectFileOrganizer

    assert organizer_cls is DirectFileOrganizer


@pytest.mark.ci
def test_unknown_attribute_raises_attribute_error() -> None:
    """Unknown attributes raise ``AttributeError`` instead of silently
    returning ``None`` or triggering unrelated imports.
    """
    import core

    with pytest.raises(AttributeError, match="no attribute 'DoesNotExist'"):
        core.DoesNotExist  # noqa: B018  — intentional attribute access


@pytest.mark.ci
def test_core_package_does_not_eager_load_services() -> None:
    """Importing ``core.path_guard`` alone must not transitively load
    ``core.organizer``. That was the cycle that motivated the lazy loader.

    Uses ``patch.dict(sys.modules, ...)`` so the module table is restored on
    exit — popping ``core.*`` / ``services.*`` entries without restoring
    them would leak re-imported module objects to every subsequent test on
    this xdist worker and silently break identity assertions elsewhere
    (Pattern T12 FIXTURE_STATE_LEAK).
    """
    import importlib
    import sys
    from unittest.mock import patch

    preserved = {
        name: module
        for name, module in sys.modules.items()
        if not name.startswith(("core", "services"))
    }
    with patch.dict(sys.modules, preserved, clear=True):
        importlib.import_module("core.path_guard")
        assert "core.organizer" not in sys.modules
