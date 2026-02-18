---
issue: 338
title: "Security: Plugin Sandbox Bypass Risk (Plug-1)"
epic: technical-debt
analyzed: 2026-02-18T07:02:12Z
estimated_hours: 10
parallelization_factor: 2.5
---

# Parallel Work Analysis: Issue #338

## Overview

The current `PluginSandbox` enforces permissions via soft checks (`validate_file_access`, `validate_operation`) that plugins can trivially bypass by calling `os`, `subprocess`, or any stdlib module directly. The fix requires moving plugin execution into an isolated subprocess with OS-level enforcement, rather than relying on in-process policy checks.

**Root cause**: `PluginRegistry._load_module()` uses `importlib` to exec plugin code directly in the host process — the sandbox is advisory only.

**Fix strategy**: Run plugin code in a child process (via `multiprocessing` or a worker queue), communicating via a defined IPC protocol. The host process retains control; plugins cannot escape.

---

## Parallel Streams

### Stream A: Subprocess Isolation Layer
**Scope**: New module that runs plugin `on_load`, `on_enable`, `on_disable`, `on_unload`, and custom hook calls in a child process via `multiprocessing` or `concurrent.futures.ProcessPoolExecutor`. Defines the IPC protocol (e.g. serialized call/result messages).

**Files**:
- `src/file_organizer/plugins/executor.py` ← new
- `src/file_organizer/plugins/ipc.py` ← new (message schema)

**Agent Type**: backend-specialist
**Can Start**: immediately
**Estimated Hours**: 4
**Dependencies**: none

---

### Stream B: Registry Integration
**Scope**: Update `PluginRegistry` to use the new executor instead of calling plugin lifecycle methods directly. Replace in-process `_load_module` execution with out-of-process equivalent. Update `_build_sandbox` — sandbox policy is now passed to the child process as a constraint, not a Python object check.

**Files**:
- `src/file_organizer/plugins/registry.py` ← modify `load_plugin`, `_load_module`, `_instantiate_plugin`, `_build_sandbox`

**Agent Type**: backend-specialist
**Can Start**: after Stream A (needs executor interface)
**Estimated Hours**: 3
**Dependencies**: Stream A

---

### Stream C: Tests
**Scope**: Integration tests verifying bypass is actually blocked. A test plugin that attempts `os.system(...)`, `subprocess.run(...)`, and open of a forbidden path must fail. Update existing plugin tests that mock lifecycle hooks in-process to work with the new executor. Verification test per the issue's spec.

**Files**:
- `tests/plugins/test_sandbox_isolation.py` ← new
- `tests/plugins/test_plugin_architecture.py` ← update
- `tests/plugins/test_plugin_sdk.py` ← update
- `examples/plugins/` ← ensure examples still load correctly

**Agent Type**: backend-specialist
**Can Start**: Stream A interface defined (can stub executor); finalize after Stream B
**Estimated Hours**: 3
**Dependencies**: Stream A (interface), Stream B (full integration)

---

## Coordination Points

### Shared Files
- `src/file_organizer/plugins/registry.py` — Stream B only; Stream A does not touch this
- `src/file_organizer/plugins/base.py` — no changes needed; `Plugin` ABC stays the same
- `src/file_organizer/plugins/security.py` — `PluginSecurityPolicy` becomes a serializable config passed to child process; minor update possible in Stream A

### Sequential Requirements
1. Stream A must define the executor interface before Stream B integrates it
2. Stream C needs Stream B working for full integration tests; can write stubs against Stream A's interface earlier

---

## Conflict Risk Assessment

- **Low Risk**: Streams A and C work on new files — no conflicts
- **Low Risk**: Stream B is the only stream touching `registry.py`
- **Watch**: `security.py` may need minor serialization support — coordinate between A and B before touching

---

## Parallelization Strategy

**Recommended Approach**: Hybrid

1. Start **Stream A** immediately
2. Start **Stream C** stubs in parallel with Stream A (write test skeletons + the bypass verification plugin)
3. Start **Stream B** once Stream A's executor interface is defined (~4h in)
4. Finalize **Stream C** after Stream B completes

---

## Expected Timeline

With parallel execution:
- Wall time: ~7 hours (A=4h → B=3h overlap with C stubs → C finalize=2h)
- Total work: 10 hours
- Efficiency gain: ~30% vs sequential

Without parallel execution:
- Wall time: 10 hours

---

## Key Technical Decisions

1. **`multiprocessing` vs worker queue**: `multiprocessing.Process` is simplest for MVP; Celery/Redis adds operational overhead not justified here. Use `ProcessPoolExecutor` for reuse.

2. **IPC protocol**: Pickle is convenient but risky with untrusted plugins. Use a simple JSON-serialisable message schema for call/result. Plugin code runs in child; only results come back.

3. **Policy enforcement**: Pass `PluginSecurityPolicy` as a serialized config to the child process. Child process sets up `resource` limits (file descriptors, CPU) before executing plugin code.

4. **Backward compatibility**: `Plugin` ABC and `PluginMetadata` do not change. Existing plugins remain compatible.

5. **`PluginSandbox` fate**: Retain as a lightweight advisory layer inside the child process (defence in depth), but the OS-level isolation is the true enforcement.
