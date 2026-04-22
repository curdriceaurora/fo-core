"""Unit tests for cli.dedupe_strategy helpers.

Covers both public helpers:

- `_resolve_console` — previously deferred to `cli.dedupe.console` via a
  try/except fallback. Epic D.cleanup (#157 / PR #167) dropped that v1
  lookup in favour of an unconditional `Console()` when no console is
  passed, so this test pins both branches of the helper.
- `select_files_to_keep` — deterministic strategy evaluator
  (oldest / newest / largest / smallest / manual).
- `get_user_selection` — interactive + batch prompts across manual and
  automatic strategies, including the error-recovery loop branches that
  D#167 left uncovered when the legacy `fo dedupe` CLI was removed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rich.console import Console

from cli.dedupe_strategy import (
    _resolve_console,
    get_user_selection,
    select_files_to_keep,
)


@pytest.mark.ci
@pytest.mark.integration
class TestResolveConsole:
    def test_returns_injected_console_when_provided(self) -> None:
        injected = Console()
        assert _resolve_console(injected) is injected

    def test_returns_fresh_console_when_none(self) -> None:
        resolved = _resolve_console(None)
        assert isinstance(resolved, Console)


def _sample_files() -> list[dict[str, object]]:
    return [
        {"path": "/a.txt", "size": 100, "mtime": 300.0},
        {"path": "/b.txt", "size": 200, "mtime": 100.0},
        {"path": "/c.txt", "size": 150, "mtime": 200.0},
    ]


@pytest.mark.ci
@pytest.mark.integration
class TestSelectFilesToKeep:
    def test_empty_returns_empty_list(self) -> None:
        assert select_files_to_keep([], "oldest") == []

    def test_oldest_marks_file_with_smallest_mtime(self) -> None:
        result = select_files_to_keep(_sample_files(), "oldest")
        kept = [f for f in result if f.get("keep")]
        assert len(kept) == 1
        assert kept[0]["path"] == "/b.txt"

    def test_newest_marks_file_with_largest_mtime(self) -> None:
        result = select_files_to_keep(_sample_files(), "newest")
        kept = [f for f in result if f.get("keep")]
        assert len(kept) == 1
        assert kept[0]["path"] == "/a.txt"

    def test_largest_marks_file_with_biggest_size(self) -> None:
        result = select_files_to_keep(_sample_files(), "largest")
        kept = [f for f in result if f.get("keep")]
        assert len(kept) == 1
        assert kept[0]["path"] == "/b.txt"

    def test_smallest_marks_file_with_smallest_size(self) -> None:
        result = select_files_to_keep(_sample_files(), "smallest")
        kept = [f for f in result if f.get("keep")]
        assert len(kept) == 1
        assert kept[0]["path"] == "/a.txt"

    def test_manual_leaves_keep_flag_unset(self) -> None:
        result = select_files_to_keep(_sample_files(), "manual")
        assert all("keep" not in f for f in result)

    def test_unknown_strategy_is_noop(self) -> None:
        result = select_files_to_keep(_sample_files(), "random")
        assert all("keep" not in f for f in result)


def _console_with_inputs(*inputs: str) -> Console:
    """Return a Console whose `.input()` yields each value in order."""
    console = MagicMock(spec=Console)
    console.input = MagicMock(side_effect=list(inputs))
    return console


@pytest.mark.ci
@pytest.mark.integration
class TestGetUserSelection:
    def test_manual_skip_returns_empty(self) -> None:
        files = _sample_files()
        console = _console_with_inputs("s")
        assert get_user_selection(files, "manual", console=console) == []

    def test_manual_keep_all_returns_empty(self) -> None:
        files = _sample_files()
        console = _console_with_inputs("a")
        assert get_user_selection(files, "manual", console=console) == []

    def test_manual_keep_one_returns_other_indices(self) -> None:
        files = _sample_files()
        console = _console_with_inputs("1")
        # Keep index 0 → remove [1, 2]
        assert get_user_selection(files, "manual", console=console) == [1, 2]

    def test_manual_invalid_then_valid_loops_until_valid(self) -> None:
        files = _sample_files()
        # First invalid (out-of-range), then non-numeric, then valid
        console = _console_with_inputs("99", "zzz", "2")
        assert get_user_selection(files, "manual", console=console) == [0, 2]

    def test_manual_keyboard_interrupt_propagates(self) -> None:
        files = _sample_files()
        console = MagicMock(spec=Console)
        console.input.side_effect = KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            get_user_selection(files, "manual", console=console)

    def test_auto_batch_returns_files_not_marked_to_keep(self) -> None:
        files = [
            {"path": "/a.txt", "keep": True},
            {"path": "/b.txt"},
            {"path": "/c.txt"},
        ]
        # batch=True skips the confirmation prompt
        assert get_user_selection(files, "newest", batch=True) == [1, 2]

    def test_auto_confirm_yes_removes_unmarked(self) -> None:
        files = [
            {"path": "/a.txt", "keep": True},
            {"path": "/b.txt"},
        ]
        console = _console_with_inputs("y")
        assert get_user_selection(files, "newest", console=console) == [1]

    def test_auto_confirm_no_returns_empty(self) -> None:
        files = [{"path": "/a.txt", "keep": True}, {"path": "/b.txt"}]
        console = _console_with_inputs("n")
        assert get_user_selection(files, "newest", console=console) == []

    def test_auto_confirm_skip_returns_empty(self) -> None:
        files = [{"path": "/a.txt", "keep": True}, {"path": "/b.txt"}]
        console = _console_with_inputs("s")
        assert get_user_selection(files, "newest", console=console) == []

    def test_auto_invalid_then_yes_loops(self) -> None:
        files = [{"path": "/a.txt", "keep": True}, {"path": "/b.txt"}]
        console = _console_with_inputs("maybe", "yes")
        assert get_user_selection(files, "newest", console=console) == [1]

    def test_auto_keyboard_interrupt_propagates(self) -> None:
        files = [{"path": "/a.txt", "keep": True}]
        console = MagicMock(spec=Console)
        console.input.side_effect = KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            get_user_selection(files, "newest", console=console)
