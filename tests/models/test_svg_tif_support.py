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


class TestSvgSecurityHardening:
    """Adversarial inputs for the layered defences added in issue #415."""

    def test_xxe_external_entity_is_rejected(self, tmp_path: Path) -> None:
        """SVG with a file:// external entity must be rejected before fitz sees it.

        Without defusedxml the entity is either expanded into the rasterized
        PNG (data exfiltration) or silently dropped — both undesirable. The
        helper raises OSError so the organize loop skips the file.
        """
        from models._vision_helpers import rasterize_svg_to_png_bytes

        xxe_svg = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            b'<text x="10" y="50">&xxe;</text></svg>'
        )
        svg_file = tmp_path / "xxe.svg"
        svg_file.write_bytes(xxe_svg)

        with pytest.raises(OSError, match="defusedxml"):
            rasterize_svg_to_png_bytes(svg_file)

    def test_external_dtd_is_rejected(self, tmp_path: Path) -> None:
        """SVG referencing an external DTD must be rejected by defusedxml."""
        from models._vision_helpers import rasterize_svg_to_png_bytes

        dtd_svg = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE svg SYSTEM "http://attacker.example/evil.dtd">'
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
        )
        svg_file = tmp_path / "dtd.svg"
        svg_file.write_bytes(dtd_svg)

        with pytest.raises(OSError, match="defusedxml"):
            rasterize_svg_to_png_bytes(svg_file)

    def test_billion_laughs_is_rejected_in_under_one_second(self, tmp_path: Path) -> None:
        """Quadratic / billion-laughs entity expansions must bounce in < 1s."""
        import time

        from models._vision_helpers import rasterize_svg_to_png_bytes

        # Classic billion-laughs payload: each level multiplies expansion.
        bomb = (
            b'<?xml version="1.0"?>'
            b"<!DOCTYPE svg ["
            b'<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            b'<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">'
            b'<!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">'
            b"]>"
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            b"<text>&e;</text></svg>"
        )
        svg_file = tmp_path / "bomb.svg"
        svg_file.write_bytes(bomb)

        start = time.monotonic()
        with pytest.raises(OSError, match="defusedxml"):
            rasterize_svg_to_png_bytes(svg_file)
        # defusedxml refuses up-front — no expansion happens. Generous
        # ceiling tolerates slow CI runners but still catches a regression
        # that would expand the bomb.
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"defusedxml rejection took {elapsed:.2f}s"

    def test_malformed_xml_raises_oserror_not_fitz_exception(self, tmp_path: Path) -> None:
        """Truncated SVG must surface as OSError, not an uncaught fitz error."""
        from models._vision_helpers import rasterize_svg_to_png_bytes

        # Unclosed root tag — defusedxml rejects with ParseError, helper
        # converts to OSError per the security-layer contract.
        bad_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"'
        svg_file = tmp_path / "bad.svg"
        svg_file.write_bytes(bad_svg)

        with pytest.raises(OSError):
            rasterize_svg_to_png_bytes(svg_file)

    def test_oversized_svg_file_rejected_before_read(self, tmp_path: Path) -> None:
        """Files larger than ``svg_max_input_bytes`` must be rejected at stat()."""
        from models import _vision_helpers
        from models._vision_helpers import rasterize_svg_to_png_bytes

        oversized = tmp_path / "huge.svg"
        # 2 MB of garbage — well above the 1 MB cap we'll patch in.
        oversized.write_bytes(b"a" * (2 * 1024 * 1024))

        with patch.object(
            _vision_helpers, "_resolve_svg_max_input_bytes", return_value=1024 * 1024
        ):
            with pytest.raises(OSError, match="exceeds maximum input size"):
                rasterize_svg_to_png_bytes(oversized)

    def test_under_size_cap_still_rasterizes(self, tmp_path: Path) -> None:
        """Sanity: well-formed SVGs under the cap still produce PNG bytes."""
        pytest.importorskip("fitz")
        from models._vision_helpers import rasterize_svg_to_png_bytes

        svg_file = tmp_path / "small.svg"
        svg_file.write_bytes(_MINIMAL_SVG)

        result = rasterize_svg_to_png_bytes(svg_file)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"


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
