# File Organizer User Guide

## Introduction

File Organizer is an AI-powered file management system built with a privacy-first, local-first architecture. By default it uses local LLMs through Ollama; optional cloud providers can be enabled explicitly via provider configuration.

## Installation

### Prerequisites

- Python 3.11 or higher
- [Ollama](https://ollama.ai/) installed and running
- 8 GB RAM minimum (16 GB recommended)
- ~10 GB disk space for AI models

### Setup

```bash
# Clone the repository
git clone https://github.com/curdriceaurora/fo-core.git
cd fo-core

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install the package
pip install -e .

# Pull the required AI models
ollama pull qwen2.5:3b-instruct-q4_K_M    # Text model (~1.9 GB)
ollama pull qwen2.5vl:7b-q4_K_M           # Vision model (~6.0 GB)

# Verify the installation
fo version
```

### Optional Feature Packs

Extend functionality by installing optional dependency groups:

!!! tip
    For a complete feature-to-install matrix with platform-specific notes, see [Optional Features](getting-started.md#optional-features) in the Getting Started guide.

| Pack | Install Command | Features |
|------|----------------|----------|
| Search | `pip install -e ".[search]"` | BM25-based search ranking algorithms |
| Cloud | `pip install -e ".[cloud]"` | OpenAI-compatible API providers (OpenAI, Groq, LM Studio, vLLM) |
| Claude | `pip install -e ".[claude]"` | Anthropic Claude API provider (text + vision) |
| LLaMA | `pip install -e ".[llama]"` | Local llama.cpp inference (GGUF models, no Ollama needed) |
| MLX | `pip install -e ".[mlx]"` | Apple Silicon MLX acceleration for faster local inference |
| Media | `pip install -e ".[media]"` | Audio transcription + video scene detection (faster-whisper, OpenCV) |
| Dedup text | `pip install -e ".[dedup-text]"` | TF-IDF/cosine text deduplication (scikit-learn) |
| Dedup image | `pip install -e ".[dedup-image]"` | Image deduplication (perceptual hashing) |
| Scientific | `pip install -e ".[scientific]"` | HDF5, NetCDF, MATLAB file support |
| CAD | `pip install -e ".[cad]"` | DXF and other CAD format support |
| Build | `pip install -e ".[build]"` | Executable packaging (PyInstaller) |
| All | `pip install -e ".[all]"` | All optional extras above |

!!! note
    The audio and video packs require FFmpeg and optionally a CUDA-capable GPU. See the [Audio & Video Setup Guide](setup/audio-video.md) for detailed installation instructions, model selection, and configuration.

## CLI Commands Overview

File Organizer provides two equivalent entrypoints: `fo` and the short alias `fo`.

| Command | Description |
|---------|-------------|
| `organize` | Organize files from an input directory to an output directory |
| `preview` | Preview organization changes without moving files |
| `search` | Search files by filename pattern/keyword with optional `--type` filter |
| `analyze` | Analyze a file and display AI-generated metadata |
| `doctor` | Check Ollama connection and dependencies |
| `undo` | Undo the last file operation |
| `redo` | Redo a previously undone operation |
| `history` | Show operation history |
| `analytics` | Display storage analytics and insights |
| `version` | Show the application version |
| `config` | View and edit configuration |
| `model` | Manage AI models |
| `autotag` | Auto-tagging suggestions and batch operations |
| `copilot` | AI assistant for file management questions |
| `daemon` | Background file watching and auto-organization |
| `dedupe` | Find and resolve duplicate files |
| `rules` | Manage custom organization rules |
| `suggest` | Get smart file placement suggestions |
| `update` | Check for and install application updates |
| `profile` | Manage named configuration profiles |
| `benchmark` | Run performance benchmarks |
| `setup` | Interactive setup wizard |

## Organizing Files

### Basic Organization

The `organize` command analyzes files in an input directory and moves them to categorized folders in an output directory:

```bash
# Dry run first to preview changes
fo organize ~/Downloads ~/Organized --dry-run

# Run the actual organization
fo organize ~/Downloads ~/Organized

# Verbose output for debugging
fo organize ~/Downloads ~/Organized --verbose
```

### Previewing Changes

Use `preview` for a quick dry-run view:

```bash
fo preview ~/Downloads
```

### Audio transcription (optional)

When the `[media]` extra is installed, you can categorize audio files by
transcript content rather than only by metadata tags:

```bash
fo organize ~/Downloads ~/Organized --transcribe-audio
```

Transcription is **off by default** because it is CPU-intensive. The default
duration cap (10 minutes per file, override with `--max-transcribe-seconds`)
prevents podcast-length files from monopolizing the run; over-cap files fall
back to metadata-only categorization with a warning. Set
`--max-transcribe-seconds 0` to disable the cap entirely.

The first run downloads the Whisper "tiny" model (about 39 MB). Subsequent runs
use the local cache.

If `[media]` is not installed, `--transcribe-audio` degrades gracefully: the
organize batch completes using metadata-only categorization and prints a yellow
warning naming the missing extra.

### Searching Files

Search through organized files by filename pattern or keyword:

```bash
fo search "quarterly report" ~/Organized
```

### Analyzing Individual Files

Inspect what the AI detects about a specific file:

```bash
fo analyze ~/Documents/report.pdf
```

## Copilot

The Copilot is an AI assistant that can answer questions about your files and perform management tasks using natural language.

### Interactive Chat (REPL)

```bash
# Start an interactive session
fo copilot chat

# Specify a working directory
fo copilot chat --dir ~/Documents
```

### Single-Shot Mode

```bash
# Ask a single question
fo copilot chat "How many PDF files are in my Documents folder?"
```

## Daemon and Background Processing

The daemon watches directories for new files and organizes them automatically.

### Starting the Daemon

```bash
# Watch a directory
fo daemon start --watch-dir ~/Downloads --output-dir ~/Organized

# Run in foreground (useful for debugging)
fo daemon start --watch-dir ~/Downloads --output-dir ~/Organized --foreground

# Adjust poll interval (seconds)
fo daemon start --watch-dir ~/Downloads --output-dir ~/Organized --poll-interval 30

# Dry-run mode (log actions without moving files)
fo daemon start --watch-dir ~/Downloads --output-dir ~/Organized --dry-run
```

### Managing the Daemon

```bash
# Check daemon status
fo daemon status

# Stop the daemon
fo daemon stop
```

## Organization Methodologies

File Organizer supports multiple organization systems. Configure these through the `config edit` command.

### Default AI Organization

The default method uses AI to analyze file content and suggest categories based on the content itself, not just file extensions.

### PARA Method

Projects, Areas, Resources, Archive -- a productivity-focused system:

- **Projects**: Active work with deadlines
- **Areas**: Ongoing responsibilities
- **Resources**: Reference materials by topic
- **Archive**: Completed or inactive items

### Johnny Decimal

A numerical categorization system using `XX.YY` numbering:

- **Areas** (10-19, 20-29, ...): Broad categories
- **Categories** (X1, X2, ...): Specific sub-categories
- **IDs** (XX.01, XX.02, ...): Individual items

## Deduplication

Find and resolve duplicate files using perceptual hashing (for images) and content-based comparison (for documents).

### Scanning for Duplicates

```bash
fo dedupe scan ~/Documents
```

### Resolving Duplicates

```bash
fo dedupe resolve ~/Documents
```

### Generating Reports

```bash
fo dedupe report ~/Documents
```

!!! note
    Image deduplication requires the dedup-image optional pack: `pip install -e ".[dedup-image]"`

## Auto-Tagging

The auto-tagging system suggests and applies tags to files based on AI analysis of their content.

### Getting Tag Suggestions

```bash
# Suggest tags for files in a directory
fo autotag suggest ~/Documents
```

### Applying Tags

```bash
# Apply specific tags to a file
fo autotag apply ~/Documents/report.pdf finance quarterly
```

### Viewing Popular Tags

```bash
# List the most commonly used tags
fo autotag popular
```

### Batch Operations

```bash
# Tag files in batch
fo autotag batch ~/Documents

# View recently applied tags
fo autotag recent
```

## Organization Rules

Rules let you override AI decisions with explicit patterns. When a file matches a rule, it is organized according to that rule instead of the AI suggestion.

### Listing Rules

```bash
# List all rules
fo rules list

# List rule sets
fo rules sets
```

### Adding Rules

```bash
fo rules add my-rule --pattern "*.invoice.*" --action move --dest "Documents/Financial"
```

### Previewing and Managing Rules

```bash
# Preview what a rule would match in a directory
fo rules preview ~/Documents

# Remove a rule
fo rules remove my-rule

# Export rules to a YAML file
fo rules export --output rules-backup.yaml

# Import rules from a YAML file
fo rules import rules-backup.yaml
```

## Smart Suggestions

Get AI-powered suggestions for where to place files based on your existing directory structure and past organization patterns.

### Getting Suggestions

```bash
# Suggest placements for files
fo suggest files ~/Unsorted

# View detected patterns
fo suggest patterns ~/Unsorted
```

### Applying Suggestions

```bash
fo suggest apply ~/Unsorted
```

## Analytics

View storage analytics, file distribution, and organization metrics.

```bash
# Display analytics dashboard
fo analytics
```

## Profiles

Profiles allow you to save and switch between different configuration sets. This is useful for managing separate environments (e.g., work vs. personal).

### Managing Profiles

```bash
# List all profiles
fo profile list

# Show current active profile
fo profile current

# Create a new profile
fo profile create work

# Activate a profile
fo profile activate work

# Delete a profile
fo profile delete old-profile
```

### Exporting and Importing

```bash
# Export a profile (--output is required)
fo profile export work --output work-profile.json

# Import a profile from a JSON file
fo profile import work-profile.json

# Merge two or more profiles into a new one (--output is required)
fo profile merge work personal --output combined
```

## Undo and Redo

All file move operations are tracked and reversible.

```bash
# Undo the last operation
fo undo

# Redo the last undone operation
fo redo

# View operation history
fo history
```

## Configuration

### Viewing Configuration

```bash
# Show current configuration
fo config show

# Show configuration for a specific profile
fo config show --profile work

# List available configuration profiles
fo config list
```

### Editing Configuration

```bash
# Edit configuration interactively
fo config edit

# Edit specific settings directly
fo config edit --text-model "qwen2.5:3b-instruct-q4_K_M"
fo config edit --vision-model "qwen2.5vl:7b-q4_K_M"
fo config edit --temperature 0.7
fo config edit --device auto

# Edit a specific profile's configuration
fo config edit --profile work
```

## AI Model Management

### Listing Models

```bash
# List all available models
fo model list

# Filter by type
fo model list --type text
fo model list --type vision
```

### Pulling Models

```bash
# Pull a model by name
fo model pull qwen2.5:3b-instruct-q4_K_M
```

### Cache Management

```bash
# View model cache status
fo model cache
```

## Self-Update

### Checking for Updates

```bash
# Check if a newer version is available
fo update check

# Include pre-release versions
fo update check --pre
```

### Installing Updates

```bash
# Download and install the latest version
fo update install

# Dry run (download without installing)
fo update install --dry-run
```

## Supported File Types

| Category | Formats |
|----------|---------|
| Documents | `.txt`, `.md`, `.pdf`, `.docx`, `.doc`*, `.csv`, `.xlsx`, `.xls`*, `.pptx` |
| Images | `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.tif` |
| Video | `.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv` |
| Audio | `.mp3`, `.wav`, `.flac`, `.m4a`, `.ogg` |
| Archives | `.zip`, `.7z`, `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.rar` |
| Scientific | `.hdf5`, `.h5`, `.hdf`, `.nc`, `.nc4`, `.netcdf`, `.mat` |
| CAD | `.dxf`, `.dwg`, `.step`, `.stp`, `.iges`, `.igs` |

*Legacy formats (`.doc`, `.xls`) have limited support and may return `None` or require additional dependencies. See the [File Format Reference](reference/file-formats.md) for details.

!!! tip
    Some format categories require optional feature packs. See [Optional Feature Packs](#optional-feature-packs) above. For audio transcription and video analysis features, see the [Audio & Video Setup Guide](setup/audio-video.md).

## Privacy and Security

File Organizer is designed to keep your data completely private:

- All AI processing runs locally through Ollama -- no files or content are uploaded to any cloud service.
- Network requests are limited to:
  - Communicating with your local Ollama instance (localhost only)
  - Checking for application updates (optional, can be disabled)
- No telemetry, analytics, or usage tracking of any kind.

## Troubleshooting

### Ollama Not Running

If you see connection errors, ensure Ollama is running:

```bash
ollama ps
```

If no models are listed, pull the required models:

```bash
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M
```

### Verbose Output

Add `--verbose` (or `-v`) to any command for detailed logging:

```bash
fo organize ~/Downloads ~/Organized --verbose
```

### Checking Health

Verify Ollama and the application are working:

```bash
fo doctor .
```

For more detailed troubleshooting, see [Troubleshooting](troubleshooting.md).
