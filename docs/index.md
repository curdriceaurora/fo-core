# File Organizer Documentation

Welcome to the **File Organizer** documentation! A privacy-first, AI-powered local file management system that organizes files intelligently using local LLMs with zero cloud dependencies.

## Quick Navigation

=== "🚀 Getting Started"

    **New to File Organizer?** Start here to understand the basics.

    - [Installation & Setup](getting-started.md)
    - [CLI Reference](cli-reference.md)

=== "🔧 Deployment"

    **Running your own instance?** Deploy and configure File Organizer.

    - [Installation](admin/installation.md)
    - [Deployment](admin/deployment.md)
    - [Configuration](admin/configuration.md)
    - [Monitoring](admin/monitoring.md)

=== "👨‍💻 Development"

    **Extending File Organizer?** Build plugins and integrations.

    - [Architecture](developer/architecture.md)
    - [API Clients](developer/api-clients.md)

## Key Features

- 🔒 **Privacy-First**: 100% local processing, zero cloud dependencies
- 🤖 **AI-Powered**: Uses local LLMs for intelligent file organization
- 🎯 **Methodologies**: Supports PARA, Johnny Decimal, and custom organization systems
- 🔍 **Smart Search**: Full-text search with filters and saved searches
- 📊 **Analytics**: Storage analysis, duplicate detection, and insights
- 🔄 **Undo/Redo**: Reverse any operation instantly
- 🎨 **Multiple Interfaces**: CLI, Terminal UI, and native desktop app
- 🔌 **Extensible**: Plugin system for custom functionality

## Supported File Types

File Organizer processes **48+ file formats** including:

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

### Deployment & Administration

- [Installation](admin/installation.md) - Setup instructions
- [Deployment Guide](admin/deployment.md) - Production deployment
- [Configuration](admin/configuration.md) - Environment setup
- [Audio & Video Processing](setup/audio-video.md) - Audio and video processing setup
- [Security](admin/security.md) - Security best practices
- [Monitoring](admin/monitoring.md) - Health checks and logging

### Development & Extension

- [Architecture Guide](developer/architecture.md) - System design
- [API Clients](developer/api-clients.md) - Client libraries

## Getting Help

- **Issues**: Found a bug? [Report it on GitHub](https://github.com/curdriceaurora/Local-File-Organizer/issues)
- **Discussions**: [Ask questions in discussions](https://github.com/curdriceaurora/Local-File-Organizer/discussions)
- **Troubleshooting**: Check the [Troubleshooting Guide](troubleshooting.md)
- **FAQ**: Browse [Frequently Asked Questions](faq.md)

## Installation Quick Start

=== "Docker (Recommended)"

    ```bash
    git clone https://github.com/curdriceaurora/Local-File-Organizer.git
    cd Local-File-Organizer
    docker-compose up -d
    ```

    Access at `http://localhost:8000`

=== "Python Package"

    ```bash
    pip install local-file-organizer
    file-organizer serve
    ```

=== "From Source"

    ```bash
    git clone https://github.com/curdriceaurora/Local-File-Organizer.git
    cd Local-File-Organizer
    pip install -e .
    file-organizer serve
    ```

See the [Installation Guide](admin/installation.md) for detailed instructions.

## Documentation Updates

This documentation is maintained for File Organizer `2.0.0-alpha.3`. For older versions, check the [GitHub releases](https://github.com/curdriceaurora/Local-File-Organizer/releases).

**Last Updated**: 2026-04-05
**Version**: 2.0.0-alpha.3

______________________________________________________________________

## License

File Organizer is open source and available under the MIT License. See [LICENSE](https://github.com/curdriceaurora/Local-File-Organizer/blob/main/LICENSE) for details.
