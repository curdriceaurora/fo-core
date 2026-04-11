# Architecture Guide

## System Design Overview

### High-Level Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                    CLI Interface (Typer)                 │
├─────────────────────────────────────────────────────────┤
│  File Organizer Core Engine (Python)                    │
│  ├── Text Processor                                     │
│  ├── Vision Processor                                   │
│  ├── Pattern Analyzer                                   │
│  ├── Intelligence Services                              │
│  └── Deduplication Services                             │
├─────────────────────────────────────────────────────────┤
│  External Services                                      │
│  ├── Ollama (Local LLMs)                                │
│  └── File System (Storage)                              │
└─────────────────────────────────────────────────────────┘
```

## CLI Layer

### Typer CLI Framework

- **Purpose**: Command-line interface for all user interactions
- **Location**: `file_organizer/cli/`
- **Key Features**:
  - Rich terminal output via Rich
  - Subcommand groups (organize, search, dedupe, copilot, etc.)
  - Shell completion support
  - Short alias `fo` for `file-organizer`

### Core File Organizer Engine

- **Purpose**: File organization logic
- **Location**: `file_organizer/core/`
- **Components**:
  - `organizer.py` - Main orchestrator
  - Model abstractions (text, vision, audio)
  - Service layer (processors, analyzers)

## Key Components

### AI Models

#### TextModel

- **Model**: Qwen 2.5 3B (via Ollama)
- **Purpose**: Text understanding and generation
- **Input**: File content/metadata
- **Output**: Categories, tags, descriptions

#### VisionModel

- **Model**: Qwen 2.5-VL 7B (via Ollama)
- **Purpose**: Image/video analysis
- **Input**: Images, video frames
- **Output**: OCR text, descriptions, classification

### Services

#### TextProcessor

- Reads text files
- Extracts metadata
- Generates organization suggestions

#### VisionProcessor

- Analyzes images and videos
- Performs OCR
- Detects content type

#### PatternAnalyzer

- Analyzes file naming patterns
- Learns user preferences
- Generates smart suggestions

#### Intelligence Services

- Tracks user preferences
- Learns organization patterns
- Provides personalized recommendations

### Deduplication

- Image hashing (perceptual)
- Document similarity (embeddings)
- Metadata comparison

## Data Flow

### File Organization Workflow

```markdown
1. User runs CLI command (e.g., fo organize)
   |
2. Files scanned in input directory
   |
3. Content analysis:
   - Text extraction
   - Vision analysis
   - Metadata extraction
   |
4. Pattern matching:
   - Analyze file properties
   - Check user preferences
   - Generate suggestions
   |
5. Organization preview:
   - Proposed folder
   - Suggested filename
   - Confidence score
   |
6. User confirmation (or --dry-run preview)
   |
7. File move/copy to destination
   |
8. Update metadata and indices
```

### CLI Command Flow

```text
CLI Command (Typer)
   |
Argument Parsing & Validation
   |
Business Logic (Service Layer)
   |
Core Engine (File Organizer)
   |
File System Operations
   |
Rich Terminal Output
```

## Development Patterns

### Service Layer Pattern

```python
# CLI command (endpoint)
@app.command()
def organize(input_dir: Path, output_dir: Path) -> None:
    service = OrganizationService()
    result = service.organize(input_dir, output_dir)
    display_results(result)

# Service (business logic)
class OrganizationService:
    def __init__(self):
        self.core = FileOrganizer()

    def organize(self, input_dir, output_dir):
        # Business logic
        return result

# Core (file organizer logic)
class FileOrganizer:
    def organize(self, files):
        # Core organization logic
        return result
```

## Extension Points

### Custom Models

Implement custom AI models:

```python
from file_organizer.models.base import BaseModel

class CustomModel(BaseModel):
    def generate(self, prompt: str) -> str:
        # Custom implementation
        pass
