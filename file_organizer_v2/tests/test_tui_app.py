"""Tests for TUI application."""
from __future__ import annotations

import pytest

from file_organizer.tui.app import FileOrganizerApp, PlaceholderView, StatusBar


@pytest.mark.asyncio
async def test_app_starts_and_quits() -> None:
    """App should start, render, and exit cleanly."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        assert app.query_one(StatusBar) is not None
        assert app.query_one(PlaceholderView) is not None
        await pilot.press("q")


@pytest.mark.asyncio
async def test_initial_view_is_files() -> None:
    """Initial main content should show the Files view."""
    app = FileOrganizerApp()
    async with app.run_test():
        assert app._current_view == "files"


@pytest.mark.asyncio
async def test_switch_to_organized_view() -> None:
    """Pressing '2' should switch to the Organized view."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        await pilot.press("2")
        assert app._current_view == "organized"


@pytest.mark.asyncio
async def test_switch_to_settings_view() -> None:
    """Pressing '3' should switch to the Settings view."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        await pilot.press("3")
        assert app._current_view == "settings"


@pytest.mark.asyncio
async def test_switch_back_to_files_view() -> None:
    """Pressing '1' after switching should return to Files view."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.press("1")
        assert app._current_view == "files"


@pytest.mark.asyncio
async def test_status_bar_updates_on_view_switch() -> None:
    """StatusBar should update its internal message when switching views."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        await pilot.press("2")
        status = app.query_one(StatusBar)
        assert "Organized" in status._message


@pytest.mark.asyncio
async def test_help_action_updates_status() -> None:
    """Pressing '?' should update the status bar with help info."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        await pilot.press("question_mark")
        status = app.query_one(StatusBar)
        assert "quit" in status._message.lower()


class TestStatusBarUnit:
    """Unit tests for StatusBar widget (no app context needed)."""

    def test_default_message(self) -> None:
        bar = StatusBar()
        assert bar._message == "Ready"

    def test_custom_initial_message(self) -> None:
        bar = StatusBar("Loading...")
        assert bar._message == "Loading..."

    def test_set_status_updates_message(self) -> None:
        bar = StatusBar()
        bar.set_status("Processing")
        assert bar._message == "Processing"
