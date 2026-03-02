---
issue: 338
stream: Subprocess Isolation Layer
agent: backend-specialist
started: 2026-02-18T07:04:22Z
updated: 2026-02-18T07:11:21Z
status: completed
---

# Stream A: Subprocess Isolation Layer

## Scope

Create the subprocess executor and IPC protocol modules that isolate plugin execution from the host process.

## Files

- `src/file_organizer/plugins/executor.py` ← implemented
- `src/file_organizer/plugins/ipc.py` ← implemented
- `src/file_organizer/plugins/security.py` ← implemented (new; required by executor)

## Completed

### ipc.py — JSON IPC Protocol

- `PluginCall` dataclass: `method` (str), `args` (list), `kwargs` (dict)
- `PluginResult` dataclass: `success` (bool), `return_value` (Any), `error` (str | None)
- `encode_call(call: PluginCall) -> bytes` — newline-terminated JSON
- `decode_call(data: bytes) -> PluginCall` — with ValueError on bad input
- `encode_result(result: PluginResult) -> bytes`
- `decode_result(data: bytes) -> PluginResult`
- JSON-only; no pickle anywhere

### security.py — Plugin Security Policy

- `PluginSecurityPolicy` frozen dataclass with `allowed_paths`, `allowed_operations`, `allow_all_paths`, `allow_all_operations`
- `unrestricted()` classmethod for fully permissive policy
- `from_permissions()` classmethod for user-configurable policy
- Fields serialisable as a plain dict for JSON IPC bootstrap

### executor.py — Subprocess Isolation Executor

- `_worker(plugin_path, policy_dict)` — child-process entrypoint:
  - Applies `RLIMIT_NOFILE=64` and `RLIMIT_CPU=60s` via `resource` module (Linux/macOS, best-effort)
  - Dynamically loads plugin module with `importlib.util.spec_from_file_location`
  - Finds first concrete `Plugin` subclass and instantiates it
  - NDJSON read loop: decode PluginCall → dispatch → encode PluginResult
- `PluginExecutor` class:
  - `__init__(plugin_path, plugin_name, policy)` — stores config
  - `start()` — spawns `subprocess.Popen` with `stdin=PIPE, stdout=PIPE`
  - `stop()` — graceful terminate with 5 s timeout, then kill
  - `__enter__` / `__exit__` — context manager lifecycle
  - `call(method, *args, **kwargs)` — RPC over pipes, raises `PluginError` on failure

## Key Constraints Met

- No pickle — JSON only for all IPC
- Plugin ABC in `base.py` NOT modified
- Full type hints and Google-style docstrings on all public methods
- Passes `ruff check` with zero errors

## Commit

`0f62919` — Issue #338: Add subprocess isolation executor and JSON IPC protocol
