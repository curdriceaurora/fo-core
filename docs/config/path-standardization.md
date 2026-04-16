# Path Standardization & Migration Guide

## Overview

fo-core standardizes application paths following the **XDG Base Directory Specification**, replacing legacy hardcoded paths with a centralized, configurable path management system.

## Key Changes

### New: Centralized PathManager

The `PathManager` class provides unified access to all application paths:

```python
from config.path_manager import PathManager

path_manager = PathManager()

# Access standard directories
config_dir = path_manager.config_dir          # ~/.config/fo (XDG_CONFIG_HOME)
data_dir = path_manager.data_dir              # ~/.local/share/fo (XDG_DATA_HOME)
state_dir = path_manager.state_dir            # ~/.local/state/fo (XDG_STATE_HOME)
cache_dir = path_manager.cache_dir            # data_dir/cache

# Access specific files
config_file = path_manager.config_file        # config_dir/config.json
preferences_file = path_manager.preferences_file  # config_dir/preferences.json
history_db = path_manager.history_db          # data_dir/history/operations.db
undo_redo_db = path_manager.undo_redo_db      # state_dir/undo-redo.db
```

### XDG Base Directory Specification

The new system respects XDG environment variables with sensible fallbacks:

| Variable | Default | Purpose |
|----------|---------|---------|
| `XDG_CONFIG_HOME` | `~/.config` | User-specific configuration files |
| `XDG_DATA_HOME` | `~/.local/share` | User-specific data files |
| `XDG_STATE_HOME` | `~/.local/state` | User-specific state/cache data |

### Legacy Paths (Deprecated)

The following legacy paths are now deprecated and should not be used:

| Old Path | New Path | Notes |
|----------|----------|-------|
| `~/.config/fo` | `~/.config/fo` | Still supported for config |
| `~/.fo` | `~/.local/share/fo` | Data files moved to data_dir |
| `~/.fo` | `~/.local/share/fo` | Underscore variant (legacy typo) |

## Migration Guide

### For End Users

fo-core automatically migrates from legacy paths:

1. **First Run**: The application detects legacy paths and creates a backup
2. **Migration**: Files are copied to new XDG-compliant locations
3. **Backup**: Original files are preserved with timestamp suffix (e.g., `.backup.20260227_143022_123456`)

To manually trigger migration:

```bash
fo config migrate --from-legacy
```

### For Developers

#### Using PathManager in New Code

Always use PathManager for path access:

```python
from config.path_manager import PathManager

path_manager = PathManager()

# Ensure all directories exist
path_manager.ensure_directories()

# Save configuration
config_file = path_manager.config_file
config_file.parent.mkdir(parents=True, exist_ok=True)
config_file.write_text(config_yaml)

# Access data directories
data_file = path_manager.data_dir / "mydata.json"
```

#### Updating Existing Code

Replace hardcoded paths with PathManager:

**Before (Legacy):**

```python
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "fo"
config_path = DEFAULT_CONFIG_DIR / "config.json"
```

**After (New):**

```python
from config.path_manager import PathManager

path_manager = PathManager()
config_path = path_manager.config_file
```

#### Module Integration

Modules that manage their own paths should accept a PathManager parameter:

```python
from config.path_manager import PathManager

class MyService:
    def __init__(self, path_manager: PathManager | None = None):
        self.path_manager = path_manager or PathManager()
        self.data_dir = self.path_manager.data_dir / "myservice"
        self.data_dir.mkdir(parents=True, exist_ok=True)
```

#### ConfigManager & PreferenceStore

Both classes support custom path parameters:

```python
from config import ConfigManager, PathManager
from services.intelligence.preference_store import PreferenceStore

path_manager = PathManager()
path_manager.ensure_directories()

# ConfigManager with PathManager
config_manager = ConfigManager(config_dir=path_manager.config_dir)

# PreferenceStore with PathManager
pref_store = PreferenceStore(storage_path=path_manager.data_dir / "preferences")
```

## Migration Classes

### PathManager

- **Purpose**: Unified interface for all application paths
- **Location**: `config.path_manager`
- **Key Methods**:
  - `ensure_directories()`: Create all necessary directories
  - `get_path(category)`: Get path by category name

### PathMigrator

- **Purpose**: Migrate files from legacy to canonical paths
- **Location**: `config.path_migration`
- **Features**:
  - Automatic backup creation with timestamp
  - Safe file copying with metadata preservation
  - Migration logging for audit trail
  - Rollback support via backups

### detect_legacy_paths()

- **Purpose**: Detect legacy path locations
- **Returns**: List of legacy paths that exist
- **Checks**:
  - `~/.fo` (legacy hyphen variant)
  - `~/.fo` (legacy underscore variant)
  - `~/.config/fo` (old canonical location)

## Backwards Compatibility

All existing code continues to work during the transition:

✅ Legacy paths are auto-migrated on first run
✅ ConfigManager and PreferenceStore work with both old and new paths
✅ Default fallbacks maintain compatibility

## Environment Variables

Configure paths using environment variables:

```bash
# Use custom config directory
export XDG_CONFIG_HOME=/custom/config
fo config list

# Use custom data directory
export XDG_DATA_HOME=/custom/data
fo analyze

# Use custom state directory
export XDG_STATE_HOME=/custom/state
fo daemon start
```

## Testing Path Configuration

Verify your path configuration:

```bash
# Show current paths
fo config paths

# Show path debug info
fo config paths --verbose

# Show migration status
fo config migration-status
```

## Troubleshooting

### Files Not Found After Migration

If files aren't found after migration:

1. Check backup location: `ls -la ~/.fo.backup.*`
2. Manually restore: `cp -r ~/.fo.backup.TIMESTAMP/* ~/.local/share/fo/`
3. Report issue with migration log

### Permission Denied Errors

If you see permission errors:

```bash
# Fix directory permissions
chmod -R 755 ~/.config/fo
chmod -R 755 ~/.local/share/fo
chmod -R 755 ~/.local/state/fo
```

### XDG Variables Not Working

Ensure environment variables are set before launching:

```bash
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_DATA_HOME="$HOME/.local/share"
export XDG_STATE_HOME="$HOME/.local/state"
fo
```

## See Also

- **XDG Base Directory Specification**: https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
- **Path Manager Implementation**: `src/config/path_manager.py`
- **Path Migration**: `src/config/path_migration.py`
- **Integration Tests**: `tests/integration/config/test_path_integration.py`
