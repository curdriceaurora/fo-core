# Troubleshooting Guide

Common issues and solutions for File Organizer.

## Installation Issues

### Ollama Connection Failed

**Error**: `ConnectionRefusedError` or "Ollama unavailable"

**Cause**: Ollama service is not running or is bound to a different port.

**Solution**:

```bash
# Start Ollama
ollama serve

# Verify it's running
curl http://localhost:11434/api/version

# If using a custom port, set the environment variable
export OLLAMA_HOST=http://localhost:12345
```

### Model Not Found

**Error**: "Model not found"

**Cause**: Required Ollama models have not been downloaded.

**Solution**:

```bash
# Pull the required models
ollama pull qwen2.5:3b-instruct-q4_K_M      # Text model (~1.9 GB)
ollama pull qwen2.5vl:7b-q4_K_M             # Vision model (~6.0 GB)

# Verify they're installed
ollama list
```

## Optional Dependency Issues

### Module Not Found Error

**Error**: `ModuleNotFoundError: No module named 'faster_whisper'` or similar

**Cause**: Attempting to use a feature that requires optional dependencies not installed with the base package.

**Solution**:

Install the appropriate optional dependency group based on the feature you're using:

| Feature | Error Pattern | Install Command |
|---------|---------------|-----------------|
| Audio/video | `faster_whisper`, `cv2`, `torch` | `pip install "fo-core[media]"` |
| Image deduplication | `imagededup` | `pip install "fo-core[dedup-image]"` |
| Text deduplication | `sklearn` | `pip install "fo-core[dedup-text]"` |
| Semantic search | `rank_bm25`, `sklearn` | `pip install "fo-core[search]"` |
| Scientific formats | `h5py`, `netCDF4` | `pip install "fo-core[scientific]"` |
| CAD file support | `ezdxf` | `pip install "fo-core[cad]"` |
| Claude API provider | `anthropic` | `pip install "fo-core[claude]"` |
| OpenAI-compatible API | `openai` | `pip install "fo-core[cloud]"` |
| llama.cpp inference | `llama_cpp` | `pip install "fo-core[llama]"` |
| MLX inference (macOS) | `mlx_lm` | `pip install "fo-core[mlx]"` |
| All features | Any of the above | `pip install "fo-core[all]"` |

For more details, see [Dependencies & Setup](setup/dependencies.md).

### Import Error with Specific Message

**Error**: `ImportError: faster-whisper is required for audio transcription. Install it with: pip install faster-whisper`

**Cause**: The error message indicates exactly which package is missing.

**Solution**:

Follow the instruction in the error message, or use the table above to install the complete feature group.

## Permission Errors

### File Access Denied (macOS)

**Error**: `PermissionError: [Errno 13] Permission denied: '/Users/username/Desktop'`

**Cause**: macOS protects certain directories (Desktop, Documents, Downloads) and requires explicit permission for applications to access them.

**Solution**:

```bash
# Option 1: Grant Full Disk Access
# System Settings > Privacy & Security > Full Disk Access
# Add your terminal application or Python

# Option 2: Use a different directory
mkdir ~/fo-workspace
fo organize ~/fo-workspace ~/organized

# Option 3: Copy files to an accessible location first
cp -r ~/Desktop/files ~/fo-workspace/
```

### Cannot Read File Error

**Error**: `PermissionError: Cannot read file: /path/to/file`

**Cause**: Insufficient permissions to read the file, typically from file ownership or mode restrictions.

**Solution**:

```bash
# Check file permissions
ls -la /path/to/file

# Make file readable
chmod +r /path/to/file

# If owned by another user, change ownership (requires sudo)
sudo chown $USER /path/to/file
```

## Memory and Performance Issues

### Out of Memory During Organization

**Error**: Process killed or `MemoryError` when organizing large directories

**Cause**: Processing too many files simultaneously or analyzing very large files (videos, high-res images).

**Solution**:

