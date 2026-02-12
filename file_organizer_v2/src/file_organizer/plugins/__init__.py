"""Plugin architecture primitives."""
from __future__ import annotations

from file_organizer.plugins.base import Plugin, PluginMetadata
from file_organizer.plugins.config import PluginConfig, PluginConfigManager
from file_organizer.plugins.errors import (
    HookExecutionError,
    PluginConfigError,
    PluginDependencyError,
    PluginDiscoveryError,
    PluginError,
    PluginLifecycleError,
    PluginLoadError,
    PluginNotFoundError,
    PluginNotLoadedError,
    PluginPermissionError,
)
from file_organizer.plugins.hooks import HookExecutionResult, HookRegistry
from file_organizer.plugins.lifecycle import PluginLifecycleManager, PluginState
from file_organizer.plugins.registry import PluginRecord, PluginRegistry
from file_organizer.plugins.security import PluginSandbox, PluginSecurityPolicy

__all__ = [
    "HookExecutionError",
    "HookExecutionResult",
    "HookRegistry",
    "Plugin",
    "PluginConfig",
    "PluginConfigError",
    "PluginConfigManager",
    "PluginDependencyError",
    "PluginDiscoveryError",
    "PluginError",
    "PluginLifecycleError",
    "PluginLifecycleManager",
    "PluginLoadError",
    "PluginMetadata",
    "PluginNotFoundError",
    "PluginNotLoadedError",
    "PluginPermissionError",
    "PluginRecord",
    "PluginRegistry",
    "PluginSandbox",
    "PluginSecurityPolicy",
    "PluginState",
]

