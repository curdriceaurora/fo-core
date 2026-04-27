# Organize Audio Transcription (Step 2B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `fo organize` use audio transcription for content-aware categorization when the `[media]` extra is installed, so dropping `~/podcast-archive` into `fo organize` produces transcript-tagged folders rather than just metadata-tagged folders.

**Depends on:** [Step 2A](2026-04-27-audio-model-wiring-2a.md) must merge first — this plan calls `AudioModel.generate()`.

**Architecture:** `FileOrganizer._process_audio_files` (`src/core/organizer.py:528-530`) currently only extracts metadata. We add an opt-in transcription pass: when `[media]` is installed and the new `--transcribe-audio` flag is set, transcribe each audio file (with a configurable max duration cap), pass the transcript to the existing text-categorization path so audio gets organized like text content. When `[media]` is not installed, transcription is silently skipped and the existing metadata-only behavior is preserved (graceful degrade — beta testers without `[media]` still get a working `fo organize`).

**Performance budget:** Transcription is the expensive operation. We default `--transcribe-audio` to OFF and document the tradeoff. When enabled, we cap per-file transcription at 10 minutes of audio (Whisper "tiny" model is roughly 5-10× realtime on CPU, so a 10-min clip is 1-2 minutes of CPU work). Files exceeding the cap fall back to metadata-only categorization with a warning.

**Tech Stack:** Existing `services.audio.transcriber.AudioTranscriber` (via `AudioModel`), `services.audio.metadata_extractor` for the fallback path, `core.dispatcher.process_audio_files` for the integration point.