```bash
# Process sequentially instead of in parallel
fo organize /path/to/input /path/to/output --sequential

# Limit number of parallel workers
fo organize /path/to/input /path/to/output --max-workers 2

# Process subdirectories separately
for dir in /path/*/; do
  fo organize "$dir" /output
done

# Skip vision processing for large directories
fo organize /path/to/input /path/to/output --no-vision
```

For production deployments with high memory demands, see [Performance Tuning](reference/performance.md).

### Audio Transcription Out of Memory

**Error**: `RuntimeError: CUDA out of memory` or system OOM killer

**Cause**: Whisper model too large for available GPU memory, or processing very long audio files.

**Solution**:

Audio transcription uses `faster-whisper` (not Ollama). Model size and device are configured via the application config. To reduce memory usage:

```bash
# Process files sequentially to limit concurrent memory use
fo organize /audio /output --sequential

# Skip vision processing to free up resources
fo organize /audio /output --text-only
```

For GPU memory issues with Ollama models, reduce the model size or restrict GPU access via environment variables like `CUDA_VISIBLE_DEVICES=""` (NVIDIA) or `HIP_VISIBLE_DEVICES=""` (AMD) to force CPU-only inference.

Available Whisper model sizes (smallest to largest):

- `tiny` - ~1 GB VRAM, fastest
- `base` - ~1 GB VRAM, good balance (default)
- `small` - ~2 GB VRAM, better accuracy
- `medium` - ~5 GB VRAM, high accuracy
- `large-v3` - ~10 GB VRAM, best accuracy

## Configuration Issues

### YAML Parse Error

**Error**: `yaml.scanner.ScannerError: mapping values are not allowed here`

**Cause**: Invalid YAML syntax in configuration file.

**Solution**:

```bash
# Validate YAML syntax online or with a linter
python -c "import yaml; yaml.safe_load(open('config.yaml'))"

# Common issues:
# - Tabs instead of spaces (use spaces only)
# - Missing quotes around strings with special characters
# - Incorrect indentation

# View current config to check for errors
fo config show
```

### Config File Not Found

**Error**: `FileNotFoundError` for configuration file

**Cause**: Configuration file does not exist in the expected location. The config path is determined by `platformdirs` and varies by OS:

- **Linux**: `~/.config/fo/`
- **macOS**: `~/Library/Application Support/fo/`
- **Windows**: `%APPDATA%\fo\`

**Solution**:

```bash
# View the current config path and values
fo config show

# Open the config file in your editor to create/edit it
fo config edit

# List all available config keys
fo config list
```

### XDG Config Migration

**Error**: Warning about deprecated config location

**Cause**: Old config files in a legacy location instead of the platform-appropriate directory managed by `platformdirs`.

**Solution**:

```bash
# Check where config is currently stored
fo config show

# Edit config in the correct location
fo config edit

# Or set XDG_CONFIG_HOME explicitly (Linux only)
export XDG_CONFIG_HOME=~/.config
```

See [Path Standardization](config/path-standardization.md) for details on config migration.

## Audio Transcription Issues

### No GPU Available Warning

**Error**: `UserWarning: No GPU detected, falling back to CPU`

**Cause**: PyTorch cannot detect CUDA or MPS (Apple Silicon) acceleration.

**Solution**:

```bash
# Install PyTorch with CUDA support (NVIDIA GPUs)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# For Apple Silicon (MPS)
pip install torch torchvision torchaudio

# Verify GPU detection
python -c "
import torch
print(f'CUDA: {torch.cuda.is_available()}')
print(f'MPS: {torch.backends.mps.is_available() if hasattr(torch.backends, \"mps\") else False}')
"

# CPU mode works but is slower - no special flags needed,
# the application auto-detects available hardware
```

### Model Download Timeout

**Error**: `TimeoutError` or `ConnectionError` when downloading Whisper model

**Cause**: Network issues or slow connection when downloading large model files.

**Solution**:

```bash
# Increase timeout and retry
export HF_HUB_DOWNLOAD_TIMEOUT=600

