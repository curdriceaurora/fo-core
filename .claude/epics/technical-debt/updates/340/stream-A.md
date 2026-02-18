---
name: stream-A-issue-340
stream: A
issue: 340
title: Security — Insecure Default JWT Secret (Auth-1)
status: completed
created: 2026-02-18T13:46:31Z
updated: 2026-02-18T13:46:31Z
---

# Stream A — Issue #340: SecretStr for auth_jwt_secret

## Summary

Fixed the insecure default JWT secret by converting the `auth_jwt_secret`
field in `ApiSettings` from a plain `str` to `pydantic.SecretStr`.

## Changes Made

### 1. `src/file_organizer/api/config.py`
- Added `SecretStr` to the `pydantic` import line.
- Changed field declaration from:
  `auth_jwt_secret: str = "change-me"`
  to:
  `auth_jwt_secret: SecretStr = SecretStr("change-me")`
- Updated the production-guard comparison in `load_settings()` to use
  `.get_secret_value()` so the `== "change-me"` check still works.

### 2. `src/file_organizer/api/auth.py`
- Updated `_build_token()`: changed `settings.auth_jwt_secret` →
  `settings.auth_jwt_secret.get_secret_value()` in the `jwt.encode()` call.
- Updated `decode_token()`: changed `settings.auth_jwt_secret` →
  `settings.auth_jwt_secret.get_secret_value()` in the `jwt.decode()` call.

### 3. `tests/unit/api/test_config_security.py` (new)
Five tests added:
- `test_jwt_secret_not_in_repr` — asserts the raw value is masked in repr/str.
- `test_jwt_secret_accessible_via_get_secret_value` — asserts `.get_secret_value()` returns the correct value.
- `test_jwt_secret_field_is_secret_str` — asserts the field type is `SecretStr`.
- `test_jwt_secret_default_masked_in_repr` — verifies the default "change-me" is also masked.
- `test_jwt_secret_default_accessible_via_get_secret_value` — verifies default value retrieval.

## Test Results

All 5 new tests passed. All 17 existing unit/api tests continued to pass.
Ruff linting: no issues on changed files.

## Commit

`e0afb51` — Issue #340: Use SecretStr for auth_jwt_secret to prevent log leakage

## Notes

- Pydantic v2 automatically coerces plain strings to `SecretStr`, so
  callers that pass `auth_jwt_secret="some-string"` (e.g., `test_utils.py`)
  continue to work without any changes.
- The existing production-guard validation in `load_settings()` was preserved;
  only the comparison expression was updated to call `.get_secret_value()`.
