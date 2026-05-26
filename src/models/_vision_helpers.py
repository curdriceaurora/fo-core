"""Shared image-encoding helpers for vision model implementations.

Provides base64 data-URL encoding utilities used by both
:mod:`~models.openai_vision_model` and
:mod:`~models.claude_vision_model` so that neither module
cross-imports the other.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import os
import sys
from pathlib import Path

import fitz  # PyMuPDF — core dep; also handles SVG rasterization

# defusedxml is the project-wide standard for parsing untrusted XML
# (.claude/rules/pr-comment-derived-rails.md Rail 2 — fail-closed, no
# silent stdlib fallback). For SVG we lean on:
#   - DTDForbidden              : blocks any DOCTYPE declaration
#   - EntitiesForbidden         : blocks <!ENTITY xxe SYSTEM "...">
#   - ExternalReferenceForbidden: blocks file:/http: external references
# raised during the pre-parse, before MuPDF sees the bytes. Modern SVG
# exporters do not emit a DOCTYPE, so forbidding DTDs outright trades a
# small legacy-tooling compatibility risk for a closed XXE surface.
from defusedxml import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden
from defusedxml.ElementTree import ParseError as _DefusedParseError
from defusedxml.ElementTree import fromstring as _defused_fromstring
from loguru import logger

# Fallback MIME type when extension is not recognised
_DEFAULT_IMAGE_MIME = "image/jpeg"

# Maximum pixel dimension for SVG rasterization — guards against OOM from
# SVGs with very large intrinsic width/height before downscaling is applied.
_SVG_MAX_RENDER_EDGE = 4096

# Hard cap on .svg file size, applied at read time (#415). Default of
# 5 MiB matches ``AppConfig.vision.svg_max_input_bytes`` and is enforced
# even when the config is unavailable so the helper stays safe in
# library-style call paths.
_SVG_MAX_INPUT_BYTES_DEFAULT = 5 * 1024 * 1024

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
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".svg": "image/svg+xml",
}


def _validate_svg_xml(svg_data: bytes) -> None:
    """Reject SVGs that would trigger XML-layer attacks before MuPDF sees them (#415).

    Layered defence:

    - ``defusedxml`` raises :class:`EntitiesForbidden` on
      ``<!ENTITY xxe SYSTEM "file:///etc/passwd">`` style declarations and
      :class:`ExternalReferenceForbidden` on external DTDs (billion-laughs,
      XXE, external-DTD).
    - Malformed XML surfaces as :class:`_DefusedParseError`.

    Either case is converted to :class:`OSError` so the organize pipeline
    skips the file via the standard read-error path (#411 ``read_error``
    bucket) instead of crashing.
    """
    try:
        # ``forbid_dtd=True`` makes a DOCTYPE declaration itself an error
        # (raises ``DTDForbidden``), which is what we want — modern SVG
        # exporters do not emit a DOCTYPE. Without this flag a payload
        # like ``<!DOCTYPE svg SYSTEM "http://...">`` parses silently as
        # long as no entity is expanded, because the default parser only
        # forbids entity / external-reference activation.
        _defused_fromstring(svg_data, forbid_dtd=True)
    except (DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as exc:
        raise OSError(f"SVG rejected by defusedxml: {exc}") from exc
    except _DefusedParseError as exc:
        # Malformed XML — let the caller skip the file with a stable
        # error message instead of letting a fitz.FileDataError escape.
        raise OSError(f"SVG XML parse error: {exc}") from exc


def _rasterize_svg_bytes_to_png(svg_data: bytes) -> bytes:
    """Rasterize SVG bytes to PNG bytes via PyMuPDF, without touching the filesystem.

    Layered defences (#415):

    1. ``_validate_svg_xml`` runs first — billion-laughs / XXE / external-DTD
       SVGs are rejected before MuPDF allocates anything.
    2. Render size is capped at ``_SVG_MAX_RENDER_EDGE`` on the longest side
       so an SVG with an enormous intrinsic width/height cannot exhaust
       memory before the caller's downscale step runs.

    Raises:
        OSError: For XML-layer attacks (XXE, billion-laughs, malformed XML)
            and any :class:`fitz.FileDataError` that survives the pre-parse.
    """
    _validate_svg_xml(svg_data)
    try:
        doc = fitz.open(stream=svg_data, filetype="svg")
    except fitz.FileDataError as exc:
        # Truncated / structurally broken SVG that passed the XML check
        # but tripped MuPDF's parser. Normalise to OSError so the organize
        # loop skips the file via the standard read-error path.
        raise OSError(f"SVG could not be opened by fitz: {exc}") from exc
    try:
        page = doc[0]
        w, h = page.rect.width, page.rect.height
        scale = min(1.0, _SVG_MAX_RENDER_EDGE / max(w, h, 1))
        mat = fitz.Matrix(scale, scale) if scale < 1.0 else fitz.Identity
        pix = page.get_pixmap(matrix=mat)
        return bytes(pix.tobytes("png"))
    finally:
        doc.close()


def _read_image_bytes_safedir(image_path: Path) -> bytes:
    """Read image file bytes via SafeDir on POSIX, direct open on Windows.

    Refuses symlinks on POSIX (issue #352 S3).
    """
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
                    return fh.read()
        except (NotImplementedError, ImportError):
            with open(
                image_path, "rb"
            ) as fh_direct:  # safedir: ok — Windows / NotImplementedError fallback
                return fh_direct.read()
        except (SymlinkRejected, ValueError) as exc:
            raise OSError(f"Refused to read symlinked image {image_path}: {exc}") from exc
    else:  # pragma: no cover — Windows-only branch, not reachable on POSIX CI
        with open(
            image_path, "rb"
        ) as fh_win:  # safedir: ok — Windows / NotImplementedError fallback
            return fh_win.read()


def _resolve_svg_max_input_bytes() -> int:
    """Return the configured ``svg_max_input_bytes`` or the safe default.

    ``ConfigManager().load()`` is best-effort: any failure (file missing,
    parse error, schema drift) degrades silently to
    ``_SVG_MAX_INPUT_BYTES_DEFAULT`` so the size precheck is always armed,
    even when called outside the organize pipeline.
    """
    try:
        from config.manager import ConfigManager

        return int(ConfigManager().load().vision.svg_max_input_bytes)
    except Exception:  # pragma: no cover — defensive degrade path
        return _SVG_MAX_INPUT_BYTES_DEFAULT


def rasterize_svg_to_png_bytes(svg_path: Path) -> bytes:
    """Rasterize an SVG file to PNG bytes using PyMuPDF (fitz).

    PyMuPDF is a core dependency (used for PDF extraction), so no additional
    packages are required. The file is read via SafeDir on POSIX to prevent
    symlink attacks (issue #352 S3).

    Security layering (#415):

    1. **File-size precheck.** Files larger than
       ``AppConfig.vision.svg_max_input_bytes`` (default 5 MiB) are
       rejected before any bytes are read into memory.
    2. **defusedxml pre-parse.** ``_validate_svg_xml`` runs before fitz
       sees the bytes; it rejects external entities (XXE), external DTDs,
       billion-laughs expansions, and malformed XML.
    3. **Render-edge cap.** Output canvas is clamped to
       ``_SVG_MAX_RENDER_EDGE`` so a huge intrinsic width/height cannot
       OOM the process.

    Args:
        svg_path: Path to the .svg file.

    Returns:
        PNG image as raw bytes.

    Raises:
        OSError: If the SVG exceeds the size cap, is rejected by the XML
            pre-parse, cannot be opened or rendered, or if *svg_path* is a
            symlink (POSIX only).
    """
    try:
        size = svg_path.stat().st_size
    except OSError as exc:
        # Surface as OSError so the caller skips the file consistently
        # (e.g. broken symlinks reach here before SafeDir does).
        raise OSError(f"Could not stat SVG {svg_path}: {exc}") from exc
    max_bytes = _resolve_svg_max_input_bytes()
    if size > max_bytes:
        raise OSError(f"SVG exceeds maximum input size ({size} > {max_bytes} bytes): {svg_path}")
    svg_data = _read_image_bytes_safedir(svg_path)
    return _rasterize_svg_bytes_to_png(svg_data)


def downscale_image_if_needed(
    image_path: Path, max_long_edge: int = 1024
) -> tuple[Path | bytes, bool]:
    """Downscale an image if it exceeds the maximum dimension.

    Large images are resized to fit within max_long_edge on the longest side,
    preserving aspect ratio. This reduces inference time for vision models
    without significantly impacting quality for high-level tasks like
    categorization and filename generation.

    Args:
        image_path: Path to the image file.
        max_long_edge: Maximum length of the longest edge in pixels.
            Images with either dimension exceeding this are downscaled.

    Returns:
        A tuple of (image_data, was_converted) where:
        - image_data is either the original Path (if no conversion) or
          bytes of the processed image
        - was_converted is True if the image was resized **or** converted
          to PNG (SVG files are always rasterized to PNG bytes, even when
          no downscaling is needed)

    Raises:
        ImportError: If PIL/Pillow is not available.
        OSError: If the image cannot be opened or processed.
    """
    # SVG cannot be opened by Pillow — rasterize via fitz first, then downscale the PNG
    if image_path.suffix.lower() == ".svg":
        png_bytes = rasterize_svg_to_png_bytes(image_path)
        try:
            from PIL import Image

            with Image.open(io.BytesIO(png_bytes)) as img:
                width, height = img.size
                if max(width, height) <= max_long_edge:
                    return png_bytes, True
                if width > height:
                    new_width = max_long_edge
                    new_height = int(height * (max_long_edge / width))
                else:
                    new_height = max_long_edge
                    new_width = int(width * (max_long_edge / height))
                logger.debug(
                    "Downscaling rasterized SVG {} from {}×{} → {}×{} before vision inference",
                    image_path.name,
                    width,
                    height,
                    new_width,
                    new_height,
                )
                resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                resized.save(buf, format="PNG")
                buf.seek(0)
                return buf.getvalue(), True
        except Exception as exc:
            logger.warning(
                "Failed to downscale rasterized SVG {}: {}; using rasterized PNG",
                image_path.name,
                exc,
            )
            return png_bytes, True

    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "PIL/Pillow is required for image downscaling. Install with: pip install Pillow"
        ) from exc

    try:
        with Image.open(image_path) as img:
            width, height = img.size
            max_dim = max(width, height)

            # No downscaling needed
            if max_dim <= max_long_edge:
                return image_path, False

            # Calculate new dimensions preserving aspect ratio
            if width > height:
                new_width = max_long_edge
                new_height = int(height * (max_long_edge / width))
            else:
                new_height = max_long_edge
                new_width = int(width * (max_long_edge / height))

            logger.debug(
                "Downscaling {} from {}×{} → {}×{} before vision inference",
                image_path.name,
                width,
                height,
                new_width,
                new_height,
            )

            # Resize using LANCZOS for best quality
            resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Save to bytes buffer in original format
            buffer = io.BytesIO()
            # Preserve original format; default to JPEG if unknown
            format_str = img.format or "JPEG"
            resized.save(buffer, format=format_str)
            buffer.seek(0)

            return buffer.getvalue(), True

    except Exception as exc:
        logger.warning(
            "Failed to downscale image {}: {}; using original",
            image_path.name,
            exc,
        )
        return image_path, False


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

    # SVG: read via SafeDir then rasterize to PNG via fitz
    if ext == ".svg":
        png_bytes = rasterize_svg_to_png_bytes(image_path)
        return bytes_to_data_url(png_bytes, "image/png")

    mime_type = _EXTENSION_MIME.get(ext)
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(str(image_path))
    if not mime_type:
        mime_type = _DEFAULT_IMAGE_MIME

    raw = _read_image_bytes_safedir(image_path)
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
