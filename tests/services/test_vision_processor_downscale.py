"""Integration test for vision image downscaling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.ci]


@pytest.fixture
def large_test_image(tmp_path: Path) -> Path:
    """Create a large test image that should be downscaled."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL/Pillow not available")

    img_path = tmp_path / "large_image.jpg"
    # Create a 3000x2000 image (larger than default 1024 threshold)
    img = Image.new("RGB", (3000, 2000), color=(100, 150, 200))
    img.save(img_path)
    return img_path


@pytest.mark.unit
def test_vision_processor_downscales_large_image(large_test_image: Path) -> None:
    """Test that VisionProcessor downscales large images before sending to model."""
    from models.base import ModelType

    # Create a mock vision model. Force the legacy per-field path (#433) so
    # this test exercises the ``generate`` call that carries the downscale
    # parameter — the structured path is covered separately.
    from models.vision_schema import StructuredParseError
    from services.vision_processor import VisionProcessor

    mock_model = MagicMock()
    mock_model.is_initialized = True
    mock_model.config.model_type = ModelType.VISION
    mock_model.generate.return_value = "Test description"
    mock_model.generate_structured.side_effect = StructuredParseError("force legacy")

    # Create processor with default max_image_long_edge (1024)
    processor = VisionProcessor(vision_model=mock_model, max_image_long_edge=1024)

    # Process the large image
    result = processor.process_file(
        large_test_image,
        generate_description=True,
        generate_folder=False,
        generate_filename=False,
        perform_ocr=False,
    )

    # Verify the model was called
    mock_model.generate.assert_called()
    call_kwargs = mock_model.generate.call_args[1]

    # Verify max_image_long_edge was passed
    assert "max_image_long_edge" in call_kwargs
    assert call_kwargs["max_image_long_edge"] == 1024

    # Verify result is successful
    assert result.description == "Test description"
    assert result.error is None


@pytest.mark.unit
def test_vision_processor_custom_downscale_threshold() -> None:
    """Test that VisionProcessor respects custom max_image_long_edge parameter."""
    from models.base import ModelType
    from services.vision_processor import VisionProcessor

    mock_model = MagicMock()
    mock_model.is_initialized = True
    mock_model.config.model_type = ModelType.VISION

    # Create processor with custom threshold
    processor = VisionProcessor(vision_model=mock_model, max_image_long_edge=512)

    # Verify the threshold was set and clamped correctly
    assert processor._max_image_long_edge == 512


@pytest.mark.unit
def test_vision_processor_clamps_max_edge_to_valid_range() -> None:
    """Test that max_image_long_edge is clamped to valid range (256-4096)."""
    from models.base import ModelType
    from services.vision_processor import VisionProcessor

    mock_model = MagicMock()
    mock_model.is_initialized = True
    mock_model.config.model_type = ModelType.VISION

    # Test lower bound clamping
    processor_low = VisionProcessor(vision_model=mock_model, max_image_long_edge=100)
    assert processor_low._max_image_long_edge == 256

    # Test upper bound clamping
    processor_high = VisionProcessor(vision_model=mock_model, max_image_long_edge=10000)
    assert processor_high._max_image_long_edge == 4096

    # Test valid value (no clamping)
    processor_valid = VisionProcessor(vision_model=mock_model, max_image_long_edge=1024)
    assert processor_valid._max_image_long_edge == 1024
