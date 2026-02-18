---
issue: 338
stream: Tests
agent: backend-specialist
started: 2026-02-18T07:04:22Z
updated: 2026-02-18T07:12:00Z
status: in_progress
---

# Stream C: Tests

## Scope
Integration tests verifying sandbox bypass is blocked. Test plugin that attempts forbidden operations must fail. Update existing tests for new executor.

## Files Created
- `tests/plugins/test_sandbox_isolation.py` — 15 tests across 3 groups (new)
- `tests/plugins/__init__.py` — package marker (new)
- `tests/plugins/fixtures/__init__.py` — fixtures package marker (new)
- `tests/plugins/fixtures/malicious_plugin/__init__.py` — malicious fixture package (new)
- `tests/plugins/fixtures/malicious_plugin/plugin.py` — MaliciousPlugin fixture (new)
- `src/file_organizer/plugins/__init__.py` — package re-exporting Plugin, PluginLoadError, PluginPermissionError (new)
- `src/file_organizer/plugins/base.py` — Plugin base class + exception hierarchy (new)
- `src/file_organizer/plugins/executor.py` — PluginExecutor stub documenting Stream A interface (new)

## Test Results
- 12 passed, 3 skipped (executor tests await Stream A)
- All files pass `ruff` linting

## Test Groups

### TestBypassAttempts (3 passing — run now)
- `test_plugin_cannot_call_os_system` — MaliciousPlugin fixture's os.system() call is intercepted
- `test_plugin_cannot_call_subprocess` — inline plugin's subprocess.run() call is blocked
- `test_plugin_cannot_open_forbidden_path` — file access outside allowed_paths raises PluginPermissionError

### TestExecutorInterface (3 skipped — awaiting Stream A)
Full assertion bodies written, marked `@pytest.mark.skip(reason="Waiting for executor implementation (Stream A)")`:
- `test_executor_starts_and_stops`
- `test_executor_call_returns_result`
- `test_executor_call_propagates_errors`

### TestIPCProtocol (9 passing — run now)
Uses existing `ipc.py` dataclass API (`PluginCall`, `PluginResult`, `encode_call`, `decode_call`, `encode_result`, `decode_result`):
- `test_encode_decode_call_roundtrip`
- `test_encode_decode_result_roundtrip`
- `test_result_with_error`
- `test_decode_call_rejects_invalid_bytes`
- `test_decode_result_rejects_invalid_bytes`
- `test_decode_call_rejects_missing_method`
- `test_encode_call_with_complex_args`
- `test_result_with_none_value`
- `test_plugin_call_default_args_and_kwargs`

## Next Steps
- Stream A completes PluginExecutor subprocess isolation
- Remove `@pytest.mark.skip` from TestExecutorInterface tests
- Optionally add `test_plugin_architecture.py` and `test_plugin_sdk.py` updates when Stream B sandbox profiles land
