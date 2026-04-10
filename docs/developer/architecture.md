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

### Plugin System

Extend functionality via hooks:

```python
from file_organizer.plugins import register_hook

@register_hook("on_file_processed")
def my_plugin(file: ProcessedFile):
    # Custom logic
    pass
```

### Custom Models

Implement custom AI models:

```python
from file_organizer.models.base import BaseModel

class CustomModel(BaseModel):
    def generate(self, prompt: str) -> str:
        # Custom implementation
        pass
```

## See Also

- [Contributing Guide](contributing.md)