**Out of scope:** Caching transcripts to disk (re-running `fo organize` re-transcribes — that's a separate optimization PR). Per-language model selection. Speaker diarization. Subtitle file parallel-tracks.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/core/dispatcher.py` | Modify | Extend `process_audio_files` to optionally accept a transcriber + duration cap |
| `src/core/organizer.py` | Modify | Thread `--transcribe-audio` flag from CLI through `__init__` to `_process_audio_files` |
| `src/cli/organize.py` | Modify | Add `--transcribe-audio` and `--max-transcribe-seconds` flags to `organize` and `preview` |
| `tests/test_organizer_audio_transcription.py` | Create | Unit tests with mocked transcriber: enabled vs disabled, duration cap, missing extra |
| `tests/integration/test_organize_audio_e2e.py` | Create | Integration test: real WAV file + real transcriber, organizer produces a transcript-derived category |
| `docs/USER_GUIDE.md` | Modify | Document the `--transcribe-audio` flag and its performance tradeoff |
| `README.md` | Modify | Update the Optional Feature Packs `[media]` row to mention `--transcribe-audio` |

Plan conventions: see [2A plan](2026-04-27-audio-model-wiring-2a.md) "Conventions for this plan" section.

---

## Task 1: Read the current dispatcher.process_audio_files signature

**Files:**
- Read only: `src/core/dispatcher.py`

- [ ] **Step 1: Find and read the function**

```bash
grep -n "def process_audio_files" src/core/dispatcher.py
```

Then read 30 lines around that match. Capture:

- Current signature (parameters, return type)
- How it constructs `ProcessedFile` objects today
- Whether it already considers a transcript field on `ProcessedFile`

Note the line numbers; subsequent tasks reference them.

---

## Task 2: Extend `ProcessedFile` to optionally carry a transcript

**Files:**
- Modify: `src/services/text_processor.py` (the `ProcessedFile` dataclass at line 23)
- Test: `tests/services/test_processed_file_transcript_field.py`

The `ProcessedFile` dataclass (verified in `src/services/text_processor.py:22-32`)
currently has fields `file_path: Path`, `description: str`, `folder_name: str`,
`filename: str`, plus optional `original_content`, `processing_time`, and
`error`. We add a new optional `transcript: str | None = None` field. Existing
consumers continue to work because the new field has a default value.

- [ ] **Step 1: Write a failing test**

Create `tests/services/test_processed_file_transcript_field.py`:

```python
"""Test that ProcessedFile carries an optional transcript field."""
from __future__ import annotations
from pathlib import Path
import pytest


@pytest.mark.unit
def test_processed_file_default_transcript_is_none(tmp_path: Path) -> None:
    from services.text_processor import ProcessedFile

    pf = ProcessedFile(
        file_path=tmp_path / "x.mp3",
        description="A short audio clip",
        folder_name="Audio",
        filename="x.mp3",
    )
    assert pf.transcript is None


@pytest.mark.unit
def test_processed_file_accepts_transcript(tmp_path: Path) -> None:
    from services.text_processor import ProcessedFile

    pf = ProcessedFile(
        file_path=tmp_path / "podcast.mp3",
        description="News podcast",
        folder_name="News",
        filename="podcast.mp3",
        transcript="Hello and welcome to the news",
    )
    assert pf.transcript == "Hello and welcome to the news"
```

- [ ] **Step 2: Run — expected failure**

```bash
pytest tests/services/test_processed_file_transcript_field.py -v
```

Expected: FAIL — `TypeError: ProcessedFile.__init__() got an unexpected keyword argument 'transcript'`.

- [ ] **Step 3: Add the field**

In `src/services/text_processor.py`, modify the `ProcessedFile` dataclass
(currently at lines 22-32):

```python
@dataclass
class ProcessedFile:
    """Result of file processing."""

    file_path: Path
    description: str
    folder_name: str
    filename: str
    original_content: str | None = None
    processing_time: float = 0.0
    error: str | None = None
    transcript: str | None = None  # NEW: populated for audio files when --transcribe-audio is set
```

- [ ] **Step 4: Run — expected pass**

```bash
pytest tests/services/test_processed_file_transcript_field.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/text_processor.py tests/services/test_processed_file_transcript_field.py
git commit -m "feat(text_processor): add optional transcript field to ProcessedFile"
```

---

## Task 3: Extend dispatcher.process_audio_files to optionally transcribe

**Files:**
- Modify: `src/core/dispatcher.py`
- Test: `tests/test_organizer_audio_transcription.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_organizer_audio_transcription.py`:

```python
"""Unit tests for audio transcription in the organize path."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.dispatcher import process_audio_files


@pytest.mark.unit
class TestDispatcherTranscribe:
    def test_no_transcriber_means_no_transcript_attached(
        self, tmp_path: Path
    ) -> None:
        audio = tmp_path / "a.mp3"
        audio.touch()
        results = process_audio_files(
            [audio],
            extractor_cls=MagicMock(),
            transcriber=None,
        )
        assert all(getattr(r, "transcript", None) is None for r in results)

    def test_transcriber_attaches_transcript(self, tmp_path: Path) -> None:
        audio = tmp_path / "a.mp3"
        audio.touch()
        fake_transcriber = MagicMock()
        fake_transcriber.generate.return_value = "hello world"
        # extractor returns minimal valid metadata
        extractor_cls = MagicMock()
        extractor_cls.return_value.extract.return_value = MagicMock(duration=5.0)

        results = process_audio_files(
            [audio],
            extractor_cls=extractor_cls,
            transcriber=fake_transcriber,
            max_transcribe_seconds=600,
        )
        assert any(r.transcript == "hello world" for r in results)
        fake_transcriber.generate.assert_called_once_with(str(audio))

    def test_transcribe_respects_duration_cap(self, tmp_path: Path) -> None:
        audio = tmp_path / "long.mp3"
        audio.touch()
        fake_transcriber = MagicMock()
        extractor_cls = MagicMock()
        # 20-minute file, cap at 10 minutes -> skip transcription
        extractor_cls.return_value.extract.return_value = MagicMock(duration=1200.0)

        results = process_audio_files(
            [audio],
            extractor_cls=extractor_cls,
            transcriber=fake_transcriber,
            max_transcribe_seconds=600,
        )
        fake_transcriber.generate.assert_not_called()
        assert all(getattr(r, "transcript", None) is None for r in results)
```

- [ ] **Step 2: Run — expected failure (signature does not yet accept `transcriber`)**

- [ ] **Step 3: Modify `process_audio_files` signature and body**

Edit `src/core/dispatcher.py:process_audio_files` to accept new keyword-only parameters:

```python
def process_audio_files(
    files: list[Path],
    *,
    extractor_cls: type,
    transcriber: object | None = None,
    max_transcribe_seconds: float | None = None,
) -> list[ProcessedFile]:
    """Extract metadata; optionally transcribe when within duration cap."""
    extractor = extractor_cls(use_fallback=True)
    results: list[ProcessedFile] = []
    for file_path in files:
        try:
            metadata = extractor.extract(file_path)
        except (OSError, ImportError) as exc:
            logger.warning("audio metadata failed for %s: %s", file_path, exc)
            continue

        transcript: str | None = None
        if transcriber is not None and (
            max_transcribe_seconds is None
            or getattr(metadata, "duration", float("inf")) <= max_transcribe_seconds
        ):
            try:
                transcript = transcriber.generate(str(file_path))
            except (FileNotFoundError, RuntimeError, ImportError) as exc:
                logger.warning("transcription failed for %s: %s", file_path, exc)
                transcript = None

        description, folder_name, filename = _categorize_audio(
            file_path, metadata, transcript=transcript
        )
        results.append(
            ProcessedFile(
                file_path=file_path,
                description=description,
                folder_name=folder_name,
                filename=filename,
                transcript=transcript,
            )
        )
    return results
```

Add `_categorize_audio(file_path, metadata, *, transcript)` as a private helper
that returns the `(description, folder_name, filename)` triple. When `transcript`
is non-empty it uses the transcript content to derive a richer description and
folder name (mirroring how `_process_text_files` uses extracted text); otherwise
it falls back to metadata-only categorization (filename + extension + duration
bucket) — the existing behavior. Read the existing pattern in
`src/core/dispatcher.py:process_audio_files` and copy its categorization
contract verbatim into the metadata-only branch so behavior is preserved when
`transcribe_audio` is off.

- [ ] **Step 4: Run — expected pass**

- [ ] **Step 5: Commit**

```bash
git add src/core/dispatcher.py tests/test_organizer_audio_transcription.py
git commit -m "feat(dispatcher): optional transcription pass for audio files"
```

---

## Task 4: FileOrganizer accepts a transcribe_audio flag

**Files:**
- Modify: `src/core/organizer.py:83` (`__init__`) and `src/core/organizer.py:528` (`_process_audio_files`)
- Test: `tests/test_organizer_audio_transcription.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_organizer_audio_transcription.py`:

```python
@pytest.mark.unit
class TestFileOrganizerTranscribeFlag:
    def test_transcribe_audio_flag_threads_transcriber_to_dispatcher(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.organizer import FileOrganizer
        captured = {}

        def _spy(files, *, extractor_cls, transcriber=None, max_transcribe_seconds=None):
            captured["transcriber"] = transcriber
            captured["max_seconds"] = max_transcribe_seconds
            return []

        monkeypatch.setattr("core.organizer.dispatcher.process_audio_files", _spy)

        organizer = FileOrganizer(
            dry_run=True,
            transcribe_audio=True,
            max_transcribe_seconds=300,
        )
        organizer._process_audio_files([tmp_path / "x.mp3"])

        assert captured["transcriber"] is not None
        assert captured["max_seconds"] == 300

    def test_transcribe_audio_disabled_passes_no_transcriber(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.organizer import FileOrganizer
        captured = {}

        def _spy(files, *, extractor_cls, transcriber=None, max_transcribe_seconds=None):
            captured["transcriber"] = transcriber
            return []

        monkeypatch.setattr("core.organizer.dispatcher.process_audio_files", _spy)

        organizer = FileOrganizer(dry_run=True, transcribe_audio=False)
        organizer._process_audio_files([tmp_path / "x.mp3"])

        assert captured["transcriber"] is None
```

- [ ] **Step 2: Run — expected failure**

- [ ] **Step 3: Modify `FileOrganizer.__init__`**

Add two parameters with sensible defaults:

```python
def __init__(
    self,
    ...,
    transcribe_audio: bool = False,
    max_transcribe_seconds: float | None = 600.0,  # 10-minute default cap
) -> None:
    ...
    self.transcribe_audio = transcribe_audio
    self.max_transcribe_seconds = max_transcribe_seconds
    self._audio_model = None  # lazy-init in _process_audio_files
```

- [ ] **Step 4: Modify `_process_audio_files`**

Replace the body at line 528 with:

```python
def _process_audio_files(self, files: list[Path]) -> list[ProcessedFile]:
    """Extract metadata + optionally transcribe; return processed files."""
    transcriber = None
    if self.transcribe_audio:
        try:
            from models.audio_model import AudioModel
            from models.base import ModelConfig, ModelType

            if self._audio_model is None:
                config = ModelConfig(name="tiny", model_type=ModelType.AUDIO)
                self._audio_model = AudioModel(config)
                self._audio_model.initialize()
            transcriber = self._audio_model
        except ImportError as exc:
            self.console.print(
                f"[yellow]--transcribe-audio requires [media] extra: {exc}. "
                "Falling back to metadata-only.[/yellow]"
            )

    return dispatcher.process_audio_files(
        files,
        extractor_cls=AudioMetadataExtractor,
        transcriber=transcriber,
        max_transcribe_seconds=self.max_transcribe_seconds,
    )
```

Also extend the existing `try/finally` in `_process_all_file_types` to clean up `self._audio_model` if it was initialized:

```python
finally:
    if self.text_processor:
        self.text_processor.cleanup()
    if self.vision_processor:
        self.vision_processor.cleanup()
    if getattr(self, "_audio_model", None):
        self._audio_model.safe_cleanup()
```

- [ ] **Step 5: Run — expected pass**

- [ ] **Step 6: Commit**

```bash
git add src/core/organizer.py tests/test_organizer_audio_transcription.py
git commit -m "feat(organizer): thread transcribe_audio flag through to dispatcher"
```

---

## Task 5: CLI flags `--transcribe-audio` and `--max-transcribe-seconds`

**Files:**
- Modify: `src/cli/organize.py` (the `organize` and `preview` functions)

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_organize_transcribe_flag.py`:

```python
"""Test that the --transcribe-audio CLI flag reaches FileOrganizer."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.main import app


@pytest.mark.unit
def test_transcribe_audio_flag_reaches_organizer(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    runner = CliRunner()
    with patch("cli.organize._check_setup_completed", return_value=True), \
         patch("core.organizer.FileOrganizer") as mock_org_cls:
        mock_org_cls.return_value.organize.return_value.processed_files = 0
        mock_org_cls.return_value.organize.return_value.skipped_files = 0
        mock_org_cls.return_value.organize.return_value.failed_files = 0

        result = runner.invoke(
            app,
            [
                "organize",
                str(input_dir),
                str(output_dir),
                "--transcribe-audio",
                "--max-transcribe-seconds", "300",
            ],
        )

    assert result.exit_code == 0, result.output
    init_kwargs = mock_org_cls.call_args.kwargs
    assert init_kwargs.get("transcribe_audio") is True
    assert init_kwargs.get("max_transcribe_seconds") == 300
```

- [ ] **Step 2: Run — expected failure**

- [ ] **Step 3: Add the flags to `organize` and `preview` in `src/cli/organize.py`**

In both `organize` and `preview` signatures, add two new options after `--no-prefetch`:

```python
    transcribe_audio: bool = typer.Option(
        False,
        "--transcribe-audio",
        help=(
            "Transcribe audio files (requires [media] extra) and use the "
            "transcript for content-aware categorization. Off by default."
        ),
    ),
    max_transcribe_seconds: float = typer.Option(
        600.0,
        "--max-transcribe-seconds",
        min=0.0,
        help=(
            "Skip transcription for audio files longer than this (seconds). "
            "Default: 600 (10 min). Set to 0 to disable the cap entirely."
        ),
    ),
```

Then thread them into the `FileOrganizer(...)` call in both functions:

```python
    organizer = FileOrganizer(
        dry_run=...,
        parallel_workers=resolved_workers,
        prefetch_depth=resolved_prefetch_depth,
        enable_vision=not no_vision,
        no_prefetch=no_prefetch,
        transcribe_audio=transcribe_audio,
        max_transcribe_seconds=max_transcribe_seconds if max_transcribe_seconds > 0 else None,
    )
```

- [ ] **Step 4: Run — expected pass**

- [ ] **Step 5: Commit**

```bash
git add src/cli/organize.py tests/cli/test_organize_transcribe_flag.py
git commit -m "feat(cli): add --transcribe-audio and --max-transcribe-seconds flags"
```

---

## Task 6: End-to-end integration test with a real WAV file

**Files:**
- Create: `tests/integration/test_organize_audio_e2e.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end: fo organize --transcribe-audio on a directory with a WAV file."""
from __future__ import annotations
import struct
import wave
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app


def _silent_wav(path: Path, seconds: float = 1.0) -> None:
    sample_rate = 16000
    n_frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<h", 0) * n_frames)


@pytest.mark.integration
class TestOrganizeAudioEndToEnd:
    @pytest.fixture(autouse=True)
    def _require_faster_whisper(self) -> None:
        pytest.importorskip("faster_whisper")

    def test_organize_with_transcribe_audio_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bypass setup gate
        monkeypatch.setattr("cli.organize._check_setup_completed", lambda: True)

        input_dir = tmp_path / "in"
        input_dir.mkdir()
        _silent_wav(input_dir / "sample.wav")
        output_dir = tmp_path / "out"

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "organize",
                str(input_dir),
                str(output_dir),
                "--dry-run",
                "--transcribe-audio",
            ],
        )
        assert result.exit_code == 0, result.output
        # Smoke: no crash on transcription path; dry-run prevents file moves
