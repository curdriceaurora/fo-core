"""Shared image-encoding helpers for vision model implementations.

Provides base64 data-URL encoding utilities used by both
:mod:`~models.openai_vision_model` and
:mod:`~models.claude_vision_model` so that neither module
cross-imports the other.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import sys
from pathlib import Path

# Fallback MIME type when extension is not recognised
_DEFAULT_IMAGE_MIME = "image/jpeg"

# Hardcoded map for common image extensions — more portable than mimetypes.guess_type
# on Windows, where the registry may not have entries for modern formats (e.g. webp)
# and mimetypes.init() calls can clobber mimetypes.add_type registrations.
_EXTENSION_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def image_to_data_url(image_path: Path) -> str:
    """Encode an image file as a base64 data URL.

    Opens the file via ``SafeDir`` on POSIX so a symlink swapped into
    the path between directory enumeration and this read is refused with
    ``SymlinkRejected`` (raised as ``OSError``) rather than dereferenced
    (issue #352 S3).  On Windows or when SafeDir is unavailable the
    direct ``open()`` fallback is used.

    Args:
        image_path: Path to the image file.

    Returns:
        Data URL string in the form ``data:<mime>;base64,<data>``.

    Raises:
        FileNotFoundError: If the file does not exist.
        OSError: If the file cannot be read, or if *image_path* is a
            symlink (POSIX only).
    """
    ext = image_path.suffix.lower()
    mime_type = _EXTENSION_MIME.get(ext)
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = _DEFAULT_IMAGE_MIME

    raw: bytes
    if sys.platform != "win32":
        try:
            from utils.safedir import SafeDir, SymlinkRejected

            with SafeDir.open_root(image_path.parent) as sd:
                fd = sd.open_for_reader(image_path.name)
                try:
                    fh = os.fdopen(fd, "rb", closefd=True)
                except OSError:
                    os.close(fd)
                    raise
                with fh:
                    raw = fh.read()
        except (NotImplementedError, ImportError):
            # SafeDir primitives unavailable — fall through to direct open.
            with open(
                image_path, "rb"
            ) as fh_direct:  # safedir: ok — Windows / NotImplementedError fallback
                raw = fh_direct.read()
        except (SymlinkRejected, ValueError) as exc:
            raise OSError(f"Refused to read symlinked image {image_path}: {exc}") from exc
    else:  # pragma: no cover — Windows-only branch, not reachable on POSIX CI
        with open(
            image_path, "rb"
        ) as fh_win:  # safedir: ok — Windows / NotImplementedError fallback
            raw = fh_win.read()

    encoded = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def bytes_to_data_url(image_data: bytes, mime_type: str = _DEFAULT_IMAGE_MIME) -> str:
    """Encode raw image bytes as a base64 data URL.

    Args:
        image_data: Raw image bytes.
        mime_type: MIME type of the image data.

    Returns:
        Data URL string in the form ``data:<mime>;base64,<data>``.
    """
    encoded = base64.b64encode(image_data).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def split_data_url(data_url: str) -> tuple[str, str]:
    """Split a base64 data URL into ``(mime_type, base64_data)``.

    Args:
        data_url: Data URL in the form ``data:<mime>;base64,<data>``.

    Returns:
        A ``(mime_type, base64_data)`` tuple.

    Raises:
        ValueError: If *data_url* is not a valid ``data:…;base64,…`` URL.
    """
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise ValueError(f"Not a valid base64 data URL: {data_url[:60]!r}")
    # Strip the "data:" prefix
    rest = data_url[len("data:") :]
    mime_type, b64_data = rest.split(";base64,", 1)
    if not mime_type:
        mime_type = _DEFAULT_IMAGE_MIME
    return mime_type, b64_data
