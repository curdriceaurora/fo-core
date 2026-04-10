# Administrator Guide

Complete guide for installing, configuring, and maintaining File Organizer.

## Quick Start

```bash
pip install -e .
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M
file-organizer doctor .
```

See [Installation Guide](installation.md) for detailed setup.

## Main Sections

- [Installation Guide](installation.md) - Setup and system requirements
- [Configuration](configuration.md) - Environment variables and settings
- [Troubleshooting](troubleshooting.md) - Common issues and solutions

## System Architecture

```text
┌─────────────────────────┐
│   CLI Interface (Typer) │
│   (Python)              │
│   - Rich terminal output│
│   - Subcommand groups   │
└──────┬──────────────────┘
       │
       ├─► File System (Storage)
       └─► Ollama (AI Models)
```

## Installation Checklist

- [ ] System meets minimum requirements (Python 3.11+, 8 GB RAM)
- [ ] Ollama installed and running
- [ ] Ollama models pulled
- [ ] Package installed (`pip install -e .`)
- [ ] Verified with `file-organizer doctor .`

## Maintenance Schedule

### Weekly

- Check Ollama model availability
- Monitor disk space for models

### Monthly

- Update Ollama models if available
- Update package dependencies

## Common Tasks

### Verify Installation

```bash
file-organizer doctor .
file-organizer --version
ollama ps
```

### Update Models

```bash
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M
```

## Support

- **Documentation**: Full guides for each topic
- **Issues**: [GitHub Issues](https://github.com/curdriceaurora/Local-File-Organizer/issues)
- **Discussions**: [GitHub Discussions](https://github.com/curdriceaurora/Local-File-Organizer/discussions)

## Next Steps

- [Installation Guide](installation.md)
- [Configuration Guide](configuration.md)
