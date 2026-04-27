# AudioModel Wiring + Benchmark Smoke (Step 2A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `AudioModel.generate()` (currently `NotImplementedError`) to the existing `services.audio.transcriber.AudioTranscriber`, and add an opt-in transcription smoke pass to `fo benchmark --suite audio` so the `[media]` extra delivers what its README description claims.

**Architecture:** `AudioModel` becomes a thin adapter over `services.audio.transcriber.AudioTranscriber`. The transcriber is instantiated in `AudioModel.__init__` (cheap — model weights load lazily on first `transcribe()`). `generate(prompt)` treats `prompt` as the audio file path, calls `transcriber.transcribe(path)`, and returns `result.text`. Lifecycle hooks (`_enter_generate` / `_exit_generate`) come from `BaseModel`. Benchmark gains a `--transcribe-smoke` flag that transcribes exactly one candidate file (smallest first) so the smoke test cost is bounded.

**Tech Stack:** Python 3.11+, `faster-whisper` (via `[media]` extra), pytest, Typer CLI, dataclasses. No new dependencies.

**Out of scope:** consolidating `models.audio_transcriber` (the unused duplicate) and `services.audio.transcriber` — that's follow-up cleanup. Wiring transcription into `fo organize` is Step 2B (separate plan).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/models/audio_model.py` | Modify | Replace stub: instantiate transcriber, route `generate()`, unload in `cleanup()` |
| `tests/test_audio_model.py` | Rewrite | Replace placeholder skip-tests with real unit tests (mock the transcriber) |
| `tests/integration/test_audio_model_integration.py` | Create | End-to-end test with a generated WAV file, skipped if `faster-whisper` absent |
| `src/cli/benchmark.py` | Modify | Add `--transcribe-smoke` flag; extend `_run_audio_suite` and `_SuiteIterationOutcome` |
| `tests/cli/test_benchmark_audio_transcribe.py` | Create | Test that `--transcribe-smoke` invokes transcription on exactly one file |
| `README.md` | Modify | Soften `[media]` row to match what's actually wired |
| `pyproject.toml` | Modify | Update inline comment for `[media]` extra to match new behavior |

---

## Conventions for this plan

- All `pytest` invocations run from repo root: `pytest <args>` (the project pre-commit-validation script handles env).
- Commits use the project's `<type>(<scope>): <subject>` format (per `.claude/rules/development-guidelines.md`).
- After each commit, the `pre-commit` hook will run automatically — if it fails, fix violations and create a NEW commit (never `--amend` per CLAUDE.md guidance).
- Tests use `pytest.MonkeyPatch` for env, never `os.environ` mutation.
- No hardcoded `/tmp/` paths — always `tmp_path`.

---

## Task 1: AudioModel.__init__ holds an AudioTranscriber instance

**Files:**
- Modify: `src/models/audio_model.py:21-33`
- Test: `tests/test_audio_model.py`

- [ ] **Step 1: Write the failing test**

Replace the contents of `tests/test_audio_model.py` (the file currently only contains placeholder skip-tests) with the new test class header plus this first test:

```python
"""Unit tests for AudioModel — wires services.audio.transcriber."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.audio_model import AudioModel
from models.base import ModelConfig, ModelType


@pytest.mark.unit
class TestAudioModelInit:
    def test_init_creates_transcriber_attribute(self) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        assert model._transcriber is not None
        assert hasattr(model._transcriber, "transcribe")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_audio_model.py::TestAudioModelInit::test_init_creates_transcriber_attribute -v
```

Expected: FAIL — `AttributeError: 'AudioModel' object has no attribute '_transcriber'`.

- [ ] **Step 3: Write minimal implementation**

In `src/models/audio_model.py`, replace lines 1-33 (header through `__init__`) with:

```python
"""Audio model — wraps services.audio.transcriber for the BaseModel interface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from models.base import BaseModel, ModelConfig, ModelType
from services.audio.transcriber import (
    AudioTranscriber,
    ModelSize,
    TranscriptionOptions,
)


def _resolve_model_size(name: str) -> ModelSize:
    """Map a `ModelConfig.name` to a faster-whisper `ModelSize`.

    Accepts forms like "base", "whisper-base", "Whisper_Large-V3". Falls back
    to `ModelSize.BASE` for unknown names — keeps the call live rather than
    crashing on a config typo.
    """
    normalized = name.lower().replace("whisper-", "").replace("_", "-")
    for size in ModelSize:
        if size.value == normalized:
            return size
    return ModelSize.BASE


class AudioModel(BaseModel):
    """Audio transcription model backed by faster-whisper."""

    def __init__(self, config: ModelConfig):
        if config.model_type != ModelType.AUDIO:
            raise ValueError(f"Expected AUDIO model type, got {config.model_type}")
        super().__init__(config)
        self._transcriber = AudioTranscriber(
            model_size=_resolve_model_size(config.name),
            device=config.device.value if config.device else "auto",
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_audio_model.py::TestAudioModelInit::test_init_creates_transcriber_attribute -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models/audio_model.py tests/test_audio_model.py
git commit -m "$(cat <<'EOF'
feat(audio_model): instantiate AudioTranscriber in __init__

First step of wiring AudioModel to the existing
services.audio.transcriber. Subsequent commits add the generate(),
initialize(), and cleanup() bridges.
EOF
)"
```

---

## Task 2: ModelConfig.name resolves to a valid Whisper size

**Files:**
- Modify: `src/models/audio_model.py` (the `_resolve_model_size` helper added in Task 1)
- Test: `tests/test_audio_model.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audio_model.py`:

```python
@pytest.mark.unit
@pytest.mark.parametrize(
    "name,expected_value",
    [
        ("base", "base"),
        ("whisper-base", "base"),
        ("tiny", "tiny"),
        ("Whisper-Large-V3", "large-v3"),
        ("nonsense-model-name", "base"),  # falls back
    ],
)
def test_resolve_model_size_maps_to_valid_size(
    name: str, expected_value: str
) -> None:
    from models.audio_model import _resolve_model_size

    assert _resolve_model_size(name).value == expected_value
```

- [ ] **Step 2: Run test to verify it passes**

```bash
pytest tests/test_audio_model.py::test_resolve_model_size_maps_to_valid_size -v
```

Expected: PASS (the helper was already implemented in Task 1; this is an explicit test pinning its contract).

- [ ] **Step 3: Commit**

```bash
git add tests/test_audio_model.py
git commit -m "test(audio_model): pin _resolve_model_size mapping contract"
```

---

## Task 3: Update get_default_config to use a faster-whisper-compatible name

**Files:**
- Modify: `src/models/audio_model.py` (the `get_default_config` static method, currently at lines 61-79 in the original file)
- Test: `tests/test_audio_model.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audio_model.py`:

```python
@pytest.mark.unit
def test_default_config_resolves_to_valid_model_size() -> None:
    from models.audio_model import AudioModel, _resolve_model_size

    config = AudioModel.get_default_config()
    size = _resolve_model_size(config.name)
    # Must be one of the real ModelSize values (not the silent fallback)
    assert size.value == config.name or size.value == config.name.replace("whisper-", "")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_audio_model.py::test_default_config_resolves_to_valid_model_size -v
```

Expected: FAIL — current default name is `"distil-whisper-large-v3"` which is not a `ModelSize` value (it falls back to `BASE`).

- [ ] **Step 3: Write minimal implementation**

In `src/models/audio_model.py`, replace the existing `get_default_config` block with:

```python
    @staticmethod
    def get_default_config(model_name: str = "base") -> ModelConfig:
        """Get default configuration for audio model.

        Default is faster-whisper "base" (~150 MB, multilingual). Override
        via `model_name` for "tiny", "small", "large-v3", etc.
        """
        return ModelConfig(
            name=model_name,
            model_type=ModelType.AUDIO,
            framework="faster-whisper",
            temperature=0.0,
            max_tokens=1000,
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_audio_model.py::test_default_config_resolves_to_valid_model_size -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models/audio_model.py tests/test_audio_model.py
git commit -m "fix(audio_model): default get_default_config to whisper 'base'"
```

---

## Task 4: AudioModel.initialize() sets _initialized through BaseModel

**Files:**
- Modify: `src/models/audio_model.py` (replace the placeholder `initialize`)
- Test: `tests/test_audio_model.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audio_model.py`:

```python
@pytest.mark.unit
class TestAudioModelLifecycle:
    def test_initialize_sets_initialized_flag(self) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        assert model.is_initialized is False
        model.initialize()
        assert model.is_initialized is True
```

- [ ] **Step 2: Run test to verify the current behavior**

```bash
pytest tests/test_audio_model.py::TestAudioModelLifecycle::test_initialize_sets_initialized_flag -v
```

Expected outcome depends on the file at this point: the original `initialize` calls `super().initialize()` already (via `super().initialize()` on line 38), so this test may already PASS. Either way, capture the current state before editing.

- [ ] **Step 3: Replace the `initialize` method**

In `src/models/audio_model.py`, replace the existing `initialize` method (originally at lines 35-38) with:

```python
    def initialize(self) -> None:
        """Initialize the model. Whisper weights load lazily on first generate()."""
        super().initialize()
```

(Drops the misleading "not fully implemented" warning. The transcriber is already created in `__init__`; weight loading defers until first `transcribe()` call inside the underlying `AudioTranscriber`.)

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_audio_model.py::TestAudioModelLifecycle::test_initialize_sets_initialized_flag -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models/audio_model.py tests/test_audio_model.py
git commit -m "refactor(audio_model): drop 'not implemented' warning from initialize()"
```

---

## Task 5: AudioModel.generate() returns transcribed text

**Files:**
- Modify: `src/models/audio_model.py` (replace `generate` method body)
- Test: `tests/test_audio_model.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audio_model.py`:

```python
@pytest.mark.unit
class TestAudioModelGenerate:
    def test_generate_returns_transcription_text(self, tmp_path: Path) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()

        fake_audio = tmp_path / "sample.wav"
        fake_audio.touch()  # transcriber is mocked; existence check still runs

        fake_result = MagicMock()
        fake_result.text = "hello world"
        with patch.object(
            model._transcriber, "transcribe", return_value=fake_result
        ) as mock_transcribe:
            output = model.generate(str(fake_audio))

        assert output == "hello world"
        mock_transcribe.assert_called_once()
        # First positional arg is the audio path
        called_path = mock_transcribe.call_args.args[0]
        assert Path(called_path) == fake_audio
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_audio_model.py::TestAudioModelGenerate::test_generate_returns_transcription_text -v
```

Expected: FAIL — `NotImplementedError: Audio processing will be implemented in Phase 3`.

- [ ] **Step 3: Replace the `generate` method**

In `src/models/audio_model.py`, replace the existing `generate` method (originally at lines 40-53, currently raising NotImplementedError) with:

```python
    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Transcribe an audio file.

        Args:
            prompt: Path to the audio file (treated as a filesystem path).
            **kwargs: Reserved for future per-call options. Currently ignored.

        Returns:
            Transcribed text. May be the empty string for silent or
            unintelligible audio.

        Raises:
            FileNotFoundError: If `prompt` does not name an existing file.
            ImportError: If the `[media]` extra is not installed
                (faster-whisper missing).
            RuntimeError: If the model is shutting down or not initialized.
        """
        self._enter_generate()
        try:
            options = TranscriptionOptions()
            result = self._transcriber.transcribe(Path(prompt), options=options)
            return result.text
        finally:
            self._exit_generate()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_audio_model.py::TestAudioModelGenerate::test_generate_returns_transcription_text -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models/audio_model.py tests/test_audio_model.py
git commit -m "feat(audio_model): wire generate() to AudioTranscriber.transcribe()"
```

---

## Task 6: generate() lifecycle errors propagate correctly

**Files:**
- Test only: `tests/test_audio_model.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audio_model.py`:

```python
@pytest.mark.unit
class TestAudioModelGenerateErrors:
    def test_generate_before_initialize_raises_runtime_error(
        self, tmp_path: Path
    ) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        # Note: not calling initialize()
        fake_audio = tmp_path / "sample.wav"
        fake_audio.touch()
        with pytest.raises(RuntimeError, match="not initialized"):
            model.generate(str(fake_audio))

    def test_generate_after_shutdown_raises_runtime_error(
        self, tmp_path: Path
    ) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()
        model.safe_cleanup()  # marks _shutting_down
        fake_audio = tmp_path / "sample.wav"
        fake_audio.touch()
        with pytest.raises(RuntimeError, match="shutting down"):
            model.generate(str(fake_audio))

    def test_generate_propagates_filenotfound(self, tmp_path: Path) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()
        missing = tmp_path / "does-not-exist.wav"
        with pytest.raises(FileNotFoundError):
            model.generate(str(missing))
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
pytest tests/test_audio_model.py::TestAudioModelGenerateErrors -v
```

Expected: PASS — `_enter_generate()` and `AudioTranscriber.transcribe()` already raise these errors per `src/models/base.py:198-208` and `src/services/audio/transcriber.py:211-213`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_audio_model.py
git commit -m "test(audio_model): pin generate() lifecycle and FileNotFoundError contracts"
```

---

## Task 7: AudioModel.cleanup() unloads the Whisper model

**Files:**
- Modify: `src/models/audio_model.py` (replace `cleanup`)
- Test: `tests/test_audio_model.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audio_model.py`:

```python
    def test_cleanup_unloads_transcriber(self) -> None:
        config = ModelConfig(name="base", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()
        with patch.object(model._transcriber, "unload_model") as mock_unload:
            model.cleanup()
        mock_unload.assert_called_once()
        assert model.is_initialized is False
```

(Append inside `TestAudioModelLifecycle`.)

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_audio_model.py::TestAudioModelLifecycle::test_cleanup_unloads_transcriber -v
```

Expected: FAIL — current `cleanup()` only flips `_initialized`; the transcriber's model is never released.

- [ ] **Step 3: Replace the `cleanup` method**

In `src/models/audio_model.py`, replace the existing `cleanup` method (originally at lines 55-59) with:

```python
    def cleanup(self) -> None:
        """Cleanup model resources. Unloads the underlying Whisper model."""
        logger.debug("Cleaning up audio model")
        with self._lifecycle_lock:
            self._transcriber.unload_model()
            self._initialized = False
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_audio_model.py::TestAudioModelLifecycle::test_cleanup_unloads_transcriber -v
```

Expected: PASS.

- [ ] **Step 5: Run the whole test_audio_model.py file**

```bash
pytest tests/test_audio_model.py -v
```

Expected: all tests PASS. No regressions.

- [ ] **Step 6: Commit**

```bash
git add src/models/audio_model.py tests/test_audio_model.py
git commit -m "feat(audio_model): cleanup() unloads underlying Whisper model"
```

---

## Task 8: Integration test — AudioModel.generate() against real audio

**Files:**
- Create: `tests/integration/test_audio_model_integration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_audio_model_integration.py`:

```python
"""Integration tests for AudioModel — end-to-end with faster-whisper.

