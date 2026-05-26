"""Configuration schema for File Organizer.

Defines the top-level AppConfig and ModelPreset dataclasses that provide
a unified configuration interface across all modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config.defaults import DEFAULT_MODEL

# F6 (hardening roadmap #159): single source of truth for the config
# schema version. Bump this when introducing a breaking change to the
# serialized shape (new required field, renamed field, type change on
# an existing field), and register a corresponding migration in
# ``src/config/migrations.py::MIGRATIONS`` so existing ``config.yaml``
# files upgrade cleanly on next load. ``AppConfig.version`` defaults
# to this constant — bumping the constant means ``AppConfig()`` starts
# reporting the new version, and ``ConfigManager.save`` always stamps
# it into the output regardless of the in-memory object's version.
CURRENT_SCHEMA_VERSION = "1.0"

# Legacy baseline for pre-F6 ``config.yaml`` files — those written
# before the version field existed, or with ``version: null``. They
# correspond semantically to schema 1.0 (the version in effect when
# the field was added). Keeping this as a SEPARATE constant from
# ``CURRENT_SCHEMA_VERSION`` means a future bump to 2.0 correctly
# routes unversioned configs through the 1.0→2.0 migration instead
# of silently skipping it (codex P2 PRRT_kwDOR_Rkws59fwMM). Never
# change this — new baselines introduce new constants.
LEGACY_CONFIG_VERSION = "1.0"


@dataclass
class ModelPreset:
    """Preset configuration for AI models.

    Args:
        text_model: Ollama model name for text processing.
        vision_model: Ollama model name for vision processing.
        temperature: Generation temperature (0.0-1.0).
        max_tokens: Maximum tokens for generation.
        device: Device for inference (auto, cpu, cuda, mps, metal).
        framework: Inference framework (ollama, llama_cpp, mlx).
        model_path: Filesystem path to the GGUF/MLX model file, used by
            the local frameworks (``llama_cpp`` / ``mlx``). Ignored by
            ``ollama`` (which loads by name from its own model registry).
            Defaults to ``None``; required at runtime when
            ``framework`` is ``llama_cpp`` or ``mlx`` and
            ``FO_LLAMA_CPP_MODEL_PATH`` / ``FO_MLX_MODEL_PATH`` are
            unset. Persisting it here lets profile-only users opt into
            those backends without exporting env vars on every run
            (#423 unblocks #408's framework→provider mapping for
            profile-based configs).
    """

    text_model: str = DEFAULT_MODEL
    vision_model: str = DEFAULT_MODEL
    temperature: float = 0.5
    max_tokens: int = 3000
    device: str = "auto"
    framework: str = "ollama"
    model_path: str | None = None


@dataclass
class UpdateSettings:
    """Auto-update preferences.

    Args:
        check_on_startup: Check for updates when the app launches.
        interval_hours: Minimum hours between update checks.
        include_prereleases: Include pre-release versions.
        repo: GitHub repository used for update checks.
    """

    check_on_startup: bool = True
    interval_hours: int = 24
    include_prereleases: bool = False
    repo: str = "curdriceaurora/fo-core"


@dataclass
class VisionSettings:
    """Vision model configuration.

    Args:
        max_long_edge: Maximum length of longest image edge before downscaling.
            Large images are resized to this dimension (preserving aspect ratio)
            before being sent to the vision model. Default: 1024 px.
            Min: 256, Max: 4096.
        svg_max_input_bytes: Hard cap on the size of an .svg file before
            rasterization (issue #415). Files exceeding this are rejected
            with an ``OSError`` so a 500 MB SVG cannot exhaust memory at
            the read step. Default: 5 MiB (5_242_880).
    """

    max_long_edge: int = 1024
    svg_max_input_bytes: int = 5 * 1024 * 1024

    def __post_init__(self) -> None:
        """Reject non-positive ``svg_max_input_bytes`` at construction.

        CodeRabbit Minor on PR #428: a zero or negative cap silently
        rejects every SVG (the precheck compares
        ``stat().st_size > max_bytes``). Surface the malformed YAML
        loudly instead of producing a hard-to-diagnose "always rejected"
        symptom at runtime.
        """
        if not isinstance(self.svg_max_input_bytes, int) or self.svg_max_input_bytes <= 0:
            raise ValueError(
                f"vision.svg_max_input_bytes must be a positive int "
                f"(got {self.svg_max_input_bytes!r})"
            )


@dataclass
class ProcessingSettings:
    """Per-file processing configuration.

    Args:
        timeout_per_file: Maximum seconds spent on a single file before the
            dispatcher abandons the in-flight task and moves on. Default:
            300 (5 minutes). Values below ~60s tend to false-positive on
            vision models running large images; values above ~600s reduce
            the protection against genuine hangs (e.g. a model that's
            stuck in a generation loop). The dispatcher cannot cancel a
            running Ollama call — see issue #396 — so the timeout only
            governs when we stop waiting, not when the underlying thread
            actually terminates.
        vision_base_timeout_s: Floor of the adaptive vision timeout (#407).
            Every image gets at least this many seconds. Default 30s —
            enough for a small screenshot on a fast vision model.
        vision_per_mb_factor_s: Per-MB scaling factor for the adaptive
            vision timeout (#407). The computed timeout is
            ``vision_base_timeout_s + file_size_mb * vision_per_mb_factor_s``,
            then clamped to ``vision_max_timeout_s``. Default 15s/MB.
        vision_max_timeout_s: Ceiling of the adaptive vision timeout (#407).
            Should not exceed ``timeout_per_file`` (the dispatcher's hard
            kill-switch). Default 300s — same as ``timeout_per_file`` so
            the adaptive value never silently outlives the dispatcher.
        low_confidence_threshold: Files whose categorization confidence
            falls below this score appear in the summary's "Review
            recommended" section and in the per-file ``confidence=``
            log line as low-confidence placements (#409). Range
            ``(0.0, 1.0]``; default 0.5 — half the maximum so EXIF
            fallbacks (0.5) and filename-only fallbacks (0.3, from
            #406) are surfaced for review while happy-path vision /
            text inferences (1.0) are not.
    """

    timeout_per_file: float = 300.0
    vision_base_timeout_s: float = 30.0
    vision_per_mb_factor_s: float = 15.0
    vision_max_timeout_s: float = 300.0
    low_confidence_threshold: float = 0.5

    def __post_init__(self) -> None:
        """Reject invalid timeout values at construction time (#396, #407)."""
        if self.timeout_per_file <= 0:
            raise ValueError(
                f"processing.timeout_per_file must be > 0, got {self.timeout_per_file}"
            )
        if self.vision_base_timeout_s <= 0:
            raise ValueError(
                f"processing.vision_base_timeout_s must be > 0, got {self.vision_base_timeout_s}"
            )
        if self.vision_per_mb_factor_s < 0:
            raise ValueError(
                f"processing.vision_per_mb_factor_s must be >= 0, got {self.vision_per_mb_factor_s}"
            )
        if self.vision_max_timeout_s <= 0:
            raise ValueError(
                f"processing.vision_max_timeout_s must be > 0, got {self.vision_max_timeout_s}"
            )
        if self.vision_base_timeout_s > self.vision_max_timeout_s:
            raise ValueError(
                f"processing.vision_base_timeout_s ({self.vision_base_timeout_s}) "
                f"must be <= vision_max_timeout_s ({self.vision_max_timeout_s})"
            )
        if not (0.0 < self.low_confidence_threshold <= 1.0):
            raise ValueError(
                "processing.low_confidence_threshold must be in (0.0, 1.0], "
                f"got {self.low_confidence_threshold}"
            )


@dataclass
class AppConfig:
    """Top-level application configuration.

    All fields have defaults so ``AppConfig()`` works standalone.
    Module-specific configs are optional and only loaded when their
    feature is used.

    Args:
        profile_name: Name of this configuration profile.
        version: Configuration schema version.
        default_methodology: Default organization methodology (none, para, jd).
        setup_completed: Whether the guided setup wizard has been completed.
        models: AI model preset configuration.
        updates: Auto-update preferences.
        vision: Vision model configuration.
        processing: Per-file processing configuration (timeout_per_file).
        watcher: Watcher module config overrides.
        daemon: Daemon module config overrides.
        parallel: Parallel processing config overrides.
        pipeline: Pipeline config overrides.
        events: Event system config overrides.
        deploy: Deployment config overrides.
        para: PARA methodology config overrides.
        johnny_decimal: Johnny Decimal config overrides.
    """

    profile_name: str = "default"
    version: str = CURRENT_SCHEMA_VERSION
    default_methodology: str = "none"
    setup_completed: bool = False
    models: ModelPreset = field(default_factory=ModelPreset)
    updates: UpdateSettings = field(default_factory=UpdateSettings)
    vision: VisionSettings = field(default_factory=VisionSettings)
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)

    # Module-specific config overrides stored as dicts.
    # Delegated to module config constructors by ConfigManager.
    watcher: dict[str, Any] | None = None
    daemon: dict[str, Any] | None = None
    parallel: dict[str, Any] | None = None
    pipeline: dict[str, Any] | None = None
    events: dict[str, Any] | None = None
    deploy: dict[str, Any] | None = None
    para: dict[str, Any] | None = None
    johnny_decimal: dict[str, Any] | None = None
