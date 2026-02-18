---
issue: 338
stream: Registry Integration
agent: backend-specialist
started: 2026-02-18T07:15:00Z
updated: 2026-02-18T07:20:37Z
status: completed
---

# Stream B: Registry Integration

## Scope
Update `PluginRegistry` to use `PluginExecutor` for all plugin lifecycle calls instead of invoking them directly in-process.

## Files Changed

- `file_organizer_v2/src/file_organizer/plugins/registry.py` — **created** (new file)
  - `PluginRecord` dataclass: `name`, `version`, `plugin_path`, `policy`, `plugin` (in-process instance for metadata), `executor` (PluginExecutor)
  - `PluginRegistry.load_plugin()`: in-process metadata extraction via `_load_module` / `_instantiate_plugin`, then spawns `PluginExecutor`, calls `executor.call("on_load")` through IPC
  - `PluginRegistry.unload_plugin()`: calls `executor.call("on_unload")` then `executor.stop()`
  - `PluginRegistry._build_sandbox()`: retained for constructing `PluginSecurityPolicy` from plugin's declared `allowed_paths`
  - `PluginRegistry.enable_plugin()` / `disable_plugin()`: routed through executor (disable delegates to unload)
  - `PluginRegistry.call_all()`: routes a method call to all loaded plugins via executors
  - `PluginRegistry.unload_all()`: teardown for all plugins

- `file_organizer_v2/src/file_organizer/plugins/executor.py` — **updated**
  - `PluginExecutor.__init__` now accepts `plugin_path: Path | str`, and makes `plugin_name` and `policy` optional with sensible defaults (stem of path, unrestricted policy)
  - `PluginExecutor.call()` now raises `PluginLoadError` specifically for `on_load` failures (vs generic `PluginError` for other methods)

- `file_organizer_v2/tests/plugins/test_sandbox_isolation.py` — **updated**
  - Removed `@pytest.mark.skip` from 3 executor interface tests:
    - `test_executor_starts_and_stops`
    - `test_executor_call_returns_result`
    - `test_executor_call_propagates_errors`

## Test Results

All 15 plugin tests pass (0 skipped):
- 3 bypass-attempt tests: PASSED
- 3 executor interface tests: PASSED (previously skipped)
- 9 IPC protocol tests: PASSED

## Completed
- 2026-02-18T07:20:37Z