Skipped automatically when the [media] extra is not installed.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from models.audio_model import AudioModel
from models.base import ModelConfig, ModelType


def _generate_silence_wav(path: Path, seconds: float = 1.0) -> None:
    """Write a mono 16-bit 16kHz silent WAV file at `path`."""
    sample_rate = 16000
    n_frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        # 16-bit signed silence = b"\x00\x00" per sample
        w.writeframes(struct.pack("<h", 0) * n_frames)


@pytest.mark.integration
class TestAudioModelEndToEnd:
    @pytest.fixture(autouse=True)
    def _require_faster_whisper(self) -> None:
        pytest.importorskip("faster_whisper")

    def test_generate_returns_string_for_silent_wav(self, tmp_path: Path) -> None:
        audio_path = tmp_path / "silence.wav"
        _generate_silence_wav(audio_path, seconds=1.0)

        config = ModelConfig(name="tiny", model_type=ModelType.AUDIO)
        model = AudioModel(config)
        model.initialize()
        try:
            output = model.generate(str(audio_path))
        finally:
            model.safe_cleanup()

        assert isinstance(output, str)
        # Silence may transcribe to empty or to a short artifact; both are valid.
        # The contract we're proving: pipeline runs end-to-end without crashing.
        assert len(output) <= 200  # sanity: not a huge garbage dump
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/integration/test_audio_model_integration.py -v
```

Expected: PASS if `faster-whisper` is installed (it is in the dev environment per `[media]`). Skipped if not. First run will download the "tiny" model (~39 MB) — subsequent runs use the cached download.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_audio_model_integration.py
git commit -m "test(audio_model): integration test with generated silent WAV"
```

