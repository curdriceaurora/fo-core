"""
Command-line interface modules for File Organizer.
"""

from .autotag import handle_autotag_command, setup_autotag_parser
from .dedupe import dedupe_command
from .profile import profile_command
from .undo_redo import history_command, redo_command, undo_command

__all__ = [
    "dedupe_command",
    "undo_command",
    "redo_command",
    "history_command",
    "setup_autotag_parser",
    "handle_autotag_command",
    "profile_command",
]
