"""Tests for TUI application."""
from __future__ import annotations

import pytest

from file_organizer.tui.app import FileOrganizerApp, PlaceholderView, StatusBar
from file_organizer.tui.file_preview import FilePreviewView


@pytest.mark.asyncio
async def test_app_starts_and_quits() -> None:
    """App should start, render, and exit cleanly."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        assert app.query_one(StatusBar) is not None
        # Files view now mounts FilePreviewView instead of PlaceholderView
        assert app.query_one("#view") is not None
        await pilot.press("q")


@pytest.mark.asyncio
async def test_initial_view_is_files() -> None:
    """Initial main content should show the Files view."""
    app = FileOrganizerApp()
    async with app.run_test():
        assert app._current_view == "files"


@pytest.mark.asyncio
async def test_switch_to_organized_view() -> None:
    """Switching to organized view should mount a PlaceholderView."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        await app.action_switch_view("organized")
        await pilot.pause()
        assert app._current_view == "organized"
        assert app.query_one("#view", PlaceholderView) is not None


@pytest.mark.asyncio
async def test_switch_to_settings_view() -> None:
    """Switching to settings view should mount a PlaceholderView."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        await app.action_switch_view("settings")
        await pilot.pause()
        assert app._current_view == "settings"
        assert app.query_one("#view", PlaceholderView) is not None


@pytest.mark.asyncio
async def test_switch_back_to_files_view() -> None:
    """Switching back to files should restore the FilePreviewView."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        await app.action_switch_view("settings")
        await pilot.pause()
        await app.action_switch_view("files")
        await pilot.pause()
        assert app._current_view == "files"


@pytest.mark.asyncio
async def test_status_bar_updates_on_view_switch() -> None:
    """StatusBar should update its internal message when switching views."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        await app.action_switch_view("organized")
        await pilot.pause()
        status = app.query_one(StatusBar)
        assert "Organized" in status._message


@pytest.mark.asyncio
async def test_help_action_updates_status() -> None:
    """Help action should update the status bar with help info."""
    app = FileOrganizerApp()
    async with app.run_test() as pilot:
        app.action_toggle_help()
        await pilot.pause()
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
