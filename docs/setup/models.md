# AI Model Configuration

## Supported Models

- `gemma3:4b` — Default model for both text and image processing (~3.3 GB; verify with `ollama show gemma3:4b`)
- `gemma3:12b` — Recommended for systems with 16 GB RAM or more (~8.1 GB; verify with `ollama show gemma3:12b`)
- `faster-whisper` — Audio transcription (local, multi-language)

## Device Support

```python
from models.base import DeviceType

DeviceType.AUTO    # Automatic detection (recommended)
DeviceType.CPU     # CPU inference (universal)
DeviceType.CUDA    # NVIDIA GPU (fastest)
DeviceType.MPS     # Apple Silicon (fast)
DeviceType.METAL   # Apple Silicon (MLX)
```

---