# Pre-download models manually
python -c "from faster_whisper import WhisperModel; WhisperModel('base')"

# Check disk space (models require several GB)
df -h
```

### Unsupported Audio Format

**Error**: `ValueError: Unsupported audio format` or FFmpeg error

**Cause**: Audio file format not supported by FFmpeg or corrupted file.

**Solution**:

```bash
# Install/update FFmpeg
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Convert unsupported format to WAV
ffmpeg -i input.unknown output.wav

# Supported formats: WAV, MP3, FLAC, OGG, M4A, AAC, WMA
# Check file integrity
ffmpeg -v error -i audio.mp3 -f null - 2>error.log
cat error.log
```

## File Organization Errors

### Duplicate File Handling

**Error**: `FileExistsError: Destination file already exists` or "Duplicate file detected"

**Cause**: A file with the same name already exists in the destination directory.

**Solution**:

Use the built-in deduplication tools to identify and manage duplicates:

```bash
# Scan for duplicates
fo dedupe scan /path/to/files

# View deduplication report
fo dedupe report

# Resolve duplicates interactively
fo dedupe resolve

# Preview organization without moving files
fo organize /input /output --dry-run
```

### Filename Too Long Error

**Error**: `OSError: [Errno 63] File name too long` or `OSError: [Errno 36] File name too long` (ENAMETOOLONG)

**Cause**: Generated filename exceeds filesystem limits (typically 255 characters on most systems).

**Solution**:

The application handles filename length internally. If you encounter this error:

```bash
# Preview what filenames would be generated
fo organize /path/to/input /path/to/output --dry-run

