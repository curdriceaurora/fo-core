"""Schema, prompt, and parser for single-call structured vision output (#433).

Stdlib-only by design: this module must never import `models.base` (or anything
that does), so `base.py` can import from here one-way without a cycle.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


class StructuredParseError(Exception):
    """The model returned unparsable or incomplete structured output.

    Raised ONLY for a bad/incomplete JSON payload from a structured vision
    call. Backend/runtime errors (connection failures, "model shutting down",
    timeouts) are never wrapped in this — they propagate so the caller's
    circuit breaker handles them.
    """


@dataclass
class VisionStructuredResult:
    """Combined per-image vision result produced in a single model call."""

    description: str = ""
    extracted_text: str = ""
    folder_name: str = ""
    filename: str = ""


# Field name -> human-readable guidance injected into the prompt.
VISION_FIELD_SPECS: dict[str, str] = {
    "description": "A 100-150 word description of the image content.",
    "extracted_text": "All text visible in the image, verbatim; empty string if none.",
    "folder_name": "A 1-2 word lowercase_with_underscores category (meaningful nouns).",
    "filename": "A 2-3 word lowercase_with_underscores name, no extension (meaningful nouns).",
}


def build_vision_json_schema(fields: list[str]) -> dict[str, object]:
    """Build a JSON schema (for Ollama ``format=``) requesting only ``fields``."""
    return {
        "type": "object",
        "properties": {f: {"type": "string"} for f in fields},
        "required": list(fields),
        "additionalProperties": False,
    }


def build_vision_json_prompt(fields: list[str], *, strict: bool = False) -> str:
    """Build the combined prompt requesting ``fields`` as one JSON object.

    When ``strict`` is True (the retry path), a hard "JSON only" preamble is
    prepended. The text-priority instruction is included only when a naming
    field (folder_name/filename) is requested.
    """
    lines = [
        "Analyze this image and respond with a single JSON object containing exactly these keys:"
    ]
    for field in fields:
        lines.append(f'- "{field}": {VISION_FIELD_SPECS[field]}')
    if "folder_name" in fields or "filename" in fields:
        lines.append(
            "If the image contains significant visible text, prioritize that text when "
            "choosing folder_name and filename; otherwise base them on the visual content."
        )
    body = "\n".join(lines)
    if strict:
        return (
            "Return ONLY one valid JSON object and nothing else — no prose, no markdown, "
            "no code fences.\n" + body
        )
    return body


def parse_structured_json(raw: str, fields: list[str]) -> dict[str, str]:
    """Parse a structured JSON payload, requiring every key in ``fields``.

    Tolerant of code fences, surrounding prose, and extra keys. Raises
    ``StructuredParseError`` when no JSON object decodes or a requested key is
    absent. Values are coerced to ``str``.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    obj = _first_json_object(text)
    if obj is None:
        raise StructuredParseError(f"No JSON object found in model output: {raw[:200]!r}")
    missing = [f for f in fields if f not in obj]
    if missing:
        raise StructuredParseError(f"Missing required keys {missing} in {obj!r}")
    return {f: str(obj[f]) for f in fields}


def _first_json_object(text: str) -> dict[str, object] | None:
    """Return the first JSON object decodable from ``text``, else None.

    Uses ``raw_decode`` from each ``{`` candidate so braces inside string
    values (e.g. OCR'd code) do not confuse a balanced-brace scan.
    """
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
