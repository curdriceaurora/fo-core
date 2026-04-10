# Admin Troubleshooting Guide

## Common Issues

### Disk Space Issues

**Problem**: Disk full or running low

**Solution**:

```bash
# Check disk usage
df -h

# Check Ollama models size
du -sh ~/.ollama/
```

## Model Issues

### Ollama Model Not Available

**Problem**: "Model not found" error

**Solution**:

```bash
# Check available models
ollama ls

# Pull required models
ollama pull qwen2.5:3b-instruct-q4_K_M
ollama pull qwen2.5vl:7b-q4_K_M

# Verify Ollama is running
ollama ps
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

## Getting Help

### Collect Diagnostic Information

```bash
# System info
uname -a
python3 --version

# Check installation
file-organizer doctor .

# Ollama status
ollama ps
ollama ls
```

### Report an Issue

Include:

1. Error message and logs
1. Steps to reproduce
1. System information (OS, Python version)
1. Recent configuration changes
