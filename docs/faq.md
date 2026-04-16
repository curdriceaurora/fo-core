# Frequently Asked Questions

## General Questions

### What is fo-core?

fo-core is an AI-powered CLI file organizer that automatically categorizes files using local LLMs (large language models). It supports multiple organization methodologies like PARA and Johnny Decimal, with zero cloud dependencies.

### Is my data safe?

Yes. fo-core:

- Runs 100% locally
- Never uploads files to cloud
- Uses local AI models
- Keeps all data on your device

### What are the system requirements?

- **Python**: 3.11+
- **RAM**: 8 GB minimum (16 GB recommended)
- **Storage**: 10 GB for AI models
- **Ollama**: Latest version

### Can I use it on Windows/Mac/Linux?

Yes. fo-core runs on all three platforms.

## Installation Questions

### How do I install fo-core?

```bash
git clone https://github.com/curdriceaurora/fo-core.git
cd fo-core
pip install -e .
```

See [Getting Started](getting-started.md).

### Do I need Ollama?

Yes by default. Ollama provides the AI models. Install from <https://ollama.ai>

Alternative providers (OpenAI, Claude, llama.cpp, MLX) are available via optional extras.

### Which AI models should I use?

We recommend:

- **Text**: qwen2.5:3b-instruct-q4_K_M (~1.9 GB)
- **Vision**: qwen2.5vl:7b-q4_K_M (~6 GB)

Both are optimized for balance between speed and accuracy.

## Usage Questions

### How do I organize my files?

```bash
fo organize ~/Downloads ~/Organized --dry-run   # Preview first
fo organize ~/Downloads ~/Organized              # Do it
fo undo                                          # Changed your mind
```

See the [Getting Started Guide](getting-started.md) for details.

### What file types does it support?

fo-core supports 48+ file types:

- Documents: PDF, Word, Excel, PowerPoint, Markdown, EPUB
- Images: JPEG, PNG, GIF, BMP, TIFF
- Video: MP4, AVI, MKV, MOV, WMV
- Audio: MP3, WAV, FLAC, M4A, OGG
- Archives: ZIP, 7Z, TAR, RAR
- Scientific: HDF5, NetCDF, MATLAB
- CAD: DXF, DWG, STEP, IGES

### How do I undo an organization?

```bash
fo undo       # Undo the last operation
fo history    # View operation history
fo redo       # Redo the last undone operation
```

### How do I find duplicate files?

```bash
fo dedupe ~/Documents    # Scan for duplicates
```

### How do I search files?

```bash
fo search "quarterly report" ~/Documents
```

## Performance Questions

### Organization is slow

Optimizations:

- Use smaller batches
- Close other applications
- Check available disk space
- Use GPU if available

### Memory usage is high

Solutions:

- Process smaller batches
- Use a smaller AI model
- Close other applications

## Configuration Questions

### How do I change configuration?

```bash
fo config show          # View current config
fo config set key val   # Update a setting
fo doctor               # Verify setup
```

Config file lives at `~/.config/fo/config.yaml`.

### Can I customize organization rules?

Yes. Use the `fo rules` command to manage YAML-based organization rules.

```bash
fo rules list           # List current rules
fo rules add            # Add a new rule
```

## Troubleshooting Questions

### Ollama connection fails

Start Ollama service:

```bash
ollama serve
```

Verify: `curl http://localhost:11434/api/version`

Run diagnostics: `fo doctor`

### Out of memory

Solutions:

- Increase available RAM
- Process smaller batches
- Use a smaller model
- Use CPU-only mode

See [Troubleshooting Guide](troubleshooting.md) for more issues.

## Contributing Questions

### How can I contribute?

1. Fork repository
1. Create feature branch
1. Make changes with tests
1. Create pull request

See [GitHub Repository](https://github.com/curdriceaurora/fo-core) for contribution guidelines.

### How do I report bugs?

1. Search existing issues
1. Create new issue with:
   - Clear description
   - Steps to reproduce
   - System info
   - Error logs

See [GitHub Issues](https://github.com/curdriceaurora/fo-core/issues).

## Getting Help

Can't find your answer?

- **Documentation**: Browse [full docs](index.md)
- **Issues**: [GitHub Issues](https://github.com/curdriceaurora/fo-core/issues)
- **Discussions**: [GitHub Discussions](https://github.com/curdriceaurora/fo-core/discussions)
- **Troubleshooting**: [Troubleshooting Guide](troubleshooting.md)