```

## Design Principles

1. **Privacy-First**: 100% local processing, zero cloud dependencies
2. **Model Abstraction**: Abstract AI model interface for framework flexibility
3. **Service Layer Pattern**: Business logic separate from models
4. **Strategy Pattern**: Different processors for different file types
5. **Event-Driven**: Event bus for loosely-coupled inter-component communication
6. **Type Safety**: Full type hints with strict mypy configuration
7. **Resource Management**: Context managers for automatic cleanup

## Component Registry

| Component | Purpose | Location | Status |
|-----------|---------|----------|--------|
| **BaseModel** | Abstract AI model interface | `models/base.py` | Active |
| **ModelManager** | Model lifecycle + hot-swap | `models/model_manager.py` | Active |
| **TextModel** | Ollama text generation | `models/text_model.py` | Active |
| **VisionModel** | Vision-language wrapper | `models/vision_model.py` | Active |
| **AudioModel** | Audio transcription | `models/audio_model.py` | Active |
| **PipelineStage** | Composable processing protocol | `interfaces/pipeline.py` | Active |
| **Pipeline Stages** | Preprocessor/Analyzer/Postprocessor/Writer | `pipeline/stages/` | Active |
| **TextProcessor** | Text file pipeline | `services/text_processor.py` | Active |
| **VisionProcessor** | Image/video pipeline | `services/vision_processor.py` | Active |
| **FileOrganizer** | Main orchestrator | `src/file_organizer/core/organizer.py` | Active |
| **PatternAnalyzer** | Naming pattern detection | `services/pattern_analyzer.py` | Active |
| **SuggestionEngine** | Placement suggestions | `services/smart_suggestions.py` | Active |
| **Intelligence** | User preference learning | `services/intelligence/` | Active |
| **Deduplication** | Duplicate detection | `services/deduplication/` | Active |
| **OperationHistory** | Operation tracking | `history/` | Active |
| **UndoManager** | Undo/redo system | `undo/` | Active |
| **EventBus** | Inter-component events | `events/` | Active |
| **Daemon** | Background file watching | `daemon/` | Active |
| **Methodologies** | PARA, Johnny Decimal | `methodologies/` | Active |

## Pipeline Data Flow

```text
Input File → FileOrganizer → ParallelProcessor (current default path)

Stage-Based Pipeline via PipelineOrchestrator (composable, double-buffered):
  PreprocessorStage → File validation + metadata extraction  (prefetched in I/O thread pool; default prefetch_stages=1)
  AnalyzerStage → FileRouter → Processor (Text/Vision/Audio) (runs on calling thread by default)
  PostprocessorStage → Destination path computation          (runs on calling thread by default)
  WriterStage → File copy/move (skipped in dry-run)         (runs on calling thread by default)

Legacy Pipeline (backward compatible):
  File Type Detection → TextProcessor / VisionProcessor / AudioModel
  → PatternAnalyzer → SuggestionEngine

All Paths → Intelligence Services → User Preference Learning
EventBus → Daemon notifications