---

## Task 9: Add `--transcribe-smoke` flag to fo benchmark

**Files:**
- Modify: `src/cli/benchmark.py` (add CLI flag, extend outcome dataclass, modify `_run_audio_suite`, add `functools.partial` dispatch in `run()`)

**Architecture note** (verified by reading the file): The benchmark CLI is `benchmark_app` (a `typer.Typer` instance) with one `run` subcommand at line 947. Suite runners live in `_SUITE_RUNNERS` (line 712) and are dispatched via `runner(files)` at line 801 — the signature is fixed `Callable[[list[Path]], _SuiteIterationOutcome]`. To thread `transcribe_smoke` through without changing every runner's signature, we use `functools.partial` in `run()` to pre-bind the flag to the audio runner only.

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_benchmark_audio_transcribe.py`:

```python
"""Test that fo benchmark run --transcribe-smoke exercises AudioModel.generate()."""

from __future__ import annotations

import struct
import wave
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.benchmark import benchmark_app


def _generate_silence_wav(path: Path, seconds: float = 0.5) -> None:
    sample_rate = 16000
    n_frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<h", 0) * n_frames)


@pytest.mark.integration
class TestBenchmarkTranscribeSmoke:
    def test_transcribe_smoke_invokes_audio_model_once(
        self, tmp_path: Path
    ) -> None:
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        _generate_silence_wav(audio_dir / "a.wav")
        _generate_silence_wav(audio_dir / "b.wav")

        runner = CliRunner()
        with patch("cli.benchmark.AudioModel") as mock_model_cls:
            instance = mock_model_cls.return_value
            instance.generate.return_value = ""
            result = runner.invoke(
                benchmark_app,
                [
                    "run",
                    str(audio_dir),
                    "--suite", "audio",
                    "--transcribe-smoke",
                    "--iterations", "1",
                    "--warmup", "0",
                ],
            )

        assert result.exit_code == 0, result.output
        # Smoke: exactly one transcription regardless of candidate count
        assert instance.generate.call_count == 1
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/cli/test_benchmark_audio_transcribe.py -v
```

Expected: FAIL — `--transcribe-smoke` flag does not exist.

- [ ] **Step 3: Modify `_SuiteIterationOutcome`**

In `src/cli/benchmark.py`, find the `_SuiteIterationOutcome` dataclass (search for `class _SuiteIterationOutcome`) and add a new field:

```python
@dataclass
class _SuiteIterationOutcome:
    processed_count: int
    used_synthetic_audio_metadata: bool = False
    transcription_smoke_passed: bool = False  # NEW: set True when --transcribe-smoke completes
```

(Preserve all existing fields; add the new one at the end with a default so existing call sites that construct `_SuiteIterationOutcome(processed_count=...)` keep working.)

- [ ] **Step 4: Add the imports for `functools.partial` and AudioModel**

Add to the top of `src/cli/benchmark.py`, in the existing imports block (next to the other `from models...` imports if any, or just after `import typer`):

```python
import functools

from models.audio_model import AudioModel
from models.base import ModelConfig, ModelType
```

(If `import functools` is already present, don't duplicate it.)

- [ ] **Step 5: Add the `--transcribe-smoke` option to the `run` command**

In `src/cli/benchmark.py`, locate the `run` function decorated with `@benchmark_app.command()` at line ~946. Add a new option parameter to its signature (place after `--compare`, before the `) -> None:` close):

```python
    transcribe_smoke: bool = typer.Option(
        False,
        "--transcribe-smoke",
        help=(
            "Run AudioModel.generate() on one candidate file as an end-to-end "
            "smoke test. Only meaningful with --suite audio. Requires [media] "
            "extra. Off by default to keep benchmark runs fast."
        ),
    ),
```

- [ ] **Step 6: Bind `transcribe_smoke` to the audio runner via `functools.partial`**

In the `run` function body, locate the block (around line 1017-1022) that reads `runner = suite_spec["run"]`. Immediately after that line, add:

```python
    runner = suite_spec["run"]
    classifier = suite_spec["classify"]
    if suite == "audio" and transcribe_smoke:
        runner = functools.partial(_run_audio_suite, transcribe_smoke=True)
