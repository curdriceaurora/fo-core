"""Tests for configuration schema dataclasses."""

from __future__ import annotations

import pytest

from config.defaults import DEFAULT_MODEL
from config.schema import (
    AppConfig,
    ModelPreset,
    ProcessingSettings,
    UpdateSettings,
    VisionSettings,
)

pytestmark = [pytest.mark.unit, pytest.mark.smoke, pytest.mark.ci]


@pytest.mark.unit
class TestModelPreset:
    """Test suite for ModelPreset dataclass."""

    def test_defaults(self) -> None:
        """All defaults should be sensible out of the box."""
        preset = ModelPreset()
        assert preset.text_model == DEFAULT_MODEL
        assert preset.vision_model == DEFAULT_MODEL
        assert preset.temperature == 0.5
        assert preset.max_tokens == 3000
        assert preset.device == "auto"
        assert preset.framework == "ollama"
        # #408 / #423: profile-persistable model_path defaults to None
        # (Ollama doesn't need it; llama_cpp / mlx do).
        assert preset.model_path is None

    def test_custom_values(self) -> None:
        """Custom values should override defaults."""
        preset = ModelPreset(
            text_model="llama3:8b",
            vision_model="llava:13b",
            temperature=0.8,
            max_tokens=4096,
            device="cuda",
            framework="llama_cpp",
            model_path="/models/qwen3.gguf",
        )
        assert preset.text_model == "llama3:8b"
        assert preset.vision_model == "llava:13b"
        assert preset.temperature == 0.8
        assert preset.max_tokens == 4096
        assert preset.device == "cuda"
        assert preset.framework == "llama_cpp"
        assert preset.model_path == "/models/qwen3.gguf"

    def test_partial_override(self) -> None:
        """Partially overriding fields keeps other defaults intact."""
        preset = ModelPreset(temperature=0.9)
        assert preset.temperature == 0.9
        assert preset.text_model == DEFAULT_MODEL
        assert preset.device == "auto"
        assert preset.model_path is None


@pytest.mark.unit
class TestUpdateSettings:
    """Test suite for UpdateSettings dataclass."""

    def test_defaults(self) -> None:
        """Default update settings should be sensible."""
        settings = UpdateSettings()
        assert settings.check_on_startup is True
        assert settings.interval_hours == 24
        assert settings.include_prereleases is False
        assert settings.repo == "curdriceaurora/fo-core"

    def test_custom_values(self) -> None:
        """Custom update settings should override defaults."""
        settings = UpdateSettings(
            check_on_startup=False,
            interval_hours=12,
            include_prereleases=True,
            repo="myuser/myrepo",
        )
        assert settings.check_on_startup is False
        assert settings.interval_hours == 12
        assert settings.include_prereleases is True
        assert settings.repo == "myuser/myrepo"