Final Output: Organized files + Operation history
```

## Project Structure

```text
fo-core/
├── .claude/                          # CCPM project management
│   ├── commands/                     # PM commands
│   ├── prds/                         # Product requirements
│   ├── epics/                        # Epic planning workspace
│   ├── rules/                        # Standard operation rules
│   └── scripts/                      # Validation scripts
│
├── src/file_organizer/               # Main application (~78,800 LOC, 314 modules)
│   ├── models/                       # AI model abstractions (9 modules)
│   │   ├── base.py                   # BaseModel interface, ModelConfig
│   │   ├── text_model.py             # Ollama text generation
│   │   ├── vision_model.py           # Vision-language models
│   │   ├── audio_model.py            # Audio transcription
│   │   ├── audio_transcriber.py      # Comprehensive audio transcription
│   │   ├── model_manager.py          # Unified model lifecycle management
│   │   ├── registry.py               # Model registry
│   │   ├── suggestion_types.py       # Type definitions
│   │   └── analytics.py              # Model analytics
│   │
│   ├── services/                     # Business logic layer
│   │   ├── analytics/                # Storage & metrics analysis
│   │   ├── audio/                    # Audio file processing
│   │   ├── auto_tagging/             # Tag recommendation & learning
│   │   ├── copilot/                  # AI copilot features
│   │   ├── deduplication/            # Image & document deduplication
│   │   ├── intelligence/             # User preference learning (23 modules)
│   │   ├── video/                    # Video processing
│   │   ├── text_processor.py         # Text file pipeline
│   │   ├── vision_processor.py       # Image/video pipeline
│   │   ├── pattern_analyzer.py       # Pattern detection
│   │   ├── smart_suggestions.py      # Placement suggestions
│   │   ├── misplacement_detector.py  # Context analysis
│   │   └── suggestion_feedback.py    # Feedback tracking
│   │
│   ├── core/                         # Main orchestrator
│   │   ├── organizer.py              # FileOrganizer thin facade (~390 lines)
│   │   ├── types.py                  # Core type definitions
│   │   ├── display.py                # Output/display helpers
│   │   ├── file_ops.py               # File operation primitives
│   │   ├── dispatcher.py             # Request dispatching
│   │   ├── initializer.py            # Service initialization
│   │   └── hardware_profile.py       # Hardware capability detection
│   │
│   ├── cli/                          # Command-line interfaces (18 modules)
│   ├── daemon/                       # Background daemon & file watcher
│   ├── events/                       # Event bus system
│   ├── parallel/                     # Parallel processing framework
│   ├── pipeline/                     # Processing pipeline orchestration
│   ├── methodologies/                # PARA, Johnny Decimal, etc.
│   ├── plugins/                      # Plugin system & marketplace
│   ├── interfaces/                   # Protocol definitions
│   ├── optimization/                 # Performance optimization
│   ├── history/                      # Operation history (6 modules)
│   ├── undo/                         # Undo/redo system (5 modules)
│   ├── utils/                        # Utilities (file_readers.py, text_processing.py)
│   └── config/                       # Configuration management
│
├── tests/                            # 237 test files
│   ├── ci/                           # CI pipeline tests
│   ├── core/                         # Core tests
│   ├── integration/                  # Integration tests
│   ├── interfaces/                   # Protocol conformance tests
│   ├── models/                       # Model tests
│   ├── optimization/                 # Optimization tests
│   ├── parallel/                     # Parallel processing tests
│   ├── pipeline/                     # Pipeline tests
│   └── services/                     # Service layer tests
│
├── scripts/                          # Build & utility scripts
├── .github/                          # GitHub Actions workflows & templates
├── docs/                             # Project documentation
├── examples/                         # Usage examples
└── pyproject.toml                    # Project configuration
```

## Deferred Features

### GLM-OCR Integration

**Issue**: [#853](https://github.com/rahulvijayy/local-file-organizer/issues/853)
**Evaluated**: 2026-03-26
**Decision**: DEFER — architectural mismatch

[GLM-OCR](https://huggingface.co/THUDM/glm-ocr) is a 0.9B parameter multimodal model ranked #1 on OmniDocBench V1.5 for OCR tasks. The proposal aimed to add it as an optional OCR provider for scanned/image-based PDF processing.

**Blocking constraint**: GLM-OCR requires a persistent HTTP sidecar daemon (vLLM, SGLang, or MLX server). The current provider abstraction is designed for in-process execution only. Additionally, GLM-OCR requires `transformers>=5.3.0` while vLLM requires `transformers<5` — these conflict.

**Revisit when**: (1) a server-process provider type is added with sidecar lifecycle management, or (2) an in-process backend becomes available.

**Alternatives**: Tesseract OCR, EasyOCR, PaddleOCR, cloud OCR APIs.

## See Also

- [Contributing Guide](contributing.md)
