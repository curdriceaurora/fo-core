# Getting Started with File Organizer

This guide will help you install and set up File Organizer quickly.

## Installation Methods

Choose the installation method that best fits your needs:

### Python Package

**Best for**: Quick setup, simple usage

**Prerequisites**:

- Python 3.11 or higher
- Ollama installed and running
- 4GB+ available disk space

**Install**:

```bash
pip install fo-core

# Verify installation
fo doctor .
```

See the sections below for platform-specific options.

### From Source

**Best for**: Development, customization

**Prerequisites**:

- Python 3.11 or higher
- Git
- Ollama installed

**Install**:

```bash
git clone https://github.com/curdriceaurora/fo-core.git
cd fo-core
pip install -e .

# Pull required AI models
ollama pull qwen2.5:3b-instruct-q4_K_M      # Text model
ollama pull qwen2.5vl:7b-q4_K_M             # Vision model

# Verify installation
fo doctor .
```

## System Requirements

### Minimum

- **CPU**: 2-core processor
- **RAM**: 8 GB
- **Storage**: 10 GB (for AI models)
- **Python**: 3.11+
- **Ollama**: Latest version

### Recommended

- **CPU**: 4+ cores
- **RAM**: 16 GB or more
- **Storage**: 20 GB SSD
- **GPU**: NVIDIA, AMD, or Apple Silicon (optional, for faster processing)

### Optional

- **FFmpeg**: For audio/video preprocessing

## Optional Features

File Organizer supports modular installation through optional dependency groups. Install only the features you need:

| Feature | Install Command | What It Enables | Platform Notes |
|---------|----------------|-----------------|----------------|
| **Core** | `pip install fo-core` | Basic file organization, Ollama integration, YAML/JSON/TXT parsing | All platforms |
| **cloud** | `pip install fo-core[cloud]` | OpenAI-compatible API providers (OpenAI, Groq, LM Studio, vLLM) | Requires `OPENAI_API_KEY` |
| **llama** | `pip install fo-core[llama]` | Direct GGUF inference via llama.cpp (no Ollama server needed) | All platforms |
| **mlx** | `pip install fo-core[mlx]` | Apple Silicon MLX acceleration for faster local inference | **macOS only** |
| **claude** | `pip install fo-core[claude]` | Anthropic Claude API provider (text and vision) | Requires `ANTHROPIC_API_KEY` |
| **audio** | `pip install fo-core[audio]` | Audio transcription (Faster Whisper), metadata extraction | GPU recommended |
| **video** | `pip install fo-core[video]` | Video frame processing, scene detection | All platforms |
| **dedup** | `pip install fo-core[dedup]` | Image and text similarity-based duplicate detection | All platforms |
| **archive** | `pip install fo-core[archive]` | 7Z and RAR archive extraction | RAR requires `unrar` tool |
| **scientific** | `pip install fo-core[scientific]` | HDF5, NetCDF, MATLAB file format support | All platforms |
| **cad** | `pip install fo-core[cad]` | DXF/DWG CAD file parsing | All platforms |
| **build** | `pip install fo-core[build]` | PyInstaller-based executable packaging | All platforms |
| **search** | `pip install fo-core[search]` | BM25-based search ranking algorithms | All platforms |
| **all** | `pip install fo-core[all]` | All optional packs above, plus development tools (`pytest`, `mypy`, `ruff`, etc.) | Includes `dev` extras in addition to feature/build packs |

**Example usage:**

```bash
# Install multiple features at once
pip install fo-core[cloud]

# Install from source with features
pip install -e .[audio]

# Install everything
pip install fo-core[all]
```

## First Run Setup

After installation, File Organizer will guide you through initial setup:

### 1. AI Model Configuration

File Organizer supports two provider modes:

**Option A — Ollama (default, fully local):**

- **Text Model**: `qwen2.5:3b-instruct-q4_K_M` (~1.9 GB)
- **Vision Model**: `qwen2.5vl:7b-q4_K_M` (~6.0 GB)

These are automatically pulled on first run if Ollama is available.

**Manual pull** (if needed):

```bash
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M
```

**Option B — OpenAI-compatible endpoint (cloud or local API server):**

No Ollama required. Install the `[cloud]` extra and set environment variables:

```bash
pip install "fo-core[cloud]"   # from PyPI
# pip install -e ".[cloud]"           # from source checkout

# Example: OpenAI
export FO_PROVIDER=openai
export FO_OPENAI_API_KEY=sk-...
export FO_OPENAI_MODEL=gpt-4o-mini

# Example: LM Studio (local, no key needed)
export FO_PROVIDER=openai
export FO_OPENAI_BASE_URL=http://localhost:1234/v1
export FO_OPENAI_MODEL=your-loaded-model
```

**Option C — Anthropic Claude:**

No Ollama required. Install the `[claude]` extra and set environment variables:

```bash
pip install "fo-core[claude]"  # from PyPI
# pip install -e ".[claude]"          # from source checkout

export FO_PROVIDER=claude
export FO_CLAUDE_API_KEY=sk-ant-...
export FO_CLAUDE_MODEL=claude-3-5-sonnet-20241022
```

Claude supports both text and vision tasks natively — no separate vision model configuration is required (though you can override with `FO_CLAUDE_VISION_MODEL`).

See [Configuration Guide](CONFIGURATION.md) for the full list of providers and options.

### 2. Workspace Configuration

Set up your workspace:

- **Workspace Path**: Where to store workspace data
- **Watch Directories**: Which folders to monitor (optional)
- **Organization Methodology**: Choose PARA, Johnny Decimal, or Custom

## Using the CLI