```

- [ ] **Step 2: Run**

```bash
pytest tests/integration/test_organize_audio_e2e.py -v
```

Expected: PASS (skipped without `[media]`).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_organize_audio_e2e.py
git commit -m "test(organize): e2e --transcribe-audio with generated silent WAV"
```

---

## Task 7: Documentation honesty pass

**Files:**
- Modify: `README.md` (Optional Feature Packs row)
- Modify: `docs/USER_GUIDE.md` (add a section on audio transcription)

- [ ] **Step 1: Update README**

Replace the `[media]` row with:

```markdown
| Media | `pip install -e ".[media]"` | Audio transcription (faster-whisper) + video scene detection. Use `fo organize --transcribe-audio` to categorize audio by transcript content, or `fo benchmark run --suite audio --transcribe-smoke` to verify the install. |
```

- [ ] **Step 2: Add a User Guide section**

Append to `docs/USER_GUIDE.md` (under the existing "Organizing files" section):

````markdown
### Audio transcription (optional)

When the `[media]` extra is installed, you can categorize audio files by
transcript content rather than just metadata:

```bash
fo organize ~/Downloads ~/Organized --transcribe-audio
```

Transcription is off by default because it's CPU-intensive. The default
duration cap (10 minutes per file, override with `--max-transcribe-seconds`)
prevents podcast-length files from monopolizing the run; over-cap files
fall back to metadata-only categorization with a warning.

