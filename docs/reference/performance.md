# Performance Notes

| File Type | Average Time | Model |
|-----------|-------------|-------|
| Text (< 1 MB) | 2–5 s | Qwen 2.5 3B |
| Image | 3–8 s | Qwen 2.5-VL 7B |
| Video | 5–20 s | Qwen 2.5-VL 7B |
| Audio | 2–10 s | faster-whisper |
| PDF (text) | 3–10 s | Qwen 2.5 3B |

## Memory Usage

| Component | RAM |
|-----------|-----|
| Qwen 2.5 3B (Q4) | ~2.5 GB |
| Qwen 2.5-VL 7B (Q4) | ~5.5 GB |
| Base application | ~200 MB |

## Performance-Sensitive Components

| Component | Purpose | Source Module |
|-----------|---------|---------------|
| ModelConfig | Inference parameters | `src/models/base.py` |
| ParallelConfig | Worker pool and timeouts | `src/parallel/config.py` |
| AdaptiveBatchSizer | Memory-aware batch sizing | `src/optimization/batch_sizer.py` |
| ModelCache | LRU model caching with TTL | `src/optimization/model_cache.py` |
| ModelWarmup | Background model pre-loading | `src/optimization/warmup.py` |
| ResourceMonitor | Memory and GPU monitoring | `src/optimization/resource_monitor.py` |
| MemoryLimiter | Hard memory caps | `src/optimization/memory_limiter.py` |

## Model Configuration

`ModelConfig` controls inference behavior for all AI models (text, vision, audio).

| Parameter | Default | Description |
|-----------|---------|-------------|
| `name` | (required) | Model identifier |
| `model_type` | (required) | `TEXT`, `VISION`, `AUDIO`, or `VIDEO` |
| `quantization` | `q4_k_m` | Quantization level (lower = faster, less accurate) |
| `device` | `AUTO` | `AUTO`, `CPU`, `CUDA`, `MPS`, `METAL` |
| `temperature` | `0.5` | Sampling temperature |
| `max_tokens` | `3000` | Maximum tokens in generated response |
| `top_k` | `3` | Top-K sampling |
| `top_p` | `0.3` | Nucleus sampling threshold |
| `context_window` | `4096` | Maximum context length in tokens |
| `batch_size` | `1` | Batch size for inference |
| `framework` | `ollama` | `ollama`, `llama_cpp`, or `mlx` |

**Tuning tips**:

- Lower `max_tokens` to 200–500 for classification tasks (folder names, filenames)
- Reduce `temperature` to 0.1–0.3 for more consistent naming
- Use `q4_k_m` quantization for the best speed/quality tradeoff

## Parallel Processing

`ParallelConfig` controls how files are processed concurrently.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_workers` | `None` (CPU count) | Maximum worker threads or processes |
| `executor_type` | `THREAD` | `THREAD` (I/O-bound) or `PROCESS` (CPU-bound) |
| `prefetch_depth` | `2` | Queue-ahead depth per worker |
| `chunk_size` | `10` | Files submitted per scheduling round |
| `timeout_per_file` | `60.0` | Seconds before a file processing times out |
| `retry_count` | `2` | Retry attempts for failed files |

**Tuning tips**:

- For Ollama-based inference (I/O-bound), use `THREAD` executor
- For local model inference with GPU, `max_workers=1` prevents GPU contention
- Increase `timeout_per_file` for large PDFs or videos (120–300 s)

```python
from parallel.config import ParallelConfig, ExecutorType

config = ParallelConfig(
    max_workers=4,
    executor_type=ExecutorType.THREAD,
    prefetch_depth=2,
    chunk_size=20,
    timeout_per_file=120.0,
    retry_count=1,
)
```

## Pipeline Prefetch

`PipelineOrchestrator` supports double-buffered processing to overlap I/O and compute.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `prefetch_depth` | `2` | Files to pre-process ahead of current file. `0` disables prefetch. |
| `prefetch_stages` | `1` | Requested number of leading stages to prefetch. The current implementation only supports the first stage; values greater than `1` log a warning and are effectively treated as `1`. |
| `memory_limiter` | `None` | Optional `MemoryLimiter` to gate prefetch slots |

```python
from optimization.memory_limiter import MemoryLimiter, LimitAction
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.stages import PreprocessorStage, AnalyzerStage

