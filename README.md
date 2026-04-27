# fo-core

> Streamlined CLI file organizer powered by local AI. Ollama-first, no cloud required.

## What it does

Point it at a directory, and it uses local AI to categorize and organize your files into a clean folder structure.

```bash
fo organize ~/Downloads ~/Organized --dry-run   # preview first
fo organize ~/Downloads ~/Organized              # do it
fo undo                                          # changed your mind
```

## Install

```bash
pip install -e .

# Pull AI models
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M
```

All document parsers (PDF, DOCX, XLSX, PPTX, EPUB) are included by default. No extras needed for core use.

## CLI Commands

```
fo organize [DIR] [OUTPUT]    Organize files using AI categorization
fo preview [DIR]              Dry-run preview
fo search [QUERY]             Full-text search across files
fo analyze [DIR]              File statistics and analysis
fo dedupe                     Find and remove duplicates
fo suggest                    AI-powered organization suggestions
fo autotag                    Auto-tag files based on content
fo copilot                    Natural-language assistant
fo rules                      Manage organization rules (YAML)
fo config                     Show/set configuration
fo doctor                     Check Ollama connection and deps
fo daemon start|stop          Background file watcher
fo undo / redo / history      Operation history
fo model                      Model selection
fo profile                    Hardware profiling
fo benchmark                  Performance benchmarks
fo setup                      Interactive setup wizard
fo version                    Show version
```

## AI Providers

**Default**: Ollama (local, private, no API key needed)

Optional cloud providers via extras:

| Provider | Install | Models |
|----------|---------|--------|
| OpenAI-compatible | `pip install -e ".[cloud]"` | OpenAI, LM Studio, vLLM, Groq |
| Anthropic Claude | `pip install -e ".[claude]"` | Claude (text + vision) |
| llama.cpp | `pip install -e ".[llama]"` | GGUF models, no Ollama needed |
| MLX (Apple Silicon) | `pip install -e ".[mlx]"` | MLX-optimized models |

## Optional Feature Packs

| Pack | Install | What it adds |
|------|---------|-------------|
| Media | `pip install -e ".[media]"` | Audio transcription + video scene detection |
| Dedup text | `pip install -e ".[dedup-text]"` | TF-IDF/cosine text deduplication |
| Dedup image | `pip install -e ".[dedup-image]"` | Image similarity deduplication |
| Scientific | `pip install -e ".[scientific]"` | HDF5, NetCDF, MATLAB formats |
| CAD | `pip install -e ".[cad]"` | DXF/DWG support |
| Search | `pip install -e ".[search]"` | BM25 + vector search |
| All | `pip install -e ".[all]"` | Everything above |

## Under the Hood

fo-core keeps the full engine from [Local-File-Organizer](https://github.com/curdriceaurora/Local-File-Organizer) with the UI surfaces stripped:

- **4-stage pipeline**: preprocess, analyze (AI), postprocess, write
- **PARA + Johnny Decimal**: Built-in organizational methodologies
- **Intelligence**: Pattern learning, preference tracking, smart suggestions
- **Auto-tagging**: Content-aware tag recommendations
- **Deduplication**: Content-hash + semantic duplicate detection
- **Copilot**: Natural-language conversation engine (CLI)
- **Daemon**: Background file watching with configurable rules
- **Undo/Redo**: Full operation history with rollback

## Configuration

Config lives in `~/.config/fo/config.yaml`. Override with `FO_CONFIG` env var.

```bash
fo config show          # view current config
fo config set key val   # update a setting
fo doctor               # verify setup
```

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src/
```

## Releases

Currently `2.0.0-alpha.3`. The criteria for promoting to beta and the contract
with public pre-release testers are documented in
[docs/release/beta-criteria.md](docs/release/beta-criteria.md).

## License

MIT OR Apache-2.0
