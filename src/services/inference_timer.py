"""Shared per-inference timer for the text + vision processors (#410).

A small context-manager wrapping ``time.perf_counter`` so the two
processors don't redefine timing logic, and so operators get a
consistent ``{kind}_inference_ms=<N>`` log line for every call —
including the failure branches that previously silently swallowed
timing data.

Usage:

    from services.inference_timer import time_inference

    with time_inference("vision", file_path) as t:
        ...  # model.generate / process_file body
    # t.elapsed_ms now carries the duration; the helper has already
    # emitted the structured log.

The context manager fires the log in ``__exit__`` regardless of
whether the body raised, so timeout / OSError / model-backend failure
paths still produce a measurement that downstream summary code
(``core/display.py``) can aggregate into p50 / p95 / p99 (#410
acceptance criteria).
"""

from __future__ import annotations

import time
from pathlib import Path
from types import TracebackType
from typing import Literal

from loguru import logger

InferenceKind = Literal["vision", "text"]


class _InferenceTimer:
    """Context-manager handle returned from :func:`time_inference`.

    Exposes ``elapsed_ms`` (a float, always >= 0) so callers can attach
    the timing to a result object as well as relying on the log line.
    """

    __slots__ = ("_kind", "_path", "_t0", "elapsed_ms")

    def __init__(self, kind: InferenceKind, file_path: Path | str) -> None:
        self._kind = kind
        self._path = Path(file_path) if not isinstance(file_path, Path) else file_path
        self._t0: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> _InferenceTimer:
        self._t0 = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Always record + log, even on the exception path.
        self.elapsed_ms = max(0.0, (time.perf_counter() - self._t0) * 1000.0)
        # Structured single-line log so log-grep / NDJSON consumers can
        # extract `{kind}_inference_ms=` deterministically.
        if exc is None:
            logger.debug(
                "{}_inference_ms={:.1f} file={}",
                self._kind,
                self.elapsed_ms,
                self._path.name,
            )
        else:
            logger.debug(
                "{}_inference_ms={:.1f} file={} error={}",
                self._kind,
                self.elapsed_ms,
                self._path.name,
                exc_type.__name__ if exc_type is not None else "unknown",
            )


def time_inference(kind: InferenceKind, file_path: Path | str) -> _InferenceTimer:
    """Open a per-inference timing scope.

    Args:
        kind: ``"vision"`` or ``"text"`` — used as the log-field prefix
            and to keep p50/p95/p99 stats partitioned per-modality in
            the run summary.
        file_path: The file being processed (used as the log's ``file``
            field; the timer never opens or stats the path itself).

    Returns:
        A context-manager whose ``.elapsed_ms`` attribute holds the
        measured duration in milliseconds after ``__exit__``.
    """
    return _InferenceTimer(kind, file_path)
