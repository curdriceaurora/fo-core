"""Tests for models._vision_helpers — focused on the SafeDir integration (issue #352 S3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from models._vision_helpers import image_to_data_url


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only")
class TestImageToDataUrlSafeDirIntegration:
    """image_to_data_url must use SafeDir on POSIX to reject symlinked images (issue #352 S3).

    The old implementation called open(image_path, 'rb') directly, with no
    symlink or path-escape check.  The fix opens via SafeDir.open_for_reader so
    a symlink swapped into the path between directory enumeration and the read
    is refused with SymlinkRejected → OSError rather than silently dereferenced.
    """

    def test_reads_real_image_file(self, tmp_path: Path) -> None:
        """Happy path: a regular image file returns a valid data URL."""
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"fake-jpeg-data")

        result = image_to_data_url(img)

        assert result.startswith("data:image/jpeg;base64,")
        assert len(result) > len("data:image/jpeg;base64,")

    def test_symlinked_image_raises_oserror(self, tmp_path: Path) -> None:
        """A symlink in place of the image must be refused with OSError.

        Without the SafeDir fix, open() would follow the symlink and return
        the target's content — potentially exfiltrating a sensitive file.
        """
        real = tmp_path / "secret.dat"
        real.write_bytes(b"TOP_SECRET_CONTENT")
        img_dir = tmp_path / "images"
        img_dir.mkdir()
        decoy = img_dir / "photo.jpg"
        try:
            decoy.symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not supported on this filesystem")

        with pytest.raises(OSError):
            image_to_data_url(decoy)

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """FileNotFoundError propagates unchanged for missing files."""
        with pytest.raises((FileNotFoundError, OSError)):
            image_to_data_url(tmp_path / "nonexistent.jpg")
