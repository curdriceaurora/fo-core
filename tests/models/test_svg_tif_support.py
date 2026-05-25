"""Tests for .svg and .tif image format support (issue #402).

Covers:
- Extension registration in IMAGE_EXTENSIONS and pipeline frozensets
- SVG rasterization via fitz
- downscale_image_if_needed handling of SVG
- image_to_data_url handling of SVG
- .tif routed as IMAGE by the pipeline router
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.ci]

_MINIMAL_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="red"/></svg>'
_WIDE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="blue"/></svg>'


class TestExtensionRegistration:
    """Both .tif and .svg must appear in every canonical extension set."""

    def test_tif_in_image_extensions(self) -> None:
        from core.types import IMAGE_EXTENSIONS

        assert ".tif" in IMAGE_EXTENSIONS

    def test_svg_in_image_extensions(self) -> None:
        from core.types import IMAGE_EXTENSIONS

        assert ".svg" in IMAGE_EXTENSIONS

    def test_tif_in_pipeline_default_extensions(self) -> None:
        from pipeline.config import DEFAULT_SUPPORTED_EXTENSIONS

        assert ".tif" in DEFAULT_SUPPORTED_EXTENSIONS

    def test_svg_in_pipeline_default_extensions(self) -> None:
        from pipeline.config import DEFAULT_SUPPORTED_EXTENSIONS

        assert ".svg" in DEFAULT_SUPPORTED_EXTENSIONS

    def test_tif_routes_to_image_processor(self) -> None:
        from pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("scan.tif")) == ProcessorType.IMAGE

    def test_svg_routes_to_image_processor(self) -> None:
        from pipeline.router import FileRouter, ProcessorType

        router = FileRouter()
        assert router.route(Path("diagram.svg")) == ProcessorType.IMAGE


class TestSvgMimeEntry:
    """SVG must have the correct MIME type in the extension map."""

    def test_svg_mime_type(self) -> None:
        from models._vision_helpers import _EXTENSION_MIME

        assert _EXTENSION_MIME[".svg"] == "image/svg+xml"

    def test_tif_mime_type(self) -> None:
        from models._vision_helpers import _EXTENSION_MIME

        assert _EXTENSION_MIME[".tif"] == "image/tiff"


class TestRasterizeSvgToPngBytes:
    """rasterize_svg_to_png_bytes produces valid PNG bytes from an SVG file."""

    def test_rasterizes_svg_to_png(self, tmp_path: Path) -> None:
        pytest.importorskip("fitz")
        from models._vision_helpers import rasterize_svg_to_png_bytes

        svg_file = tmp_path / "shape.svg"
        svg_file.write_bytes(_MINIMAL_SVG)

        result = rasterize_svg_to_png_bytes(svg_file)

        assert isinstance(result, bytes)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic header

    def test_result_is_non_empty(self, tmp_path: Path) -> None:
        pytest.importorskip("fitz")
        from models._vision_helpers import rasterize_svg_to_png_bytes

        svg_file = tmp_path / "shape.svg"
        svg_file.write_bytes(_MINIMAL_SVG)

        result = rasterize_svg_to_png_bytes(svg_file)

        assert len(result) > 100

    def test_oversized_svg_clamped_to_max_render_edge(self, tmp_path: Path) -> None:
        """SVGs with huge intrinsic dimensions are clamped before rasterization."""
        pytest.importorskip("fitz")
        import struct

        from models._vision_helpers import _SVG_MAX_RENDER_EDGE, rasterize_svg_to_png_bytes

        oversized_svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10000" height="5000">'
            b'<rect width="10000" height="5000" fill="green"/></svg>'
        )
        svg_file = tmp_path / "oversized.svg"
        svg_file.write_bytes(oversized_svg)

        result = rasterize_svg_to_png_bytes(svg_file)

        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        # PNG IHDR chunk starts at byte 16: 4-byte width then 4-byte height
        width = struct.unpack(">I", result[16:20])[0]
        height = struct.unpack(">I", result[20:24])[0]
        assert max(width, height) <= _SVG_MAX_RENDER_EDGE


class TestDownscaleHandlesSvg:
    """downscale_image_if_needed must rasterize SVG and return bytes."""

    def test_svg_returns_bytes_not_path(self, tmp_path: Path) -> None:
        pytest.importorskip("fitz")
        pytest.importorskip("PIL")
        from models._vision_helpers import downscale_image_if_needed

        svg_file = tmp_path / "shape.svg"
        svg_file.write_bytes(_MINIMAL_SVG)

        result, was_converted = downscale_image_if_needed(svg_file)

        assert isinstance(result, bytes)
        assert was_converted is True

    def test_svg_result_is_png(self, tmp_path: Path) -> None:
        pytest.importorskip("fitz")
        pytest.importorskip("PIL")
        from models._vision_helpers import downscale_image_if_needed

        svg_file = tmp_path / "shape.svg"
        svg_file.write_bytes(_MINIMAL_SVG)

        result, _ = downscale_image_if_needed(svg_file)

        assert isinstance(result, bytes)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_svg_downscales_wide_image(self, tmp_path: Path) -> None:
        """Wide SVG (width > height) exercises the if-branch in the downscale path."""
        pytest.importorskip("fitz")
        pytest.importorskip("PIL")
        from models._vision_helpers import downscale_image_if_needed

        svg_file = tmp_path / "wide.svg"
        svg_file.write_bytes(_WIDE_SVG)
        # max_long_edge=50 forces downscale since SVG rasterizes to ~200x100
        result, was_converted = downscale_image_if_needed(svg_file, max_long_edge=50)

        assert isinstance(result, bytes)
        assert was_converted is True
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_svg_downscales_square_image(self, tmp_path: Path) -> None:
        """Square SVG (width == height) exercises the else-branch in the downscale path."""
        pytest.importorskip("fitz")
        pytest.importorskip("PIL")
        from models._vision_helpers import downscale_image_if_needed

        svg_file = tmp_path / "square.svg"
        svg_file.write_bytes(_MINIMAL_SVG)
        # max_long_edge=50 forces downscale since SVG rasterizes to 100x100
        result, was_converted = downscale_image_if_needed(svg_file, max_long_edge=50)

        assert isinstance(result, bytes)
        assert was_converted is True
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_svg_fallback_on_pil_failure(self, tmp_path: Path) -> None:
        """If Pillow downscale step fails, raw rasterized PNG is still returned."""
        pytest.importorskip("fitz")
        pytest.importorskip("PIL")
        from models._vision_helpers import downscale_image_if_needed

        svg_file = tmp_path / "shape.svg"
        svg_file.write_bytes(_MINIMAL_SVG)

        with patch("models._vision_helpers.rasterize_svg_to_png_bytes") as mock_rast:
            fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
            mock_rast.return_value = fake_png
            with patch("PIL.Image.open", side_effect=OSError("simulated PIL failure")):
                result, was_converted = downscale_image_if_needed(svg_file)

        assert result == fake_png
        assert was_converted is True


class TestImageToDataUrlHandlesSvg:
    """image_to_data_url must return a PNG data URL for .svg inputs."""

    def test_svg_produces_png_data_url(self, tmp_path: Path) -> None:
        pytest.importorskip("fitz")
        from models._vision_helpers import image_to_data_url

        svg_file = tmp_path / "shape.svg"
        svg_file.write_bytes(_MINIMAL_SVG)

        result = image_to_data_url(svg_file)

        assert result.startswith("data:image/png;base64,")

    def test_svg_data_url_is_non_empty(self, tmp_path: Path) -> None:
        pytest.importorskip("fitz")
        from models._vision_helpers import image_to_data_url

        svg_file = tmp_path / "shape.svg"
        svg_file.write_bytes(_MINIMAL_SVG)

        result = image_to_data_url(svg_file)

        assert len(result) > len("data:image/png;base64,")

    def test_svg_uses_safedir_for_file_read(self, tmp_path: Path) -> None:
        """SVG file read routes through SafeDir before rasterization (issue #402 S3 fix)."""
        pytest.importorskip("fitz")
        from models._vision_helpers import image_to_data_url

        svg_file = tmp_path / "shape.svg"
        svg_file.write_bytes(_MINIMAL_SVG)

        result = image_to_data_url(svg_file)

        assert result.startswith("data:image/png;base64,")