@pytest.mark.unit
class TestAppConfig:
    """Test suite for AppConfig dataclass."""

    def test_defaults(self) -> None:
        """AppConfig() with no args should produce a valid default config."""
        config = AppConfig()
        assert config.profile_name == "default"
        assert config.version == "1.0"
        assert config.default_methodology == "none"
        assert isinstance(config.models, ModelPreset)
        assert isinstance(config.updates, UpdateSettings)

    def test_module_overrides_default_to_none(self) -> None:
        """Optional module configs should default to None."""
        config = AppConfig()
        assert config.watcher is None
        assert config.daemon is None
        assert config.parallel is None
        assert config.pipeline is None
        assert config.events is None
        assert config.deploy is None
        assert config.para is None
        assert config.johnny_decimal is None

    def test_custom_profile(self) -> None:
        """Custom profile name and methodology."""
        config = AppConfig(profile_name="work", default_methodology="para")
        assert config.profile_name == "work"
        assert config.default_methodology == "para"

    def test_nested_model_preset(self) -> None:
        """Nested ModelPreset should be accessible."""
        custom_models = ModelPreset(temperature=0.3, device="mps")
        config = AppConfig(models=custom_models)
        assert config.models.temperature == 0.3
        assert config.models.device == "mps"

    def test_nested_update_settings(self) -> None:
        """Nested UpdateSettings should be accessible."""
        custom_updates = UpdateSettings(interval_hours=6)
        config = AppConfig(updates=custom_updates)
        assert config.updates.interval_hours == 6

    def test_module_override_dicts(self) -> None:
        """Module-specific dict overrides should be stored as-is."""
        config = AppConfig(
            watcher={"poll_interval": 5},
            daemon={"auto_start": True},
            parallel={"max_workers": 4},
            pipeline={"batch_size": 10},
            events={"buffer_size": 100},
            deploy={"target": "docker"},
            para={"auto_archive": True},
            johnny_decimal={"root": "10-19"},
        )
        assert config.watcher == {"poll_interval": 5}
        assert config.daemon == {"auto_start": True}
        assert config.parallel == {"max_workers": 4}
        assert config.pipeline == {"batch_size": 10}
        assert config.events == {"buffer_size": 100}
        assert config.deploy == {"target": "docker"}
        assert config.para == {"auto_archive": True}
        assert config.johnny_decimal == {"root": "10-19"}

    def test_models_factory_creates_independent_instances(self) -> None:
        """Each AppConfig should get its own ModelPreset instance."""
        config1 = AppConfig()
        config2 = AppConfig()
        assert config1.models is not config2.models

    def test_updates_factory_creates_independent_instances(self) -> None:
        """Each AppConfig should get its own UpdateSettings instance."""
        config1 = AppConfig()
        config2 = AppConfig()
        assert config1.updates is not config2.updates


