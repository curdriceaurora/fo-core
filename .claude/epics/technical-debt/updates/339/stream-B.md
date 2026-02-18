---
stream: Stream B — Unit Tests
agent: claude-sonnet-4-6
issue: 339
started: 2026-02-18T13:45:28Z
updated: 2026-02-18T13:45:28Z
status: completed
---

# Stream B — Issue #339: File Size Gate Unit Tests

## Summary

Stream B delivered 12 unit tests covering the file size gate introduced by
Stream A in `file_organizer/utils/file_readers.py`.

## Status: Completed

All 12 tests pass against the landed Stream A implementation.

## Commit

`b1a8846` — Issue #339: Add unit tests for file size limit DoS prevention

## Files Created

- `tests/utils/__init__.py` (new package init)
- `tests/utils/test_file_size_limit.py` (221 lines, 12 tests)

## Test Classes

### TestCheckFileSizeHelper (6 tests)

| Test | Result |
|------|--------|
| test_small_file_passes | PASSED |
| test_file_at_limit_passes | PASSED |
| test_file_over_limit_raises | PASSED |
| test_custom_limit | PASSED |
| test_stat_oserror_is_ignored | PASSED |
| test_error_message_contains_size_info | PASSED |

### TestReadFileDispatcherSizeGate (2 tests)

| Test | Result |
|------|--------|
| test_read_file_rejects_oversized | PASSED |
| test_read_file_passes_normal_size | PASSED |

### TestUnboundedReadersSizeGate (4 parametrized tests)

| Test | Result |
|------|--------|
| test_reader_rejects_oversized[read_docx_file-docx] | PASSED |
| test_reader_rejects_oversized[read_presentation_file-pptx] | PASSED |
| test_reader_rejects_oversized[read_ebook_file-epub] | PASSED |
| test_reader_rejects_oversized[read_tar_file-tar] | PASSED |

## Key Design Decisions

1. **Skip-guard pattern**: Tests include an import-guard that activates
   `@pytest.mark.skipif` automatically if Stream A symbols are not yet
   importable — allowing both streams to be committed independently without
   failures blocking CI.

2. **Stat mocking via `patch.object(Path, "stat")`**: Tests mock at the
   `Path.stat` level so no real files need to be large; avoids disk I/O
   in the test suite.

3. **Boundary test at exact MAX_FILE_SIZE_BYTES**: Verifies the gate uses
   `>` (strictly over limit) not `>=` (would reject boundary-sized files).

4. **OSError passthrough**: Confirms the helper does not raise on
   `os.stat` failure — the underlying reader handles missing/unreadable
   files.

## Stream A Verification

Stream A confirmed landed with:
- `FileTooLargeError` at `file_organizer.utils.file_readers`
- `MAX_FILE_SIZE_BYTES = 524288000` (500 MB)
- `_check_file_size()` helper
- Size gates in `read_file()`, `read_docx_file()`, `read_presentation_file()`,
  `read_ebook_file()`, `read_tar_file()`