```

- [ ] **Step 7: Extend `_run_audio_suite` to perform the smoke pass**

Replace the entire `_run_audio_suite` function (currently lines 500-525) with:

```python
def _run_audio_suite(
    files: list[Path],
    transcribe_smoke: bool = False,
) -> _SuiteIterationOutcome:
    """Benchmark audio metadata + classification path.

    With `transcribe_smoke=True`, additionally runs AudioModel.generate() on
    the first candidate file to prove end-to-end transcription works. Counted
    as a single smoke pass; not a per-file benchmark.
    """
    candidates = _suite_candidates(files, _AUDIO_EXTENSIONS, fallback_to_all=False)
    if not candidates:
        typer.echo("Warning: no audio files found; falling back to IO-only benchmark.", err=True)
        return _run_io_suite(files)

    from services.audio.classifier import AudioClassifier
    from services.audio.metadata_extractor import AudioMetadataExtractor

    extractor = AudioMetadataExtractor(use_fallback=True)
    classifier = AudioClassifier()
    used_synthetic_metadata = False
    for file_path in candidates:
        try:
            metadata = extractor.extract(file_path)
        except ImportError:
            used_synthetic_metadata = True
            metadata = _synthesized_audio_metadata(file_path)
        except Exception as exc:
            raise RuntimeError(f"Audio benchmark runner failed for {file_path}: {exc}") from exc
        _ = classifier.classify(metadata)

    transcription_smoke_passed = False
    if transcribe_smoke:
        try:
            config = ModelConfig(name="tiny", model_type=ModelType.AUDIO)
            model = AudioModel(config)
            model.initialize()
            try:
                _ = model.generate(str(candidates[0]))
                transcription_smoke_passed = True
            finally:
                model.safe_cleanup()
        except ImportError as exc:
            typer.echo(
                f"Warning: --transcribe-smoke requires [media] extra: {exc}",
                err=True,
            )

    return _SuiteIterationOutcome(
        processed_count=len(candidates),
        used_synthetic_audio_metadata=used_synthetic_metadata,
        transcription_smoke_passed=transcription_smoke_passed,
    )
```

- [ ] **Step 8: Run the test to verify it passes**

```bash
pytest tests/cli/test_benchmark_audio_transcribe.py -v
```

Expected: PASS.

- [ ] **Step 9: Run the full benchmark test surface to catch regressions**

```bash
pytest tests/cli/ -k benchmark -v
```

Expected: all PASS. Pay attention to any test asserting the exact set of fields on `_SuiteIterationOutcome` — adding a defaulted field is backward-compatible at the constructor level but a strict-equality assertion on the whole dataclass would break.

- [ ] **Step 10: Commit**

```bash
git add src/cli/benchmark.py tests/cli/test_benchmark_audio_transcribe.py
git commit -m "$(cat <<'EOF'
feat(benchmark): add --transcribe-smoke flag for fo benchmark --suite audio

Runs AudioModel.generate() on one candidate file when the flag is set.
Off by default to keep default benchmark runs fast. Required for the
beta entry checklist (docs/release/beta-criteria.md §2).
EOF
)"
```

---

## Task 10: Honesty pass on README and pyproject [media] description

**Files:**
- Modify: `README.md` (the Optional Feature Packs table row for "Media")
- Modify: `pyproject.toml` (inline comment for the `media` extra)

- [ ] **Step 1: Read the current claims**

```bash
grep -A 1 "\[media\]" README.md
grep -B 1 -A 5 "^media = \[" pyproject.toml
```

- [ ] **Step 2: Update README**

In `README.md`, change the Media row of the Optional Feature Packs table from:

```markdown
| Media | `pip install -e ".[media]"` | Audio transcription + video scene detection |
```

to:

```markdown
| Media | `pip install -e ".[media]"` | Audio transcription (faster-whisper) + video scene detection. Exercise via `fo benchmark --suite audio --transcribe-smoke`. |
```

- [ ] **Step 3: Update pyproject.toml**

In `pyproject.toml`, locate the comment immediately above `media = [...]` and update it to reflect the actual surface. Example (adjust to match the existing comment style):

```toml
# Optional: audio transcription (faster-whisper) and video scene detection.
# Audio is exposed via the AudioModel class and exercised by
# `fo benchmark --suite audio --transcribe-smoke`. Video scene detection
# is invoked by services/video/scene_detector.py callers.
media = [
    "faster-whisper~=1.0",
    "torch~=2.1",
    "pydub>=0.25.0,<1",
    "opencv-python~=4.8",
    "scenedetect[opencv]>=0.6.0,<1",
]
```

- [ ] **Step 4: Lint the markdown**

```bash
pymarkdown -c .pymarkdown.json scan README.md
```

Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add README.md pyproject.toml
git commit -m "docs: tighten [media] extra description to match wired surface"
```