The first run downloads the Whisper "tiny" model (~39 MB). Subsequent runs
use the cache.
````

- [ ] **Step 3: Lint markdown**

```bash
pymarkdown -c .pymarkdown.json scan README.md docs/USER_GUIDE.md
```

Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/USER_GUIDE.md
git commit -m "docs: document --transcribe-audio in README and USER_GUIDE"
```

---

## Task 8: Pre-commit + CI verification + PR

- [ ] **Step 1: Run validation**

```bash
bash .claude/scripts/pre-commit-validation.sh
pytest -m "ci" -v
pytest -m "integration" tests/integration/test_organize_audio_e2e.py -v
```

- [ ] **Step 2: Code review**

Invoke `/code-reviewer`. Focus areas: graceful-degrade when `[media]` missing, lifecycle of `_audio_model` (must be cleaned up even on exceptions), duration-cap edge cases (0, None, negative — currently the CLI flag enforces `min=0.0` and 0 means "no cap").

- [ ] **Step 3: Push and open PR**

Title: `feat(organize): transcribe audio for content-aware categorization`

Body should reference §2 of `docs/release/beta-criteria.md` (closes the second-half of "Audio works end-to-end") and link [Step 2A](2026-04-27-audio-model-wiring-2a.md) as the prerequisite.

---

## Verification

After this plan executes:

- `fo organize --transcribe-audio` on a directory containing audio files produces transcript-tagged categorization.
- `fo organize` without the flag remains identical to current behavior (no new dep on `[media]`).
- README and USER_GUIDE describe what `[media]` actually delivers.
- The "Audio works end-to-end" entry in `docs/release/beta-criteria.md` §2 is fully closed (combined with Step 2A, which closes the AudioModel wiring half).
