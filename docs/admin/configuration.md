# Configuration Guide

## Environment Configuration

### AI Models Configuration

```bash
# Ollama Settings
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL_TEXT=qwen2.5:3b-instruct-q4_K_M
OLLAMA_MODEL_VISION=qwen2.5vl:7b-q4_K_M

# Model Parameters
MODEL_TEMPERATURE=0.5
MODEL_MAX_TOKENS=3000
MODEL_TIMEOUT=300
```

### Logging Configuration

```bash
# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

## Configuration Methods

### 1. Environment Variables

```bash
export OLLAMA_HOST="http://localhost:11434"
file-organizer organize ./Downloads ./Organized
```

### 2. CLI Config Command

```bash
file-organizer config edit
```

## Advanced Configuration

### Custom Methodologies

Configure PARA and Johnny Decimal:

```yaml
methodologies:
  para:
    projects_folder: Projects
    areas_folder: Areas
    resources_folder: Resources
    archives_folder: Archives
  johnny_decimal:
    enabled: true
    system_name: "File Organizer"
```

## See Also

- [Installation Guide](installation.md)