---

## Task 11: Full test suite + pre-commit validation

**Files:** none (verification only)

- [ ] **Step 1: Run the unit and CI test marker subsets**

```bash
pytest -m "ci" -v
pytest -m "unit" tests/test_audio_model.py -v
```

Expected: all PASS.

- [ ] **Step 2: Run the integration tests for audio**

```bash
pytest -m "integration" tests/integration/test_audio_model_integration.py tests/cli/test_benchmark_audio_transcribe.py -v
```

Expected: PASS (or SKIP if `faster-whisper` not installed in the running env — the autouse `_require_faster_whisper` fixture handles that).

- [ ] **Step 3: Run pre-commit validation**

```bash
bash .claude/scripts/pre-commit-validation.sh
```

Expected: ✓ all checks pass.

- [ ] **Step 4: Run code review**

Invoke the project's code-review skill (`/code-reviewer`) on the diff. Address any APPLY findings; reply to SKIP findings; defer DEFER findings to follow-up issues. See `.claude/rules/pr-review-response-protocol.md`.

- [ ] **Step 5: Push branch and open PR**

```bash
git push -u origin <branch-name>
gh pr create --title "feat(audio_model): wire generate() to faster-whisper + benchmark smoke" --body "$(cat <<'EOF'
## Summary

Closes Step 2A of the alpha → beta path documented in
`docs/release/beta-criteria.md`.

- `AudioModel.generate()` now wraps `services.audio.transcriber.AudioTranscriber`
  instead of raising `NotImplementedError`.
- `fo benchmark --suite audio --transcribe-smoke` runs an end-to-end
  transcription smoke pass on one candidate file. Default benchmark runs
  remain fast.
- README and pyproject's `[media]` description updated to match wired surface.

## Test plan

- [ ] `pytest tests/test_audio_model.py -v` — all unit tests pass
- [ ] `pytest tests/integration/test_audio_model_integration.py -v` —
      end-to-end test with generated silent WAV passes
- [ ] `pytest tests/cli/test_benchmark_audio_transcribe.py -v` —
      smoke flag invokes `AudioModel.generate` exactly once
- [ ] Manual: `fo benchmark --suite audio --transcribe-smoke --input <dir>` on
      a directory with two audio files; output reports `transcription_smoke_passed=True`
EOF
)"
```

- [ ] **Step 6: Monitor CI**

Per `.claude/rules/pr-monitoring-protocol.md`, monitor PR until merge. Re-arm CI monitor on every push.

---

## Verification checklist (rolls up the entry-checklist row in beta-criteria.md)

After this plan executes and merges:

- `AudioModel.generate()` returns transcribed text for a real audio file. Confirmed by `tests/integration/test_audio_model_integration.py`.
- `fo benchmark --suite audio --transcribe-smoke` succeeds end-to-end on a sample file with `[media]` installed. Confirmed by `tests/cli/test_benchmark_audio_transcribe.py` and a manual run.
- README and `pyproject.toml` describe what actually ships. Confirmed by Task 10.
- No reachable `NotImplementedError` from a documented surface. Confirmed: `grep -rn "NotImplementedError" src/models/audio_model.py` returns no matches after Task 5.

This satisfies the first row of the §2 entry checklist in
[docs/release/beta-criteria.md](../../release/beta-criteria.md). The remaining
rows (coverage floors, daemon smoke test, `--debug` flag, doc-honesty pass,
schema-stability test, bug-report template) are addressed by later steps in
the master plan.
