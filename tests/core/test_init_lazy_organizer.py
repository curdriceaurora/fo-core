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
def test_lazy_import_of_file_organizer() -> None:
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
    """
    import importlib
    import sys

    for mod in [m for m in list(sys.modules) if m.startswith(("core", "services"))]:
        sys.modules.pop(mod, None)
    importlib.import_module("core.path_guard")
    assert "core.organizer" not in sys.modules
