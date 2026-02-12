"""Reference plugin used for development and integration checks."""
from __future__ import annotations

from file_organizer.plugins import Plugin, PluginMetadata


class HelloWorldPlugin(Plugin):
    """Minimal plugin demonstrating lifecycle handlers."""

    def get_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="hello_world",
            version="1.0.0",
            author="Local File Organizer Team",
            description="Reference plugin for lifecycle and registry checks.",
        )

    def on_load(self) -> None:
        return None

    def on_enable(self) -> None:
        return None

    def on_disable(self) -> None:
        return None

    def on_unload(self) -> None:
        return None

