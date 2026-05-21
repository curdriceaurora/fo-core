"""Shared image-encoding helpers for vision model implementations.

Provides base64 data-URL encoding utilities used by both
:mod:`~models.openai_vision_model` and
:mod:`~models.claude_vision_model` so that neither module
cross-imports the other.
"""

from __future__ import annotations

import base64
import mimetypes
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

    Args:
        image_path: Path to the image file.

    Returns:
        Data URL string in the form ``data:<mime>;base64,<data>``.

    Raises:
        FileNotFoundError: If the file does not exist.
        OSError: If the file cannot be read.
    """
    ext = image_path.suffix.lower()
    mime_type = _EXTENSION_MIME.get(ext)
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = _DEFAULT_IMAGE_MIME
    with open(image_path, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("utf-8")
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
