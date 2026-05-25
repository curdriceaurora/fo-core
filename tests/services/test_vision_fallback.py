"""Tests for ``services.vision_fallback`` (#406 — degraded categorization).

Covers the three resolution paths:
  1. EXIF DateTime / DateTimeOriginal → ``Images/Photos/YYYY/MM/``
  2. Filename pattern (Screenshot / IMG_YYYYMMDD)
  3. Generic ``Images/Untagged/`` bucket
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.vision_fallback import FallbackResult, compute_fallback

pytestmark = [pytest.mark.unit, pytest.mark.ci]


class TestComputeFallbackFilenamePatterns:
    """When EXIF is unavailable, filename heuristics drive the placement."""

    @patch("services.vision_fallback._from_exif", return_value=None)
    def test_screenshot_pattern(self, _mock_exif, tmp_path: Path) -> None:
        img = tmp_path / "Screenshot 2026-05-22 at 14.03.07.png"
        img.write_bytes(b"")

        result = compute_fallback(img)

        assert isinstance(result, FallbackResult)
        assert result.folder == "Images/Screenshots/2026"
        assert result.filename == img.stem
        assert result.source == "fallback_filename"

    @patch("services.vision_fallback._from_exif", return_value=None)
    def test_img_datestamp_pattern(self, _mock_exif, tmp_path: Path) -> None:
        img = tmp_path / "IMG_20260522_140307.jpg"
        img.write_bytes(b"")

        result = compute_fallback(img)

        assert result.folder == "Images/Photos/2026/05"
        assert result.source == "fallback_filename"

    @patch("services.vision_fallback._from_exif", return_value=None)
    def test_pxl_datestamp_pattern(self, _mock_exif, tmp_path: Path) -> None:
        # Google Pixel camera prefix
        img = tmp_path / "PXL_20260601_093015.jpg"
        img.write_bytes(b"")

        result = compute_fallback(img)

        assert result.folder == "Images/Photos/2026/06"
        assert result.source == "fallback_filename"

    @patch("services.vision_fallback._from_exif", return_value=None)
    def test_unknown_filename_falls_to_untagged(self, _mock_exif, tmp_path: Path) -> None:
        img = tmp_path / "random_photo_thing.png"
        img.write_bytes(b"")

        result = compute_fallback(img)

        assert result.folder == "Images/Untagged"
        assert result.filename == "random_photo_thing"
        assert result.source == "fallback_filename"


class TestComputeFallbackExif:
    """When EXIF carries a DateTime, it takes priority over filename heuristics."""

    @patch("services.vision_fallback._from_exif")
    @patch("services.vision_fallback._from_filename", return_value=None)
    def test_exif_datetime_wins(self, _mock_fname, mock_exif, tmp_path: Path) -> None:
        mock_exif.return_value = FallbackResult(
            folder="Images/Photos/2025/11",
            filename="DSC_0042",
            source="fallback_exif",
        )

        img = tmp_path / "DSC_0042.jpg"
        img.write_bytes(b"")

        result = compute_fallback(img)
        assert result.source == "fallback_exif"
        assert result.folder == "Images/Photos/2025/11"

    @patch("services.vision_fallback._from_exif")
    def test_exif_overrides_filename_screenshot(self, mock_exif, tmp_path: Path) -> None:
        # A "Screenshot YYYY-MM-DD" filename has a filename match, but EXIF wins.
        mock_exif.return_value = FallbackResult(
            folder="Images/Photos/2024/01",
            filename="Screenshot 2026-05-22",
            source="fallback_exif",
        )
        img = tmp_path / "Screenshot 2026-05-22 at 14.03.07.png"
        img.write_bytes(b"")

        result = compute_fallback(img)
        assert result.source == "fallback_exif"
        assert result.folder == "Images/Photos/2024/01"

    def test_pillow_unavailable_degrades_to_filename(self, tmp_path: Path) -> None:
        """An ImportError on Pillow does not break the fallback path."""
        img = tmp_path / "Screenshot 2026-05-22 at 14.03.07.png"
        img.write_bytes(b"")

        # The real _from_exif catches ImportError internally and returns None,
        # so EXIF resolution silently degrades to filename matching.
        with patch.dict("sys.modules", {"PIL": None}):
            result = compute_fallback(img)

        # Filename path still works
        assert result.source == "fallback_filename"
        assert result.folder == "Images/Screenshots/2026"


class TestDispatcherTimeoutFallbackIntegration:
    """The dispatcher's timeout branch routes through compute_fallback (#406)."""

    def test_timed_out_image_becomes_fallback_not_failure(self, tmp_path: Path) -> None:
        """Vision timeout → ProcessedImage with source=fallback_*, no error."""
        from unittest.mock import MagicMock

        from core.dispatcher import process_image_files
        from parallel.processor import FileResult

        img = tmp_path / "Screenshot 2026-05-22 at 09.00.00.png"
        img.write_bytes(b"")

        # parallel_processor.process_batch_iter yields a single timed-out FileResult
        timed_out = FileResult(
            path=img,
            success=False,
            error="Timed out after 300.0s",
            non_retryable=True,
        )
        mock_parallel = MagicMock()
        mock_parallel.process_batch_iter.return_value = iter([timed_out])

        mock_vision = MagicMock()
        mock_console = MagicMock()

        results = process_image_files([img], mock_vision, mock_parallel, mock_console)

        assert len(results) == 1
        result = results[0]
        # Fallback placement, no error, source marker present
        assert result.error is None or result.error == ""
        assert result.folder_name == "Images/Screenshots/2026"
        assert result.source == "fallback_filename"

    def test_non_timeout_error_still_takes_failure_path(self, tmp_path: Path) -> None:
        """A non-timeout failure (e.g. read error) is NOT rerouted to fallback."""
        from unittest.mock import MagicMock

        from core.dispatcher import ERROR_FALLBACK_FOLDER, process_image_files
        from parallel.processor import FileResult

        img = tmp_path / "broken.png"
        img.write_bytes(b"")

        read_error = FileResult(
            path=img,
            success=False,
            error="PermissionError: [Errno 13] Permission denied",
        )
        mock_parallel = MagicMock()
        mock_parallel.process_batch_iter.return_value = iter([read_error])
        mock_vision = MagicMock()
        mock_console = MagicMock()

        results = process_image_files([img], mock_vision, mock_parallel, mock_console)

        assert len(results) == 1
        result = results[0]
        # Real failure: error preserved, folder is the error bucket
        assert result.error is not None
        assert "Permission denied" in result.error
        assert result.folder_name == ERROR_FALLBACK_FOLDER
        # Default source unchanged
        assert result.source == "vision"
