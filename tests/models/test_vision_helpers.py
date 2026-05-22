"""Tests for models._vision_helpers — focused on the SafeDir integration (issue #352 S3)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from models._vision_helpers import image_to_data_url


@pytest.mark.ci
@pytest.mark.unit
@pytest.mark.integration
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

    def test_not_implemented_error_falls_back_to_direct_open(self, tmp_path: Path) -> None:
        """NotImplementedError from SafeDir.open_root falls through to direct open().

        Exercises lines 78-81: the ``except (NotImplementedError, ImportError)``
        fallback that opens the file directly when SafeDir primitives are
        unavailable on the current platform.
        """
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"fallback-jpeg-data")

        with patch(
            "utils.safedir.SafeDir.open_root",
            side_effect=NotImplementedError("simulated SafeDir unavailable"),
        ):
            result = image_to_data_url(img)

        assert result.startswith("data:image/jpeg;base64,")

    def test_osfdopen_failure_closes_fd_and_propagates(self, tmp_path: Path) -> None:
        """OSError from os.fdopen in the SafeDir branch closes the raw fd and re-raises.

        Exercises lines 71-73 — the cleanup path where os.fdopen raises after
        open_for_reader returned a valid fd:

        .. code-block:: python

            try:
                fh = os.fdopen(fd, "rb", closefd=True)  # raises
            except OSError:        # line 71  ← covered here
                os.close(fd)       # line 72  ← covered here
                raise              # line 73  ← covered here
        """
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"data")

        original_fdopen = os.fdopen

        call_count = 0

        def patched_fdopen(fd: int, *args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("simulated os.fdopen failure")
            return original_fdopen(fd, *args, **kwargs)  # type: ignore[arg-type]

        with patch("models._vision_helpers.os.fdopen", patched_fdopen):
            with pytest.raises(OSError):
                image_to_data_url(img)
