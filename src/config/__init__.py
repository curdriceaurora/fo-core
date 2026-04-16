"""Unified configuration management for File Organizer."""

from __future__ import annotations

from config.manager import ConfigManager
from config.path_manager import PathManager
from config.path_migration import PathMigrator, detect_legacy_paths
from config.schema import AppConfig, ModelPreset, UpdateSettings

__all__ = [
    "AppConfig",
    "ConfigManager",
    "PathManager",
    "PathMigrator",
    "detect_legacy_paths",
    "ModelPreset",
    "UpdateSettings",
]
