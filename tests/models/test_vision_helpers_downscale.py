"""Tests for vision helpers image downscaling functionality."""

from __future__ import annotations

from pathlib import Path

import pytest

from models._vision_helpers import downscale_image_if_needed

pytestmark = pytest.mark.unit


@pytest.fixture
def create_test_image(tmp_path: Path):
    """Factory fixture to create test images with specific dimensions."""

    def _create(width: int, height: int, name: str = "test.png") -> Path:
        """Create a test image with specified dimensions."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("PIL/Pillow not available")

        img_path = tmp_path / name
        # Create a simple RGB image
        img = Image.new("RGB", (width, height), color=(255, 0, 0))
        img.save(img_path)
        return img_path

    return _create


@pytest.mark.unit
class TestDownscaleImageIfNeeded:
    """Tests for downscale_image_if_needed function."""

    def test_no_downscaling_needed_small_image(
        self, create_test_image
    ) -> None:
        """Test that small images are not downscaled."""
        img_path = create_test_image(800, 600)

        result, was_downscaled = downscale_image_if_needed(img_path, max_long_edge=1024)

        assert was_downscaled is False
        assert result == img_path

    def test_no_downscaling_at_threshold(
        self, create_test_image
    ) -> None:
        """Test that images exactly at threshold are not downscaled."""
        img_path = create_test_image(1024, 768)

        result, was_downscaled = downscale_image_if_needed(img_path, max_long_edge=1024)

        assert was_downscaled is False
        assert result == img_path

    def test_downscaling_large_width(
        self, create_test_image
    ) -> None:
        """Test downscaling when width exceeds threshold."""
        img_path = create_test_image(4000, 3000)

        result, was_downscaled = downscale_image_if_needed(img_path, max_long_edge=1024)

        assert was_downscaled is True
        assert isinstance(result, bytes)

        # Verify the downscaled image dimensions
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(result))
            width, height = img.size
            assert width == 1024
            assert height == 768  # 3000 * (1024 / 4000) = 768
        except ImportError:
            pytest.skip("PIL/Pillow not available for verification")

    def test_downscaling_large_height(
        self, create_test_image
    ) -> None:
        """Test downscaling when height exceeds threshold."""
        img_path = create_test_image(2000, 3000)

        result, was_downscaled = downscale_image_if_needed(img_path, max_long_edge=1024)

        assert was_downscaled is True
        assert isinstance(result, bytes)

        # Verify the downscaled image dimensions
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(result))
            width, height = img.size
            assert height == 1024
            # width should be 2000 * (1024 / 3000) ≈ 682
            assert 680 <= width <= 684
        except ImportError:
            pytest.skip("PIL/Pillow not available for verification")

    def test_aspect_ratio_preserved(
        self, create_test_image
    ) -> None:
        """Test that aspect ratio is preserved during downscaling."""
        img_path = create_test_image(3024, 1964)

        result, was_downscaled = downscale_image_if_needed(img_path, max_long_edge=1024)

        assert was_downscaled is True

        try:
            from PIL import Image
            import io

            original_aspect = 3024 / 1964
            img = Image.open(io.BytesIO(result))
            new_width, new_height = img.size
            new_aspect = new_width / new_height

            # Aspect ratio should be very close (allowing for integer rounding)
            assert abs(original_aspect - new_aspect) < 0.01
        except ImportError:
            pytest.skip("PIL/Pillow not available for verification")

    def test_custom_max_long_edge(
        self, create_test_image
    ) -> None:
        """Test downscaling with custom max_long_edge parameter."""
        img_path = create_test_image(2048, 1536)

        result, was_downscaled = downscale_image_if_needed(img_path, max_long_edge=512)

        assert was_downscaled is True

        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(result))
            width, height = img.size
            assert width == 512
            assert height == 384  # 1536 * (512 / 2048) = 384
        except ImportError:
            pytest.skip("PIL/Pillow not available for verification")

    def test_nonexistent_file_returns_original_path(
        self, tmp_path: Path
    ) -> None:
        """Test that nonexistent file returns original path with warning."""
        nonexistent = tmp_path / "nonexistent.png"

        result, was_downscaled = downscale_image_if_needed(nonexistent)

        # Should return original path and not downscale when file doesn't exist
        assert was_downscaled is False
        assert result == nonexistent

    def test_image_format_preserved(
        self, create_test_image
    ) -> None:
        """Test that image format is preserved during downscaling."""
        # Create a JPEG image
        img_path = create_test_image(2000, 1500, "test.jpg")

        result, was_downscaled = downscale_image_if_needed(img_path, max_long_edge=1024)

        assert was_downscaled is True

        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(result))
            # Format should be JPEG
            assert img.format == "JPEG"
        except ImportError:
            pytest.skip("PIL/Pillow not available for verification")
