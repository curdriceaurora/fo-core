# Testing & QA — Review and Improvement Plan

## Context

The Testing & QA epic is at 68% completion. Phases 2-5 (15/15 tasks) are complete, but
Phase 1 Foundation (8/9 tasks) remains "open." However, the project already has 227 test
files with ~3,968 test functions and extensive fixture infrastructure — meaning much of
the Phase 1 work may already exist organically.

**Goal**: Establish baseline, identify real gaps, fix failures, and improve coverage.

---

## Plan

### Step 1: Establish Baseline — Run Full Test Suite
- Run `pytest` with coverage reporting to establish current pass/fail and coverage numbers
- Identify failing tests, skip reasons, and error categories
- Generate coverage report to know exactly which modules are under-tested
- **Output**: Baseline metrics (pass count, fail count, coverage %)

### Step 2: Cross-Reference Phase 1 Tasks Against Existing Tests
- For each open Phase 1 task (001-008), check if corresponding test files already exist:
  - Task 001 (Test Infrastructure): Check conftest.py, fixtures, mock utilities
  - Task 002 (AI Model Abstractions): Check `tests/models/` for base model tests
  - Task 003 (Text Model): Check `tests/models/` for text model tests
  - Task 004 (Vision Model): Check `tests/models/` for vision model tests
  - Task 005 (File Readers): Check `tests/utils/` for file reader tests
  - Task 006 (Text Processing): Check `tests/utils/` for text processing tests
  - Task 007 (Text Processor Service): Check `tests/services/` for text processor tests
  - Task 008 (Vision Processor Service): Check `tests/services/` for vision processor tests
- **Output**: List of tasks that are actually done vs truly need work

### Step 3: Fix Failing Tests
- Triage failures by category (import errors, missing deps, logic bugs, stale tests)
- Fix import and configuration errors first (highest bang for buck)
- Fix logic bugs in test assertions
- Skip or mark tests that require external services (Ollama, GPU) with proper markers
- **Output**: Green test suite (or as close as possible)

### Step 4: Fill Coverage Gaps
- From the coverage report, identify modules with <50% coverage
- Prioritize core modules: `models/`, `services/`, `core/`, `utils/`
- Write new tests targeting uncovered critical paths
- Focus on edge cases and error handling paths
- **Output**: Improved coverage numbers on key modules

### Step 5: Close Out Epic Tasks
- For each Phase 1 task that's now covered, verify and document
- Update epic tracking if appropriate
- Commit all changes with clear messages

---

## Approach Notes

- Work on branch `claude/review-and-plan-NIDeR`
- Run tests without Ollama/GPU (mock AI model calls where needed)
- Focus on test quality over quantity — meaningful assertions, not just line coverage
- Don't over-engineer test infrastructure — leverage what exists
