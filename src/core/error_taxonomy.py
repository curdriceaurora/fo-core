"""Classify per-file failures into the #411 error taxonomy.

Final summary surfaces:

  vision_timeout    — files where vision exceeded its dispatcher timeout
                      (whether or not the metadata fallback rescued them)
  read_error        — filesystem-layer failures: read denied, symlink
                      rejected, file too large
  unsupported_type  — read returned None / "Unsupported file type"
  inference_error   — model-call failures (provider 500, AttributeError,
                      generic RuntimeError raised by the inference path)
  other             — anything that didn't match the buckets above

Returning a stable string keeps the summary renderer
(``core/display.py``) decoupled from the result classes and lets
analytics consumers (``--json``) emit a clean category field.
"""

from __future__ import annotations

from typing import Any, Literal

ErrorCategory = Literal[
    "vision_timeout",
    "read_error",
    "unsupported_type",
    "inference_error",
    "other",
]

# Recommendation strings the summary renderer attaches to each category
# when it exceeds the 10%-of-scanned-files trigger.  Phrased as
# actionable next steps so an operator can fix the issue without
# re-reading the dispatcher source.
RECOMMENDATIONS: dict[str, str] = {
    "vision_timeout": (
        "consider `--timeout-per-file` to raise the cap or `--workers` to parallelise (#396 / #408)"
    ),
    "read_error": ("check filesystem permissions or exclude unreadable files from the input set"),
    "unsupported_type": (
        "see the top-skipped-extensions list above; install the matching extra "
        "(e.g. `[scientific]`, `[cad]`) or skip those files explicitly"
    ),
    "inference_error": ("check Ollama / provider health; transient backend faults bucket here"),
    "other": ("inspect the per-file logs — these failures didn't fit the known taxonomy"),
}


# Substring tokens that signal a read-side failure, anchored to the
# exact error strings the codebase emits today. We match case-insensitively
# to absorb minor wording drift.
_READ_ERROR_TOKENS: tuple[str, ...] = (
    "refused to read symlink",
    "file not found",
    "permission denied",
    "exceeds max_file_size",  # FileTooLargeError shape
    "filereaderror",
    "fileexists",
    # Every reader in utils/readers/ wraps parse / decode failures as
    # ``FileReadError(f"Failed to read <FORMAT> ...")`` — those propagate
    # through services.text_processor.process_file → ProcessedFile.error
    # as "Failed to read DOCX file ...", "Failed to read PDF file ...",
    # etc.  Without this token they bucket into ``inference_error`` and
    # mislead the operator-facing recommendation.
    "failed to read",
)

# Tokens that signal the file's extension / content type isn't supported.
_UNSUPPORTED_TYPE_TOKENS: tuple[str, ...] = ("unsupported file type",)


def classify_error(result: Any) -> ErrorCategory | None:
    """Return the taxonomy bucket for *result*, or ``None`` if it isn't a failure.

    Args:
        result: ``ProcessedImage`` or ``ProcessedFile``. Any object that
            exposes ``error`` and optionally ``source`` is accepted —
            we duck-type rather than importing the services package so
            this helper stays cheap to load.

    Returns:
        One of the ``ErrorCategory`` literals when the result represents
        a failure (or a #406 vision-timeout fallback); ``None`` for
        clean, happy-path results that don't need bucketing.

    Resolution order matters: vision-timeout fallback is checked first
    because those results carry ``source=fallback_*`` AND ``error=None``,
    so the rest of the matching would treat them as happy-path.
    """
    source = str(getattr(result, "source", "") or "")
    if source.startswith("fallback_"):
        return "vision_timeout"

    error = getattr(result, "error", None)
    if not error:
        return None
    err_lc = str(error).lower()

    # Dispatcher's timeout-as-string sentinel — only reached when the
    # fallback path didn't fire (e.g. text files, or a vision file
    # whose error string survived without the dispatcher's source patch).
    if err_lc.startswith("timed out after"):
        return "vision_timeout"

    if any(tok in err_lc for tok in _UNSUPPORTED_TYPE_TOKENS):
        return "unsupported_type"

    if any(tok in err_lc for tok in _READ_ERROR_TOKENS):
        return "read_error"

    # Inference errors are catch-all for anything that reached the
    # model call (signalled in the dispatcher / processor by
    # confidence==0.0 with an error message that doesn't match the
    # filesystem patterns above). Anything else lands in `other`.
    confidence = getattr(result, "confidence", 1.0)
    if isinstance(confidence, (int, float)) and confidence == 0.0:
        return "inference_error"

    return "other"
