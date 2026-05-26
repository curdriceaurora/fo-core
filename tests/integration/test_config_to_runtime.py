"""Integration tests for Gap P1: Config-to-Runtime.

Verifies that configuration choices (model names, provider, parallel workers,
dry_run, temperature, profiles) actually flow through to the runtime objects
that use them.  These are the wiring tests that unit mocks hide.

All tests use real ``FileOrganizer``, ``TextProcessor``, and ``VisionProcessor``
instances — only the Ollama/OpenAI HTTP clients are stubbed.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from config.provider_env import get_model_configs
from config.schema import AppConfig, ModelPreset, ProcessingSettings
from core.organizer import FileOrganizer
from models.base import ModelConfig, ModelType
from services.text_processor import TextProcessor

from .conftest import make_text_config, make_vision_config

pytestmark = [pytest.mark.integration]


class TestConfigModelSelection:
    """Config model names flow to the organizer and processors."""

    def test_explicit_model_config_flows_to_organizer(self) -> None:
        """Explicit ModelConfig params override all defaults."""
        text_cfg = make_text_config(name="custom-text:7b")
        vision_cfg = make_vision_config(name="custom-vision:13b")

        org = FileOrganizer(
            text_model_config=text_cfg,
            vision_model_config=vision_cfg,
            dry_run=True,
        )

        assert org.text_model_config.name == "custom-text:7b"
        assert org.vision_model_config.name == "custom-vision:13b"

    def test_env_provider_openai_flows_to_model_config(self) -> None:
        """FO_PROVIDER=openai produces OpenAI model configs."""
        env = {
            "FO_PROVIDER": "openai",
            "FO_OPENAI_API_KEY": "sk-test-key",
            "FO_OPENAI_MODEL": "gpt-4o",
            "FO_OPENAI_VISION_MODEL": "gpt-4o-vision",
        }
        with patch.dict(os.environ, env, clear=False):
            text_cfg, vision_cfg = get_model_configs()

        assert text_cfg.provider == "openai"
        assert text_cfg.name == "gpt-4o"
        assert text_cfg.api_key == "sk-test-key"
        assert vision_cfg.provider == "openai"
        assert vision_cfg.name == "gpt-4o-vision"

    def test_profile_switch_changes_model(
        self,
    ) -> None:
        """Loading a different profile produces different model names."""
        custom_preset = ModelPreset(
            text_model="llama3:8b",
            vision_model="llava:13b",
        )
        app_cfg = AppConfig(models=custom_preset)

        text_cfg = ModelConfig(name="llama3:8b", model_type=ModelType.TEXT)
        vision_cfg = ModelConfig(name="llava:13b", model_type=ModelType.VISION)

        clean_env = {
            "FO_PROVIDER": "",
            "FO_PROFILE": "work",
        }
        with (
            patch.dict(os.environ, clean_env, clear=False),
            patch("config.manager.ConfigManager") as mock_cls,
        ):
            mgr = mock_cls.return_value
            mgr.load.return_value = app_cfg
            mgr.to_text_model_config.return_value = text_cfg
            mgr.to_vision_model_config.return_value = vision_cfg

            resolved_text, resolved_vision = get_model_configs()

        assert resolved_text.name == "llama3:8b"
        assert resolved_vision.name == "llava:13b"


class TestConfigParallelWorkers:
    """Parallel worker config flows to ParallelProcessor."""

    def test_parallel_workers_flows_to_organizer(self) -> None:
        """parallel_workers param sets ParallelConfig.max_workers."""
        text_cfg = make_text_config()
        vision_cfg = make_vision_config()

        org = FileOrganizer(
            text_model_config=text_cfg,
            vision_model_config=vision_cfg,
            dry_run=True,
            parallel_workers=4,
        )

        assert org.parallel_config.max_workers == 4

    def test_default_parallel_workers_is_none(self) -> None:
        """Default parallel_workers is None (auto-detect CPU count)."""
        text_cfg = make_text_config()
        vision_cfg = make_vision_config()

        org = FileOrganizer(
            text_model_config=text_cfg,
            vision_model_config=vision_cfg,
            dry_run=True,
        )

        assert org.parallel_config.max_workers is None


class TestConfigDryRun:
    """dry_run prevents file creation on disk."""

    def test_dry_run_prevents_file_moves(
        self,
        stub_all_models: None,
        integration_source_dir: Path,
        integration_output_dir: Path,
    ) -> None:
        """Organize with dry_run=True creates no files in output dir."""
        text_cfg = make_text_config()
        vision_cfg = make_vision_config()

        org = FileOrganizer(
            text_model_config=text_cfg,
            vision_model_config=vision_cfg,
            dry_run=True,
        )

        result = org.organize(
            input_path=str(integration_source_dir),
            output_path=str(integration_output_dir),
        )

        # Output dir should remain empty (no files moved)
        output_files = list(integration_output_dir.rglob("*"))
        output_files = [f for f in output_files if f.is_file()]
        assert len(output_files) == 0

        # But files should still be reported as processed (3 files in source dir)
        assert result.total_files == 3


class TestConfigTemperature:
    """Temperature from config propagates to model generate options."""

    def test_temperature_propagates_to_text_model(
        self,
        stub_text_model_init: None,
    ) -> None:
        """Temperature set in ModelConfig reaches the generate() call."""
        custom_temp = 0.9
        text_cfg = make_text_config(temperature=custom_temp)

        processor = TextProcessor(config=text_cfg)
        processor.initialize()

        assert processor.text_model.config.temperature == custom_temp

    def test_default_temperature_is_half(
        self,
        stub_text_model_init: None,
    ) -> None:
        """Default temperature is 0.5."""
        text_cfg = make_text_config()

        processor = TextProcessor(config=text_cfg)
        processor.initialize()

        assert processor.text_model.config.temperature == 0.5


class TestProcessingSettingsToOrganizer:
    """ProcessingSettings.timeout_per_file flows into the live organizer (#396)."""

    def test_default_processing_settings_yield_300s_timeout(self) -> None:
        """AppConfig().processing.timeout_per_file matches the FileOrganizer default."""
        app_cfg = AppConfig()
        assert isinstance(app_cfg.processing, ProcessingSettings)
        assert app_cfg.processing.timeout_per_file == 300.0

        # The same value lands in a fresh FileOrganizer's ParallelConfig.
        org = FileOrganizer(
            text_model_config=make_text_config(),
            vision_model_config=make_vision_config(),
            dry_run=True,
            timeout_per_file=app_cfg.processing.timeout_per_file,
        )
        assert org.parallel_config.timeout_per_file == 300.0

    def test_custom_processing_settings_propagate_through_organizer(self) -> None:
        """A non-default timeout in AppConfig.processing reaches ParallelConfig."""
        app_cfg = AppConfig(processing=ProcessingSettings(timeout_per_file=120.0))
        assert app_cfg.processing.timeout_per_file == 120.0

        org = FileOrganizer(
            text_model_config=make_text_config(),
            vision_model_config=make_vision_config(),
            dry_run=True,
            timeout_per_file=app_cfg.processing.timeout_per_file,
        )
        assert org.parallel_config.timeout_per_file == 120.0

    def test_processing_settings_rejects_zero_and_negative(self) -> None:
        """__post_init__ validation rejects non-positive values."""
        with pytest.raises(ValueError, match="must be > 0"):
            ProcessingSettings(timeout_per_file=0.0)
        with pytest.raises(ValueError, match="must be > 0"):
            ProcessingSettings(timeout_per_file=-1.0)

    def test_adaptive_vision_fields_default_to_30_15_300(self) -> None:
        """ProcessingSettings exposes the #407 fields with the documented defaults."""
        app_cfg = AppConfig()
        assert app_cfg.processing.vision_base_timeout_s == 30.0
        assert app_cfg.processing.vision_per_mb_factor_s == 15.0
        assert app_cfg.processing.vision_max_timeout_s == 300.0

    def test_adaptive_vision_validation_rejects_bad_inputs(self) -> None:
        """__post_init__ rejects each malformed adaptive-vision field (#407)."""
        with pytest.raises(ValueError, match="vision_base_timeout_s must be > 0"):
            ProcessingSettings(vision_base_timeout_s=0.0)
        with pytest.raises(ValueError, match="vision_per_mb_factor_s must be >= 0"):
            ProcessingSettings(vision_per_mb_factor_s=-1.0)
        with pytest.raises(ValueError, match="vision_max_timeout_s must be > 0"):
            ProcessingSettings(vision_max_timeout_s=0.0)
        with pytest.raises(ValueError, match="must be <= vision_max_timeout_s"):
            ProcessingSettings(vision_base_timeout_s=400.0, vision_max_timeout_s=300.0)

    def test_compute_vision_timeout_uses_processing_settings(self) -> None:
        """The helper round-trips with AppConfig.processing (#407)."""
        from services.vision_processor import compute_vision_timeout

        app_cfg = AppConfig(
            processing=ProcessingSettings(
                vision_base_timeout_s=60.0,
                vision_per_mb_factor_s=5.0,
                vision_max_timeout_s=120.0,
            )
        )

        assert compute_vision_timeout(0, app_cfg.processing) == 60.0
        # 10MB → 60 + 50 = 110s (within max)
        assert compute_vision_timeout(10 * 1024 * 1024, app_cfg.processing) == 110.0
        # 30MB → raw=210, clamped to 120
        assert compute_vision_timeout(30 * 1024 * 1024, app_cfg.processing) == 120.0

    def test_low_confidence_threshold_default_and_validation(self) -> None:
        """#409: default + (0,1] validation surface in integration coverage."""
        assert AppConfig().processing.low_confidence_threshold == 0.5

        # Inclusive upper bound is accepted.
        assert ProcessingSettings(low_confidence_threshold=1.0).low_confidence_threshold == 1.0

        # All three out-of-range cases fire the same validator.
        with pytest.raises(ValueError, match="low_confidence_threshold"):
            ProcessingSettings(low_confidence_threshold=0.0)
        with pytest.raises(ValueError, match="low_confidence_threshold"):
            ProcessingSettings(low_confidence_threshold=-0.1)
        with pytest.raises(ValueError, match="low_confidence_threshold"):
            ProcessingSettings(low_confidence_threshold=1.5)

    def test_organizer_populates_low_confidence_files(self, tmp_path: Path) -> None:
        """#409: organizer aggregation routes low-confidence results into the review list."""
        from unittest.mock import patch

        from core.organizer import FileOrganizer
        from core.types import OrganizationResult
        from services.vision_processor import ProcessedImage

        # Build a mixed batch: one happy-path, one EXIF fallback (= 0.5),
        # one filename fallback (= 0.3), one error (= 0.0). At the
        # default threshold 0.5, the inclusive `<=` puts the EXIF
        # entry into the review list alongside the two clearly-low
        # ones.
        results: list[ProcessedImage] = [
            ProcessedImage(
                file_path=tmp_path / "ok.png",
                description="d",
                folder_name="images",
                filename="ok",
                confidence=1.0,
            ),
            ProcessedImage(
                file_path=tmp_path / "exif.jpg",
                description="",
                folder_name="Images/Photos/2025/11",
                filename="exif",
                source="fallback_exif",
                confidence=0.5,
            ),
            ProcessedImage(
                file_path=tmp_path / "name.png",
                description="",
                folder_name="Images/Screenshots/2026",
                filename="name",
                source="fallback_filename",
                confidence=0.3,
            ),
            ProcessedImage(
                file_path=tmp_path / "bad.png",
                description="",
                folder_name="errors",
                filename="bad",
                error="something broke",
                confidence=0.0,
            ),
        ]
        for r in results:
            r.file_path.write_bytes(b"")

        org = FileOrganizer(
            text_model_config=make_text_config(),
            vision_model_config=make_vision_config(),
            dry_run=True,
        )

        with (
            patch.object(
                org,
                "_categorize_files",
                return_value=([], [r.file_path for r in results], [], [], [], []),
            ),
            patch.object(org, "_process_all_file_types", return_value=results),
            patch.object(org, "_execute_organization"),
        ):
            out: OrganizationResult = org.organize(tmp_path, tmp_path / "out")

        assert set(out.low_confidence_files) == {
            "exif.jpg",
            "name.png",
            "bad.png",
        }
