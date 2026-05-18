"""Tests for the SafeDir-aware ``safedir_image_open`` helper and its
integration with the image-dedup utilities.

PR3f of #267 wires ``utils.safedir.SafeDir`` into the image dedup
ingestion path. Every ``PIL.Image.open(...)`` call in
``services/deduplication/{image_utils,image_dedup,viewer}.py`` now goes
through ``safedir_image_open`` so a symlink swapped into the organize
root between the directory walk and the read is refused with
``SymlinkRejected`` rather than dereferenced.

Verifies:

- ``safedir_image_open`` opens a real image and yields a PIL.Image
- Symlinks under the SafeDir root are refused
- On Windows (``NotImplementedError``) the helper falls back to direct
  ``Image.open(path)``
- The downstream utility functions
  (``get_image_metadata`` / ``get_image_dimensions`` / etc.) route
  symlinks to the same safe-default returns as other I/O errors
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image as _PILImage

from services.deduplication.image_utils import (
    get_image_dimensions,
    get_image_format,
    get_image_metadata,
    safedir_image_open,
    validate_image_file,
)
from utils.safedir import SymlinkRejected

pytestmark = [
    pytest.mark.ci,
    pytest.mark.unit,
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only"),
]


def _make_jpeg(path: Path, size: tuple[int, int] = (8, 8)) -> None:
    """Write a tiny JPEG to ``path`` for round-trip tests."""
    img = _PILImage.new("RGB", size, color=(100, 150, 200))
    img.save(path, format="JPEG")


class TestSafedirImageOpenHelper:
    def test_opens_real_image(self, tmp_path: Path) -> None:
        target = tmp_path / "photo.jpg"
        _make_jpeg(target, size=(16, 8))
        with safedir_image_open(target) as (img, _fd):
            assert img.size == (16, 8)
            assert img.format == "JPEG"

    def test_refuses_symlinked_image(self, tmp_path: Path) -> None:
        """A symlinked image in the organize root must be refused —
        ``SafeDir.open_for_reader`` raises ``SymlinkRejected``, which
        is an ``OSError`` subclass.
        """
        real = tmp_path / "secret.jpg"
        _make_jpeg(real)
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "decoy.jpg").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        with pytest.raises(SymlinkRejected):
            with safedir_image_open(organize / "decoy.jpg"):
                pass

    def test_falls_back_to_path_open_when_safedir_not_implemented(self, tmp_path: Path) -> None:
        """When SafeDir.open_root raises NotImplementedError (Windows-style
        port not available), the helper falls back to direct
        ``Image.open(path)`` — preserves the legacy API surface.
        """
        target = tmp_path / "fallback.jpg"
        _make_jpeg(target)
        with patch(
            "services.deduplication.image_utils.SafeDir.open_root",
            side_effect=NotImplementedError("simulated unavailable SafeDir"),
        ):
            with safedir_image_open(target) as (img, _fd):
                assert img.size == (8, 8)

    def test_closes_fd_when_fdopen_raises(self, tmp_path: Path) -> None:
        """If ``os.fdopen`` raises (e.g. transient OSError on a closed
        dir_fd race), the helper must close the raw fd returned by
        ``open_for_reader`` before the exception propagates — otherwise
        we leak a descriptor. Verified via patch on ``os.fdopen``.

        ``os.close`` is patched with ``side_effect=os.close`` (i.e. the
        real ``os.close``) so the leak guard's call AND SafeDir's own
        dir_fd cleanup actually close their fds — patching with a bare
        ``MagicMock`` would just record the calls and leak the fds
        during the test run (Copilot #281 review).
        """
        target = tmp_path / "fd_leak_test.jpg"
        _make_jpeg(target)
        real_os_close = os.close  # capture before patching
        with patch(
            "services.deduplication.image_utils.os.fdopen",
            side_effect=OSError("synthetic fdopen failure"),
        ) as mock_fdopen:
            with patch(
                "services.deduplication.image_utils.os.close",
                side_effect=real_os_close,
            ) as mock_close:
                with pytest.raises(OSError, match="synthetic fdopen failure"):
                    with safedir_image_open(target):
                        pass
                # The raw fd from SafeDir.open_for_reader was reclaimed —
                # SafeDir.__exit__ also closes its own dir_fd, so we get
                # ≥1 close call. The fd that ``os.fdopen`` tried to wrap
                # must be among the closed fds.
                assert mock_close.call_count >= 1
                fdopen_fd = mock_fdopen.call_args[0][0]
                assert isinstance(fdopen_fd, int) and fdopen_fd >= 0
                closed_fds = {call.args[0] for call in mock_close.call_args_list}
                assert fdopen_fd in closed_fds


class TestImageUtilsRoutesThroughHelper:
    """The migrated image_utils functions return the legacy safe-default
    ``None`` when the underlying image is unreadable. Symlink rejection is
    just another OSError and routes the same way."""

    def test_get_image_metadata_returns_none_on_symlink(self, tmp_path: Path) -> None:
        real = tmp_path / "real.jpg"
        _make_jpeg(real)
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "link.jpg").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        out = get_image_metadata(organize / "link.jpg")
        assert out is None

    def test_get_image_dimensions_returns_none_on_symlink(self, tmp_path: Path) -> None:
        real = tmp_path / "real.jpg"
        _make_jpeg(real)
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "link.jpg").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        assert get_image_dimensions(organize / "link.jpg") is None

    def test_get_image_format_returns_none_on_symlink(self, tmp_path: Path) -> None:
        real = tmp_path / "real.jpg"
        _make_jpeg(real)
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "link.jpg").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        assert get_image_format(organize / "link.jpg") is None

    def test_validate_image_file_rejects_symlink(self, tmp_path: Path) -> None:
        """``validate_image_file`` exists-checks first (the symlink itself
        exists), then tries to open via SafeDir — which refuses. The
        function returns ``(False, "...")``.
        """
        real = tmp_path / "real.jpg"
        _make_jpeg(real)
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "link.jpg").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        is_valid, err = validate_image_file(organize / "link.jpg")
        assert is_valid is False
        assert err is not None

    def test_get_image_metadata_happy_path(self, tmp_path: Path) -> None:
        target = tmp_path / "p.jpg"
        _make_jpeg(target, size=(20, 10))
        meta = get_image_metadata(target)
        assert meta is not None
        assert meta.width == 20
        assert meta.height == 10
        assert meta.format == "JPEG"
        assert meta.size_bytes > 0


class TestImageDedupValidateRefusesSymlinks:
    """The ``ImageDeduplicator.validate_image`` flow now goes through
    ``safedir_image_open`` — symlinks return ``(False, "Cannot read
    image: ...")``."""

    def test_validate_refuses_symlinked_image(self, tmp_path: Path) -> None:
        pytest.importorskip("imagededup")  # dedup-image extra not in [dev,search]
        from services.deduplication.image_dedup import ImageDeduplicator

        real = tmp_path / "real.jpg"
        _make_jpeg(real)
        organize = tmp_path / "organize"
        organize.mkdir()
        try:
            (organize / "decoy.jpg").symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        dedup = ImageDeduplicator()
        is_valid, err = dedup.validate_image(organize / "decoy.jpg")
        assert is_valid is False
        assert err is not None
        assert "Cannot read image" in err or "symlink" in err.lower()