### Basic Commands

```bash
# Organize files
fo organize ./Downloads ./Organized

# Preview without moving (dry run)
fo organize ./Downloads ./Organized --dry-run

# Preview organisation plan
fo preview ./Downloads

# Search for files
fo search "*.pdf" ~/Documents
fo search "report" ~/Documents --type text

# Analyze a file with AI
fo analyze ./report.pdf
fo analyze ./report.pdf --verbose

# Auto-tag files
fo autotag suggest ./Documents
fo autotag popular

# Detect duplicates
fo dedupe scan ./Documents

# Analyse storage
fo analytics ./Documents

# View operation history
fo history

# Interactive AI assistant
fo copilot chat
```

### Short Alias

Use `fo` instead of `fo`:

```bash
fo organize ./Downloads ./Organized
fo preview ./Downloads
fo search "*.pdf" ~/Documents
fo analyze ./report.pdf
fo dedupe scan ./Documents
fo analytics ./Documents
```

See [CLI Reference](cli-reference.md) for all commands.

## Choosing an Organization Methodology

File Organizer supports multiple organization systems:

### PARA (Projects, Areas, Resources, Archives)

**Best for**: Knowledge workers, complex projects

**Structure**:

```text
PARA/
├── Projects/        # Active projects with deadlines
├── Areas/           # Ongoing responsibilities
├── Resources/       # Reference materials
└── Archives/        # Completed projects
```

**Learn more**: [PARA Guide](https://forte.com/reference/PARA)

### Johnny Decimal

**Best for**: Hierarchical organization, fixed categories

**Structure**:

```text
JD/
├── 10-19 Area 1/
│   ├── 11 Category A
│   ├── 12 Category B
├── 20-29 Area 2/
│   ├── 21 Category C
```

**Learn more**: [Johnny Decimal Guide](https://johnnydecimal.com)

### Custom Methodology

Create your own organization system using rules and templates.

**Learn more**: See the [Developer Guide](developer/index.md) for creating custom methodologies.

## Common First Tasks

### 1. Organize Files

```bash
fo organize ./Downloads ./Organized
fo organize ./Downloads ./Organized --dry-run  # Preview first
```

Supported formats: 43+ file types including documents, images, videos, and more.

### 2. Find Duplicates

```bash
fo dedupe scan ./Documents
```

### 3. Search Files

```bash
fo search "*.pdf" ~/Documents
fo search "report" ~/Documents --type text
```

### 4. Configure Settings

```bash
fo config edit
```

## Troubleshooting Installation

### Ollama Connection Failed

**Issue**: "Cannot connect to Ollama service"

**Solutions**:

```bash
# Start Ollama service
ollama serve

# Verify it's running
curl http://localhost:11434/api/version
```

### Models Not Found

**Issue**: "Model not found" error

**Solution**:

```bash
# Pull models manually
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M

# Verify models are installed
ollama list
```

### Out of Memory

**Issue**: "Out of memory" when processing files

**Solutions**:

- Increase available RAM
- Process smaller batches
- Reduce maximum file size
- Use CPU-only mode (slower but uses less RAM)

For more issues, see [Troubleshooting Guide](troubleshooting.md).

## Next Steps

- **Developers**: Read [Developer Guide](developer/index.md)

## Getting Help

- 📚 **Documentation**: [Full documentation](index.md)
- ❓ **FAQ**: [Frequently Asked Questions](faq.md)
- 🐛 **Issues**: [GitHub Issues](https://github.com/curdriceaurora/fo-core/issues)
- 💬 **Discussions**: [GitHub Discussions](https://github.com/curdriceaurora/fo-core/discussions)

## Audio Processing Prerequisites

Audio transcription and metadata extraction require additional system dependencies beyond the Python packages.

### FFmpeg

FFmpeg is required for audio format conversion (e.g. `.m4a` to `.wav`) and preprocessing before transcription.

Install FFmpeg for your platform:

- **macOS**: `brew install ffmpeg`
- **Ubuntu / Debian**: `sudo apt update && sudo apt install -y ffmpeg`
- **Windows**: `winget install ffmpeg` (or download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to your `PATH`)

FFmpeg is required for any audio format other than raw `.wav`. Without it, audio files in formats like `.mp3`, `.m4a`, `.flac`, and `.ogg` cannot be processed.

### GPU Acceleration (Optional)

Audio transcription uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) which benefits from GPU acceleration. CPU inference works but is significantly slower.

For NVIDIA GPUs, verify your setup:

```bash
nvidia-smi
nvcc --version
python3 -c "import torch; print('CUDA:', torch.cuda.is_available()); print('cuDNN:', torch.backends.cudnn.version())"
```

CPU-only inference works out of the box. Apple Silicon users get hardware acceleration via MPS automatically.

### Installing the Audio Pack

```bash
pip install -e ".[audio]"
```

This installs: `faster-whisper`, `torch`, `mutagen`, `tinytag`, `pydub`, `ffmpeg-python`.

The `torch` package is approximately 2 GB. For CPU-only environments, install the CPU-only variant first:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[audio]"
```

If the audio pack is not installed, audio files (`.mp3`, `.wav`, `.flac`, `.m4a`, `.ogg`) are still detected and moved by the organizer but will not be transcribed or analyzed for content.

### Verifying Audio Support

```bash
ffmpeg -version
python3 -c "from faster_whisper import WhisperModel; print('faster-whisper OK')"
python3 -c "import mutagen; print('mutagen OK')"
python3 -c "import torch; print('Device:', 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu')"
```

______________________________________________________________________

**Ready to start?** Run `fo --help` to see all available commands and begin organizing your files!