limiter = MemoryLimiter(max_memory_mb=2048, action=LimitAction.WARN)
pipeline = PipelineOrchestrator(
    stages=[PreprocessorStage(), AnalyzerStage()],
    prefetch_depth=3,
    memory_limiter=limiter,
)
results = pipeline.process_batch(files)
```

**Tuning guidance:**

- Set `prefetch_depth=0` to disable overlap and process files sequentially (useful for debugging or low-memory environments).
- Keep `prefetch_stages=1`; higher values are currently capped to the first stage.
- `--no-prefetch` is the CLI flag that acts as alias for `--prefetch-depth 0` in the legacy `ParallelProcessor` path. For new code use `prefetch_depth=0` directly.

## Adaptive Batch Sizing

`AdaptiveBatchSizer` calculates how many files to process per batch based on available memory.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `target_memory_percent` | `70.0` | Target percentage of available memory to use |
| `min_batch_size` | `1` | Minimum files per batch |
| `max_batch_size` | `1000` | Maximum files per batch |

```python
from optimization.batch_sizer import AdaptiveBatchSizer

sizer = AdaptiveBatchSizer(target_memory_percent=60.0)
sizer.set_bounds(min_size=5, max_size=100)
batch_size = sizer.calculate_batch_size(file_sizes, overhead_per_file=1024)
```

## Model Cache

`ModelCache` keeps loaded models in memory using an LRU eviction policy with TTL expiration.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_models` | `3` | Maximum models kept in cache simultaneously |
| `ttl_seconds` | `300.0` | Time-to-live before a cached model expires |

```python
from optimization.model_cache import ModelCache

cache = ModelCache(max_models=3, ttl_seconds=600)
model = cache.get_or_load("qwen2.5:3b", loader_fn)
stats = cache.stats()
```

## Model Warmup

`ModelWarmup` pre-loads models in background threads to eliminate cold-start latency.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_workers` | `2` | Maximum parallel model loading threads |

```python
from optimization.warmup import ModelWarmup

warmup = ModelWarmup(cache, loader_factory, max_workers=2)
result = warmup.warmup(["qwen2.5:3b", "qwen2.5vl:7b"])
```

## Resource Monitor

`ResourceMonitor` provides real-time memory and GPU usage.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `threshold_percent` | `85.0` | Memory usage % that triggers eviction |

```python
from optimization.resource_monitor import ResourceMonitor

monitor = ResourceMonitor()
if monitor.should_evict(threshold_percent=80.0):
    cache.clear()
```

## Memory Limiter

`MemoryLimiter` enforces hard memory caps on the process.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_memory_mb` | (required) | Maximum allowed RSS in megabytes |
| `action` | `WARN` | `WARN`, `BLOCK`, `EVICT_CACHE`, or `RAISE` |

```python
from optimization.memory_limiter import MemoryLimiter, LimitAction

limiter = MemoryLimiter(max_memory_mb=4096, action=LimitAction.EVICT_CACHE)
limiter.set_evict_callback(cache.clear)

with limiter.guarded():
    process_batch()
```

## Hardware Recommendations

### Small Workloads (< 100 files)

- RAM: 8 GB minimum; CPU: any modern multi-core; GPU: optional
- Recommended: default settings

### Medium Workloads (100–1,000 files)

- RAM: 16 GB; CPU: 4+ cores; GPU: recommended
- `max_workers=4`, `chunk_size=20`, `target_memory_percent=60.0`, `max_models=2`, `ttl_seconds=600`

### Large Workloads (1,000+ files)

- RAM: 32 GB; CPU: 8+ cores; GPU: NVIDIA 8+ GB VRAM or Apple Silicon
- `max_workers=8`, `chunk_size=50`, `target_memory_percent=50.0`, `max_memory_mb=8192`, `action=EVICT_CACHE`, `max_models=3`, `ttl_seconds=1800`, `timeout_per_file=180.0`

## Benchmarking

```bash
fo benchmark run ~/test-files --suite io --json
fo benchmark run ~/test-files --suite text --json
fo benchmark run ~/test-files --suite vision --json
fo benchmark run ~/test-files --suite pipeline --json
fo benchmark run ~/test-files --suite e2e --json
```