# Manually rename problematic source files before organizing
for f in *; do
  if [ ${#f} -gt 200 ]; then
    mv "$f" "${f:0:200}.${f##*.}"
  fi
done
```

### Invalid Filename Characters

**Error**: `OSError: Invalid argument` or files with strange characters in names

**Cause**: Filename contains characters not allowed by the filesystem (e.g., `:`, `<`, `>`, `|`, `*`, `?` on Windows).

**Solution**:

The application sanitizes filenames automatically during organization. To preview the results:

```bash
# Preview organization to see how filenames will be handled
fo organize /path/to/input /path/to/output --dry-run
```

If source files have problematic names, rename them before organizing:

```bash
# Use detox to batch-clean filenames
# macOS
brew install detox

# Ubuntu/Debian
sudo apt-get install detox

detox -r /path/to/files
```

## Metadata Extraction Errors

### EXIF Data Extraction Failed

**Error**: `ValueError: Invalid EXIF data` or "Cannot read image metadata"

**Cause**: Image file has corrupted or non-standard EXIF metadata, or file is not actually an image.

**Solution**:

```bash
# Repair EXIF data with exiftool
# macOS
brew install exiftool

# Ubuntu/Debian
sudo apt-get install libimage-exiftool-perl

# Fix corrupted EXIF
exiftool -all= -tagsfromfile @ -all:all -unsafe -icc_profile image.jpg

# Verify file type
file image.jpg  # Should show "JPEG image data"

# Analyze a specific file for details
fo analyze image.jpg --verbose
```

### PDF Metadata Extraction Timeout

**Error**: `TimeoutError: PDF processing timed out` or process hangs on certain PDFs

**Cause**: PDF file is very large, corrupted, or contains complex embedded content that takes too long to process.

**Solution**:

```bash
# Analyze the problematic PDF to see what's happening
fo analyze problematic.pdf --verbose

# Repair corrupt PDF with Ghostscript
gs -o repaired.pdf -sDEVICE=pdfwrite -dPDFSETTINGS=/prepress input.pdf

# PDF parsing is included in the base install (PyMuPDF/fitz)
```

### Video Metadata Extraction Error

**Error**: `RuntimeError: ffprobe failed` or "Cannot extract video metadata"

**Cause**: FFmpeg/ffprobe is not installed or the video file is corrupted.

**Solution**:

```bash
# Install FFmpeg
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Verify installation
ffprobe -version

# Check video file integrity
ffmpeg -v error -i video.mp4 -f null - 2>error.log
cat error.log

# Analyze the video file
fo analyze video.mp4 --verbose
```

## Plugin/Extension Errors

### Plugin Load Failed

**Error**: `ImportError: Cannot load plugin` or "Plugin not found"

**Cause**: Plugin is not installed, has incompatible version, or has missing dependencies.

**Solution**:

```bash
# List available plugins in the marketplace
fo marketplace list

# Search for a specific plugin
fo marketplace search <keyword>

# Install a plugin
fo marketplace install <plugin-name>

# Check plugin details
fo marketplace info <plugin-name>

# List installed plugins
fo marketplace installed
```

### Plugin Configuration Error

**Error**: `ValueError: Invalid plugin configuration` or plugin crashes during execution

**Cause**: Plugin configuration file has invalid values or required settings are missing.

**Solution**:

```bash
# Check plugin details for configuration requirements
fo marketplace info <plugin-name>

# Check for available updates
fo marketplace updates

# Reinstall the plugin
fo marketplace uninstall <plugin-name>
fo marketplace install <plugin-name>
```

## Archive Processing Errors

### Cannot Extract Archive

**Error**: `RuntimeError: Archive extraction failed` or "Unsupported archive format"

**Cause**: Archive is corrupted, password-protected, or format is not supported.

**Solution**:

```bash
# Install archive support
pip install "fo-core"  # py7zr and rarfile are included in the default install

# Supported formats: ZIP, TAR, GZ, BZ2, XZ, 7Z, RAR (read-only)

# Test archive integrity before processing
7z t archive.7z
unzip -t archive.zip
tar -tzf archive.tar.gz
```

### Archive Bomb Detection

**Error**: `SecurityError: Archive bomb detected` or "Archive extraction aborted"

**Cause**: Archive contains excessive compression ratio (potential zip bomb) as a security measure.

**Solution**:

```bash
# Manually inspect suspicious archive contents without extracting
7z l -slt archive.zip  # List contents without extracting

# Extract to a sandboxed location to inspect
mkdir /tmp/archive-inspect
cd /tmp/archive-inspect
unzip -l suspicious.zip  # List only, don't extract
```

Archive bomb detection is a built-in safety feature. If you trust the archive source, extract it manually before organizing its contents.

## Video Processing Errors

### Video Scene Detection Failed

**Error**: `ModuleNotFoundError: No module named 'scenedetect'` or "Scene detection error"

**Cause**: Video processing dependencies not installed or video format not supported.

**Solution**:

```bash
# Install video dependencies
pip install "fo-core[media]"

# This includes: opencv-python, scenedetect, faster-whisper, and related libraries

# Verify installation
python -c "import cv2; from scenedetect import detect, ContentDetector; print('OK')"
```

### Video Thumbnail Generation Failed

**Error**: `RuntimeError: Cannot generate thumbnail` or FFmpeg error during thumbnail extraction

**Cause**: Video codec not supported, video is corrupted, or FFmpeg cannot seek to the specified position.

**Solution**:

```bash
# Install FFmpeg with full codec support
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Generate a thumbnail manually with FFmpeg
ffmpeg -i video.mp4 -ss 00:00:05 -vframes 1 thumbnail.jpg

# Analyze the video file for details
fo analyze video.mp4 --verbose
```

### Video Processing Timeout

**Error**: `TimeoutError: Video processing exceeded time limit`

**Cause**: Video file is very large or high resolution, causing processing to take too long.

**Solution**:

```bash
# Process videos sequentially to avoid resource contention
fo organize /videos /output --sequential

# Skip vision processing for video-heavy directories
fo organize /videos /output --text-only

# Analyze individual files to identify problematic ones
fo analyze large-video.mp4 --verbose
```

## Image Processing Errors

### Image Deduplication Error

**Error**: `ModuleNotFoundError: No module named 'imagededup'` or "Deduplication failed"

**Cause**: Image deduplication dependencies not installed.

**Solution**:

```bash
# Install deduplication dependencies
pip install "fo-core[dedup-image]"  # for image deduplication
# pip install "fo-core[dedup-text]"  # for text-similarity deduplication

# Scan for duplicates
fo dedupe scan /path/to/images

# View the deduplication report
fo dedupe report

# Resolve duplicates interactively
fo dedupe resolve
```

### Image Format Conversion Failed

**Error**: `ValueError: Cannot convert image format` or PIL/Pillow error

**Cause**: Source image format is not supported by Pillow, or image is corrupted.

**Solution**:

```bash
# Update Pillow to latest version
pip install --upgrade Pillow

# Install additional image format support
pip install pillow-heif  # For HEIC/HEIF support

# Check supported formats
python -c "from PIL import Image; print(Image.registered_extensions())"

# Convert using external tool for unsupported formats
# Install ImageMagick
brew install imagemagick  # macOS
sudo apt-get install imagemagick  # Ubuntu/Debian

# Convert manually
convert input.rare output.jpg
```

### Image Resize/Optimization Failed

**Error**: `OSError: cannot write mode P as JPEG` or "Image optimization failed"

**Cause**: Image has transparency or palette mode that's incompatible with target format.

**Solution**:

```bash
# Manually convert problematic images before organizing
python -c "
from PIL import Image
img = Image.open('input.png').convert('RGB')
img.save('output.jpg')
"

# Analyze the image to understand the issue
fo analyze input.png --verbose
```

## Search Issues

### Search Returns No Results

**Error**: No results returned when searching, or "Search index not built"

**Cause**: Search dependencies not installed or search has not been run against the target directory.

**Solution**:

```bash
# Install search dependencies
pip install "fo-core[search]"

# Run a search query
fo search "query terms" --type documents

# Use semantic search mode
fo search "query terms" --semantic

# Limit results
fo search "query terms" --limit 20

# Output as JSON for programmatic use
fo search "query terms" --json
```

### Search Index Build Failed

**Error**: `ValueError` during index building or "Corpus too small"

**Cause**: Not enough documents to build vector index, or documents are empty/too short.

**Solution**:

```bash
# Check if files have extractable text
fo analyze /path/to/files --verbose

# Ensure files contain actual text content
# Vector search requires at least a few meaningful documents
# Try with more files or use keyword-based search
fo search "query" --type all
```

## Getting Help

If you can't find a solution here:

1. **Check documentation**:
   - [Getting Started Guide](getting-started.md)
   - [Performance Tuning](reference/performance.md) - Memory and optimization
   - [FAQ](faq.md) - Frequently Asked Questions

2. **Review logs**:

   ```bash
   # Enable verbose logging
   fo organize /input /output --verbose

   # Check system logs
   journalctl -u fo
   ```

3. **Community Support**:
   - [GitHub Issues](https://github.com/curdriceaurora/fo-core/issues) - Report bugs
   - [GitHub Discussions](https://github.com/curdriceaurora/fo-core/discussions) - Ask questions
   - Include: OS, Python version, error message, and steps to reproduce

4. **Diagnostic Information**:

   ```bash
   # System information
   fo version
   python --version
   ollama --version

   # Hardware details
   fo hardware-info
   ```

## Admin

### Disk Space Issues

**Problem**: Disk full or running low

**Solution**:

```bash
# Check disk usage
df -h

# Check Ollama models size
du -sh ~/.ollama/
```

### Model Inference Timeout

**Problem**: Model requests timing out

**Solution**:

```bash
# Check Ollama memory usage
ollama ps

# Restart Ollama if unresponsive
ollama serve
```
