"""Command-line interface modules for File Organizer."""

from __future__ import annotations

# Lazy-load all CLI sub-apps and utilities to reduce startup latency.
# The entrypoint (cli:main) accesses `main` and `app` which
# trigger cli.main imports — this module defers everything else.

__all__ = [
    "app",
    "autotag_app",
    "copilot_app",
    "daemon_app",
    "main",
    "complete_directory",
    "complete_file",
    "confirm_action",
    "create_progress",
    "dedupe_app",
    "history_command",
    "prompt_choice",
    "prompt_directory",
    "redo_command",
    "rules_app",
    "suggest_app",
    "profile_command",
    "undo_command",
    "update_app",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "app": ("cli.main", "app"),
    "main": ("cli.main", "main"),
    "autotag_app": ("cli.autotag_v2", "autotag_app"),
    "complete_directory": ("cli.completion", "complete_directory"),
    "complete_file": ("cli.completion", "complete_file"),
    "copilot_app": ("cli.copilot", "copilot_app"),
    "daemon_app": ("cli.daemon", "daemon_app"),
    "dedupe_app": ("cli.dedupe_v2", "dedupe_app"),
    "confirm_action": ("cli.interactive", "confirm_action"),
    "create_progress": ("cli.interactive", "create_progress"),
    "prompt_choice": ("cli.interactive", "prompt_choice"),
    "prompt_directory": ("cli.interactive", "prompt_directory"),
    "profile_command": ("cli.profile", "profile_command"),
    "rules_app": ("cli.rules", "rules_app"),
    "suggest_app": ("cli.suggest", "suggest_app"),
    "history_command": ("cli.undo_redo", "history_command"),
    "redo_command": ("cli.undo_redo", "redo_command"),
    "undo_command": ("cli.undo_redo", "undo_command"),
    "update_app": ("cli.update", "update_app"),
}


def __getattr__(name: str) -> object:
    """Lazily import CLI attributes on first access."""
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        import importlib

        module = importlib.import_module(module_path)
        value = getattr(module, attr)
        # Cache in module globals to avoid repeated lookups
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
