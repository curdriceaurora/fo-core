# Developer Guide

Extend and customize fo-core for your needs.

## Quick Start

### Clone Repository

```bash
git clone https://github.com/curdriceaurora/fo-core.git
cd fo-core
```

### Install Development Environment

```bash
pip install -e ".[dev]"
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M
```

## Main Sections

### Understanding the System

- [Architecture Guide](architecture.md) - System design and components
- Understanding the 4-stage pipeline (preprocess, analyze, postprocess, write)

### Extending fo-core

- Creating custom methodologies
- Adding new file type handlers

### Best Practices

- Code style and standards
- Pull request process
- Unit and integration testing
- Code style and standards (see [Contributing](contributing.md))
- [Guardrail Workflow](guardrails.md) - Where guardrails belong and how to add them

## Architecture

```text
fo-core v0.1
├── CLI (Typer)
│   ├── organize, preview, search, analyze
│   ├── dedupe, suggest, autotag, copilot
│   ├── daemon, rules, config, doctor
│   └── undo, redo, history
│
├── Core Engine
│   ├── File Processors
│   │   ├── Text Processor
│   │   ├── Vision Processor
│   │   └── Audio Processor
│   │
│   ├── Methodologies
│   │   ├── PARA
│   │   ├── Johnny Decimal
│   │   └── Custom
│   │
│   └── Services
│       ├── Analytics
│       ├── Deduplication
│       ├── Search
│       └── Intelligence
│
├── Storage
│   ├── SQLite Database
│   └── File System
│
└── AI Inference
    ├── Ollama (default, local)
    ├── OpenAI-compatible
    ├── Anthropic Claude
    ├── llama.cpp
    └── MLX (Apple Silicon)
```

## Key Files

**CLI**

- `src/file_organizer/cli/main.py` - Typer CLI application
- `src/file_organizer/cli/commands/` - CLI subcommands

**Core Engine**

- `src/file_organizer/core/` - Main orchestrator
- `src/file_organizer/services/` - Business logic
- `src/file_organizer/models/` - AI model interfaces

## Development Tasks

### Create Custom Methodology

```python
from file_organizer.methodologies import BaseMethodology

class CustomMethod(BaseMethodology):
    name = "custom"

    def categorize(self, file_path):
        # Your logic here
        return category
```

### Add File Type Support

1. Create reader in `utils/file_readers.py`
1. Register in processor
1. Add tests
1. Update documentation

## Testing

### Run Tests

```bash
pytest                      # All tests
pytest tests/unit/          # Unit tests only
pytest tests/ -v            # Verbose output
pytest tests/ --cov         # With coverage
```

### Write Tests

```python
import pytest

def test_my_feature():
    # Arrange
    data = setup_test_data()

    # Act
    result = my_function(data)

    # Assert
    assert result == expected_value
```

## Code Standards

### Python Style

- Follow PEP 8
- Use type hints
- Max line length 100 (Black)
- Sort imports (isort)

### Naming

- Classes: `PascalCase`
- Functions: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private: `_leading_underscore`

### Documentation

- Google-style docstrings
- Document public APIs
- Include usage examples

## Contributing

### Process

1. Fork repository
1. Create feature branch: `git checkout -b feature/my-feature`
1. Make changes with tests
1. Run quality checks
1. Commit with clear message
1. Push and create PR

### Quality Checks

```bash
ruff check .              # Linting
black .                   # Formatting
mypy .                    # Type checking
pytest                    # Tests
```

## Resources

### Documentation

- This Developer Guide
- Code comments and docstrings

### Community

- [GitHub Issues](https://github.com/curdriceaurora/fo-core/issues)
- [GitHub Discussions](https://github.com/curdriceaurora/fo-core/discussions)
- [GitHub Releases](https://github.com/curdriceaurora/fo-core/releases)

### Related Projects

- [Ollama](https://ollama.ai) - Local LLM inference
- [Typer](https://typer.tiangolo.com) - CLI framework

## Getting Help

### Documentation

- Read the architecture guide
- Review existing code

### Support

- GitHub Issues for bugs
- GitHub Discussions for questions
- Code comments in relevant files

## Next Steps

- [Architecture Guide](architecture.md)
