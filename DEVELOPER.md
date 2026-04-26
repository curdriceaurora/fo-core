# Developer Guide — fo-core

This document covers local setup, architecture, testing, and contribution workflow.
For end-user documentation see [README.md](README.md).

---

## Contents

- [Quick Setup](#quick-setup)
- [Architecture](#architecture)
- [Testing](#testing)
- [Code Quality](#code-quality)
- [Further Reading](#further-reading)

---

## Quick Setup

**Requirements**: Python 3.11+, Ollama

```bash
git clone https://github.com/curdriceaurora/fo-core.git
cd fo-core

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"            # installs fo + all dev/test deps
pre-commit install                 # sets up git hooks

ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M

fo doctor                          # verify everything is wired up
```

> **Note**: CI installs `.[dev,search]`. If you're working on search-related code, also run `pip install -e ".[search]"`.

---

## Architecture

### Pipeline

Every `fo organize` run passes files through four stages:

```
┌─────────────────────────────────────────────────────────────┐
│                    CLI  (src/cli/)                           │
└───────────────────────────┬─────────────────────────────────┘
                            │
         ┌──────────────────▼──────────────────┐
         │          Pipeline (src/pipeline/)    │
         │  preprocess → analyze → postprocess → write  │
         └──────────────────┬──────────────────┘
                            │
   ┌────────────────────────┼─────────────────────────┐
   │                        │                         │
   ▼                        ▼                         ▼
src/core/             src/services/            src/models/
(file ops,            (AI, search,             (Ollama, OpenAI,
 path guard,           dedup, audio,            Claude, llama.cpp,
 organizer)            copilot, etc.)           MLX)
```

### Key directories

| Path | What lives there |
|------|-----------------|
| `src/cli/` | Typer commands — one file per command group |
| `src/core/` | File operations, path safety, organizer logic |
| `src/pipeline/` | 4-stage orchestrator and resource-aware executor |
| `src/models/` | AI model abstractions (text, vision, audio) |
| `src/services/` | Feature services: search, dedupe, copilot, analytics, audio, auto-tag |
| `src/services/search/` | BM25 index, vector index, hybrid retriever |
| `src/undo/` | Journal, durable moves, rollback, trash GC |
| `src/daemon/` | Background file watcher, PID management, scheduler |
| `src/config/` | AppConfig schema, ConfigManager, migrations |
| `src/utils/` | File readers (PDF, DOCX, EPUB, archives, CAD, scientific), atomic writes |
| `tests/` | Mirrors `src/`; `tests/ci/` holds CI guardrail tests |

### Notable design choices

- **Atomic writes everywhere** — all persistent state (cache, history, config) uses `tempfile.NamedTemporaryFile` + `os.replace()` to prevent corruption on crash. Enforced by a pre-commit hook and CI guardrail.
- **PARA + Johnny Decimal** — built-in organizational methodologies (see `src/methodologies/`).
- **Optional dependencies** — search, media, dedup-image, etc. are guarded with `try/except ImportError` at module level; tests use `pytest.importorskip`.
- **Safe file traversal** — `safe_walk()` in `src/core/path_guard.py` filters symlinks and hidden files before any indexing operation.

Full architecture detail: [docs/developer/architecture.md](docs/developer/architecture.md)

---

## Testing

```bash
# Full CI suite (what CI runs on PRs)
pytest tests/ -m "ci and not benchmark" --timeout=30

# Just the fast CI guardrail tests
pytest tests/ci/ -q --no-cov

# A specific test file
pytest tests/services/copilot/test_copilot_executor.py -v

# With search extras available
pip install -e ".[search]"
pytest tests/ -m "ci" -k "search or retriever"
```

### Markers

| Marker | Use |
|--------|-----|
| `ci` | Runs on every PR |
| `unit` | Pure unit tests, no I/O |
| `integration` | Requires Ollama running |
| `benchmark` | Excluded from PR suite |
| `smoke` | Minimal sanity checks |
| `e2e` | Full end-to-end, excluded from PR suite |

### Pre-PR validation

Before opening a PR, run the full local gate:

```bash
bash .claude/scripts/pre-commit-validation.sh
```

This runs: `pre-commit run --all-files` → `pytest tests/ci/`.

---

## Code Quality

```bash
ruff check src/              # lint
ruff format src/ --check     # format check
mypy src/                    # type check (strict)
```

CI enforces all three. The pre-commit hooks run ruff and mypy automatically on changed files.

### Coverage gates

| Gate | Threshold | When |
|------|-----------|------|
| Unit (local `pytest`) | 95% line | Every run |
| PR diff coverage | 80% line | PRs with ≤75 changed files |
| Main branch push | 93% line | Merges to main |

---

## Further Reading

| Doc | Contents |
|-----|----------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branching, commit style, PR workflow |
| [docs/developer/architecture.md](docs/developer/architecture.md) | Component deep-dive |
| [docs/developer/testing.md](docs/developer/testing.md) | Test patterns, xdist, fixtures |
| [docs/developer/guardrails.md](docs/developer/guardrails.md) | CI guardrail system |
| [docs/developer/coverage-gates.md](docs/developer/coverage-gates.md) | Coverage gate details |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
