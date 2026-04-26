# fo-core

> Local AI file organizer. Point it at a directory — it categorizes and moves your files using a model running on your own machine. **Local by default**: Ollama runs on-device, no API key required. Cloud providers are optional extras.

[![CI](https://github.com/curdriceaurora/fo-core/actions/workflows/ci.yml/badge.svg)](https://github.com/curdriceaurora/fo-core/actions/workflows/ci.yml)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-2.0.0--alpha.3-orange)](CHANGELOG.md)

---

## Prerequisites

1. **Python 3.11 or later** — check with `python3 --version`
2. **Ollama** — install from [ollama.ai](https://ollama.ai), then start it:

   ```bash
   ollama serve
   ```

---

## Install

```bash
pip install fo-core
```

Then pull the default AI models (first-time only, ~4 GB total):

```bash
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M
```

Verify optional deps for your files:

```bash
fo doctor ~/Downloads
```

---

## Quick Start

```bash
# Preview what would happen — no files are moved
fo organize ~/Downloads ~/Organized --dry-run

# Run it for real
fo organize ~/Downloads ~/Organized

# Changed your mind?
fo undo
```

---

## Commands

| Command | What it does |
|---------|--------------|
| `fo organize [SRC] [DEST]` | Organize files using AI categorization |
| `fo preview [SRC]` | Dry-run preview without moving files |
| `fo search [QUERY]` | Full-text search across files |
| `fo analyze [DIR]` | File statistics and analysis |
| `fo dedupe` | Find and remove duplicate files |
| `fo suggest` | AI-powered organization suggestions |
| `fo autotag` | Auto-tag files based on content |
| `fo copilot` | Natural-language assistant |
| `fo rules` | Manage organization rules (YAML) |
| `fo config show\|list\|edit` | View or update configuration |
| `fo doctor [DIR]` | Scan a directory and recommend optional deps |
| `fo daemon start\|stop` | Background file watcher |
| `fo undo / redo / history` | Operation history and rollback |
| `fo model` | Select or inspect AI models |
| `fo benchmark` | Performance benchmarks |
| `fo setup` | Interactive setup wizard |

Full flag documentation: [docs/cli-reference.md](docs/cli-reference.md)

---

## AI Providers

**Default**: Ollama — runs entirely on your machine, no API key needed.

Cloud providers are optional extras:

| Provider | Install | Works with |
|----------|---------|------------|
| OpenAI-compatible | `pip install "fo-core[cloud]"` | OpenAI, LM Studio, vLLM, Groq |
| Anthropic Claude | `pip install "fo-core[claude]"` | Claude text + vision models |
| llama.cpp | `pip install "fo-core[llama]"` | GGUF models — no Ollama required |
| MLX (Apple Silicon) | `pip install "fo-core[mlx]"` | MLX-optimized local models |

---

## Optional Feature Packs

Core file types (PDF, DOCX, XLSX, PPTX, EPUB, ZIP) work out of the box. RAR also works but requires a system-level `unrar` or `unar` binary. Install extras for additional capabilities:

| Pack | Install | Adds |
|------|---------|------|
| `media` | `pip install "fo-core[media]"` | Audio transcription, video scene detection |
| `dedup-text` | `pip install "fo-core[dedup-text]"` | TF-IDF/cosine text deduplication |
| `dedup-image` | `pip install "fo-core[dedup-image]"` | Image similarity deduplication |
| `scientific` | `pip install "fo-core[scientific]"` | HDF5, NetCDF, MATLAB formats |
| `cad` | `pip install "fo-core[cad]"` | DXF/DWG CAD files |
| `search` | `pip install "fo-core[search]"` | BM25 + vector search |
| `all` | `pip install "fo-core[all]"` | All of the above |

---

## Configuration

Config lives in `~/.config/fo/config.yaml`. Override the location with the `FO_CONFIG` environment variable.

```bash
fo config show                                           # view all settings
fo config edit --text-model qwen2.5:3b-instruct-q4_K_M  # change text model
fo config edit --device auto                             # change device
```

Full configuration reference: [docs/CONFIGURATION.md](docs/CONFIGURATION.md)

---

## Documentation

| Doc | Contents |
|-----|----------|
| [Getting Started](docs/getting-started.md) | Installation options, first run, platform notes |
| [CLI Reference](docs/cli-reference.md) | Every command and flag |
| [Configuration](docs/CONFIGURATION.md) | All config keys explained |
| [FAQ](docs/faq.md) | Common questions and troubleshooting |
| [Troubleshooting](docs/troubleshooting.md) | Diagnosing connection and model issues |

---

## Contributing / Development

See [DEVELOPER.md](DEVELOPER.md) for architecture, local setup, testing, and contribution guidelines.

---

## License

MIT OR Apache-2.0
