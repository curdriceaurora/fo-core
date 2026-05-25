"""Undo/redo functionality for file operations.

This module provides comprehensive undo/redo capabilities for file operations,
including validation, rollback execution, and history viewing.

Module-level imports of ``rollback``, ``undo_manager``, ``viewer`` etc.
transitively pull in ``history.tracker`` (and therefore ``sqlite3``); per
issue #404, that's a heavy cost to pay on ``import undo`` from CLI entry
points that only need a single class.  The public API is therefore exposed
via PEP 562 ``__getattr__`` so that ``from undo import X`` only triggers
the import of the submodule that owns ``X``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing-only re-exports
    from .models import Conflict, ConflictType, RollbackResult, ValidationResult
    from .rollback import RollbackExecutor
    from .undo_manager import UndoManager
    from .validator import OperationValidator
    from .viewer import HistoryViewer

__all__ = [
    "ValidationResult",
    "RollbackResult",
    "Conflict",
    "ConflictType",
    "OperationValidator",
    "RollbackExecutor",
    "UndoManager",
    "HistoryViewer",
]

__version__ = "1.0.0"

# Maps public attribute name -> (submodule, attribute) for lazy resolution.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ValidationResult": ("undo.models", "ValidationResult"),
    "RollbackResult": ("undo.models", "RollbackResult"),
    "Conflict": ("undo.models", "Conflict"),
    "ConflictType": ("undo.models", "ConflictType"),
    "OperationValidator": ("undo.validator", "OperationValidator"),
    "RollbackExecutor": ("undo.rollback", "RollbackExecutor"),
    "UndoManager": ("undo.undo_manager", "UndoManager"),
    "HistoryViewer": ("undo.viewer", "HistoryViewer"),
}


def __getattr__(name: str) -> Any:
    """Lazily resolve public re-exports without importing submodules eagerly."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(target[0])
    value = getattr(mod, target[1])
    globals()[name] = value
    return value
