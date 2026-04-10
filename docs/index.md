# File Organizer Documentation

Welcome to the **fo-core** documentation! A privacy-first, AI-powered CLI file organizer that categorizes files intelligently using local LLMs with zero cloud dependencies.

## Quick Navigation

=== "🚀 Getting Started"

    **New to fo-core?** Start here to understand the basics.

    - [Installation & Setup](getting-started.md)
    - [CLI Reference](cli-reference.md)

=== "👨‍💻 Development"

    **Extending fo-core?** Add methodologies, file handlers, and providers.

    - [Architecture](developer/architecture.md)
    - [Developer Guide](developer/index.md)

## Key Features

- 🔒 **Privacy-First**: 100% local processing, zero cloud dependencies
- 🤖 **AI-Powered**: Uses local LLMs for intelligent file organization
- 🎯 **Methodologies**: Supports PARA, Johnny Decimal, and custom organization systems
- 🔍 **Smart Search**: Full-text search with filters and saved searches
- 📊 **Analytics**: Storage analysis, duplicate detection, and insights
- 🔄 **Undo/Redo**: Reverse any operation instantly
- 🖥️ **CLI Interface**: Streamlined command-line workflow

## Supported File Types

fo-core processes **48+ file formats** including:

- **Documents**: PDF, Word, Excel, PowerPoint, Markdown, EPUB
- **Images**: JPEG, PNG, GIF, BMP, TIFF
- **Video**: MP4, AVI, MKV, MOV, WMV
- **Audio**: MP3, WAV, FLAC, M4A, OGG
- **Archives**: ZIP, 7Z, TAR, RAR
- **Scientific**: HDF5, NetCDF, MATLAB files
- **CAD**: DXF, DWG, STEP, IGES

## System Requirements

- **Python**: 3.11+
- **RAM**: 8 GB minimum (16 GB recommended)
- **Storage**: ~10 GB for AI models
- **Ollama**: Latest version for local inference

## Documentation Sections

### User Guides

- [Getting Started](getting-started.md) - Initial setup and overview
- [CLI Reference](cli-reference.md) - Command-line interface guide
- [Troubleshooting](troubleshooting.md) - Common issues and solutions

### Setup

- [AI Provider Setup](setup/ai-providers.md) - Configure Ollama, OpenAI, Claude, and more
- [Dependencies](setup/dependencies.md) - Installation and dependencies
- [Models](setup/models.md) - AI model configuration

### Configuration

- [Configuration](admin/configuration.md) - Environment setup
- [Audio & Video Processing](setup/audio-video.md) - Audio and video processing setup

### Development & Extension

- [Architecture Guide](developer/architecture.md) - System design
- [Developer Guide](developer/index.md) - Extending fo-core

## Getting Help

- **Issues**: Found a bug? [Report it on GitHub](https://github.com/curdriceaurora/fo-core/issues)
- **Discussions**: [Ask questions in discussions](https://github.com/curdriceaurora/fo-core/discussions)
- **Troubleshooting**: Check the [Troubleshooting Guide](troubleshooting.md)
- **FAQ**: Browse [Frequently Asked Questions](faq.md)

## Installation Quick Start

```bash
# Clone and install
git clone https://github.com/curdriceaurora/fo-core.git
cd fo-core
pip install -e .

# Pull AI models
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M

# Organize files
fo organize ~/Downloads ~/Organized --dry-run
fo organize ~/Downloads ~/Organized
```

See the [Getting Started Guide](getting-started.md) for detailed instructions.

## Documentation Updates

This documentation is maintained for fo-core `0.1.0`.

**Last Updated**: 2026-04-10
**Version**: 0.1.0

______________________________________________________________________

## License

fo-core is open source and available under the MIT OR Apache-2.0 License. See [LICENSE](https://github.com/curdriceaurora/fo-core/blob/main/LICENSE) for details.
