---
name: stream-A
stream: A
issue: 339
title: File Reading DoS Risk - File Size Gate
status: completed
created: 2026-02-18T13:43:10Z
updated: 2026-02-18T13:43:10Z
---

# Stream A: File Size Gate — Issue #339

## Summary

Added a file size gate to `file_organizer_v2/src/file_organizer/utils/file_readers.py`
to prevent DoS attacks via zip bombs or excessively large files.

## Changes Made

### New Constants and Classes (file_readers.py)

- **`MAX_FILE_SIZE_BYTES`**: 500 MB hard limit constant (configurable via the `max_bytes` parameter on `_check_file_size`)
- **`FileTooLargeError(OSError)`**: New exception class raised when a file exceeds the size limit
- **`_check_file_size(file_path, max_bytes)`**: Helper function that stats the file and raises `FileTooLargeError` if it exceeds `max_bytes`. Silently returns on OSError so individual readers can surface missing-file errors.

### Functions Patched

| Function | Line | Change |
|---|---|---|
| `read_file()` | ~792 | `_check_file_size` added as first statement |
| `read_docx_file()` | ~173 | `_check_file_size` added before `Path(file_path)` |
| `read_presentation_file()` | ~273 | `_check_file_size` added before `Path(file_path)` |
| `read_ebook_file()` | ~314 | `_check_file_size` added before `Path(file_path)` |
| `read_tar_file()` | ~490 | `_check_file_size` added before `Path(file_path)` |

### Export (utils/__init__.py)

`FileTooLargeError` added to `file_organizer.utils` public exports.

## Validation

- `ruff check` passed with no issues
- Import check passed: `from file_organizer.utils.file_readers import FileTooLargeError, _check_file_size, read_file`

## Commit

`1c25d5c` — Issue #339: Add file size gate to prevent DoS via oversized files