@pytest.mark.unit
class TestProcessingSettings:
    """Test suite for ProcessingSettings dataclass (#396)."""

    def test_default_timeout_per_file_is_300s(self) -> None:
        """Default timeout matches the previous hardcoded value (issue #396)."""
        settings = ProcessingSettings()
        assert settings.timeout_per_file == 300.0

    def test_custom_timeout_per_file(self) -> None:
        """Custom timeout values override the default."""
        assert ProcessingSettings(timeout_per_file=60.0).timeout_per_file == 60.0
        assert ProcessingSettings(timeout_per_file=900.0).timeout_per_file == 900.0

    def test_zero_timeout_rejected(self) -> None:
        """Zero timeout is rejected — it would abandon every task immediately."""
        with pytest.raises(ValueError, match="must be > 0"):
            ProcessingSettings(timeout_per_file=0.0)

    def test_negative_timeout_rejected(self) -> None:
        """Negative timeout is rejected at construction."""
        with pytest.raises(ValueError, match="must be > 0"):
            ProcessingSettings(timeout_per_file=-30.0)

    def test_appconfig_processing_default(self) -> None:
        """AppConfig.processing is a ProcessingSettings with default timeout."""
        config = AppConfig()
        assert isinstance(config.processing, ProcessingSettings)
        assert config.processing.timeout_per_file == 300.0

    def test_processing_factory_creates_independent_instances(self) -> None:
        """Each AppConfig should get its own ProcessingSettings instance."""
        config1 = AppConfig()
        config2 = AppConfig()
        assert config1.processing is not config2.processing

    def test_adaptive_vision_timeout_defaults(self) -> None:
        """Adaptive vision timeout fields default to 30/15/300 (#407)."""
        settings = ProcessingSettings()
        assert settings.vision_base_timeout_s == 30.0
        assert settings.vision_per_mb_factor_s == 15.0
        assert settings.vision_max_timeout_s == 300.0

    def test_vision_base_zero_rejected(self) -> None:
        """vision_base_timeout_s <= 0 is rejected."""
        with pytest.raises(ValueError, match="vision_base_timeout_s must be > 0"):
            ProcessingSettings(vision_base_timeout_s=0.0)

    def test_vision_per_mb_factor_negative_rejected(self) -> None:
        """vision_per_mb_factor_s < 0 is rejected (zero is allowed: flat timeout)."""
        with pytest.raises(ValueError, match="vision_per_mb_factor_s must be >= 0"):
            ProcessingSettings(vision_per_mb_factor_s=-1.0)

    def test_vision_per_mb_factor_zero_allowed(self) -> None:
        """vision_per_mb_factor_s == 0 is fine — yields a flat base timeout per file."""
        settings = ProcessingSettings(vision_per_mb_factor_s=0.0)
        assert settings.vision_per_mb_factor_s == 0.0

    def test_vision_max_zero_rejected(self) -> None:
        """vision_max_timeout_s <= 0 is rejected."""
        with pytest.raises(ValueError, match="vision_max_timeout_s must be > 0"):
            ProcessingSettings(vision_max_timeout_s=0.0)

    def test_vision_base_greater_than_max_rejected(self) -> None:
        """Base above max is nonsensical (clamp would always trigger)."""
        with pytest.raises(ValueError, match="must be <= vision_max_timeout_s"):
            ProcessingSettings(vision_base_timeout_s=400.0, vision_max_timeout_s=300.0)

    def test_low_confidence_threshold_default(self) -> None:
        """Default threshold matches the EXIF-fallback score (#409)."""
        assert ProcessingSettings().low_confidence_threshold == 0.5

    def test_low_confidence_threshold_custom(self) -> None:
        """Threshold is tunable inside the (0, 1] range."""
        s = ProcessingSettings(low_confidence_threshold=0.8)
        assert s.low_confidence_threshold == 0.8

    def test_low_confidence_threshold_accepts_one(self) -> None:
        """Upper bound is inclusive — 1.0 means 'review everything not happy-path'."""
        s = ProcessingSettings(low_confidence_threshold=1.0)
        assert s.low_confidence_threshold == 1.0

    def test_low_confidence_threshold_rejects_zero(self) -> None:
        """0.0 makes the threshold useless (nothing would ever fall below it)."""
        with pytest.raises(ValueError, match="low_confidence_threshold"):
            ProcessingSettings(low_confidence_threshold=0.0)

    def test_low_confidence_threshold_rejects_above_one(self) -> None:
        """Values > 1.0 are nonsensical (confidences cap at 1.0)."""
        with pytest.raises(ValueError, match="low_confidence_threshold"):
            ProcessingSettings(low_confidence_threshold=1.5)

    def test_low_confidence_threshold_rejects_negative(self) -> None:
        """Negative threshold is rejected."""
        with pytest.raises(ValueError, match="low_confidence_threshold"):
            ProcessingSettings(low_confidence_threshold=-0.1)


@pytest.mark.integration
class TestVisionSettingsValidation:
    """#415 / CodeRabbit Minor on PR #428.

    Marked ``integration`` so the per-module integration coverage gate
    sees the new ``__post_init__`` validation branch — without that
    marker the rejection paths are unit-only and the integration tier
    reports schema.py at 98% (below the 99.5% floor).
    """

    def test_default_svg_max_input_bytes(self) -> None:
        assert VisionSettings().svg_max_input_bytes == 5 * 1024 * 1024

    def test_custom_svg_max_input_bytes_accepted(self) -> None:
        assert VisionSettings(svg_max_input_bytes=10_485_760).svg_max_input_bytes == 10_485_760

    def test_zero_svg_max_input_bytes_rejected(self) -> None:
        with pytest.raises(ValueError, match="svg_max_input_bytes"):
            VisionSettings(svg_max_input_bytes=0)

    def test_negative_svg_max_input_bytes_rejected(self) -> None:
        with pytest.raises(ValueError, match="svg_max_input_bytes"):
            VisionSettings(svg_max_input_bytes=-1)
