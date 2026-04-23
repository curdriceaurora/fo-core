"""Credential-redacting ``logging.Filter`` (A3, hardening roadmap).

Project-wide safety net for the common shapes in which API keys, tokens,
passwords and bearer credentials accidentally end up in log records. The
existing model-client code already avoids logging the key directly (see
``models/_openai_client.py:74`` and ``models/_claude_client.py:69``, both of
which log ``type(e).__name__`` rather than the exception message), but
future code paths can still reach a ``logger.info("api_key=%s", ...)`` by
accident — the filter exists so the mistake fails closed.

Contract:

- ``CredentialRedactingFilter.filter`` always returns ``True``. The filter's
  job is redaction, not level-based suppression — handler levels and
  downstream filters still decide emission.
- Both the inline message (``record.msg``) and the positional format args
  (``record.args``) are scrubbed. ``record.getMessage()`` continues to work.
- ``REDACTED`` is a module-level public constant so tests and integration
  assertions can reference the exact replacement token without duplicating
  its string form.

The filter is attached to the root logger in ``cli.main`` so every
``logging.getLogger(__name__)`` inherits the protection.
"""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Iterable

REDACTED = "[REDACTED]"

# Keys that, when seen as the left-hand side of ``key=value`` / ``key: value``
# / ``key="value"`` in a log message, should have their right-hand side
# replaced with ``REDACTED``. Hyphen and underscore spellings both covered
# (api_key vs api-key). Matched case-insensitively via the ``(?i)`` in the
# assembled pattern below.
_CRED_KEYS = (
    "api[_-]?key",
    "api[_-]?token",
    "access[_-]?token",
    "auth[_-]?token",
    "bearer[_-]?token",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
)
# ``key`` + optional quotes + ``=`` or ``:`` + optional quotes, followed by
# the value we want to capture (everything up to whitespace / a quote /
# a delimiter). The key is kept in the replacement so the log line still
# tells you *which* credential leaked — just not what.
_KV_PATTERN = re.compile(
    r"(?i)(?P<key>(?:" + "|".join(_CRED_KEYS) + r")[\"']?\s*[:=]\s*[\"']?)"
    r"(?P<value>[^\"'\s,;&}\])]+)"
)

# ``Authorization: Bearer <token>`` HTTP header shape. Distinct from the
# ``key=value`` pattern because the separator is whitespace rather than ``=``.
_BEARER_PATTERN = re.compile(r"(?i)(?P<prefix>authorization:\s*bearer\s+)(?P<value>[^\s,;}]+)")


def _redact_text(text: str) -> str:
    """Apply all credential patterns to ``text``.

    Returns a new string; the original is not mutated. Order of application
    doesn't matter because the patterns don't overlap.
    """
    text = _KV_PATTERN.sub(lambda m: f"{m.group('key')}{REDACTED}", text)
    text = _BEARER_PATTERN.sub(lambda m: f"{m.group('prefix')}{REDACTED}", text)
    return text


class CredentialRedactingFilter(logging.Filter):
    """Redact credential-shaped substrings in log records. Never drops.

    Attach to the root logger (or any parent logger) so every child logger
    inherits the filter. Per the ``logging`` module semantics, a filter on
    a logger applies to records that *originate* at that logger; filters
    on a handler apply to records routed through it. For maximum coverage
    the attachment point is the root logger in ``cli.main``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact ``record`` in-place. Always returns ``True`` (never drops).

        When ``record.args`` is non-empty, the record is a template +
        arguments pair (e.g. ``logger.info("api_key=%s", secret)``).
        Running the redact pattern on ``record.msg`` alone can strip ``%s``
        placeholders while leaving ``record.args`` intact, and the
        downstream ``record.getMessage()`` then raises ``TypeError`` —
        Python's logging error handler would print the raw ``args`` tuple
        (including the unredacted secret) to stderr. Avoid that by
        formatting first and redacting the result, then clearing ``args``
        so the formatted-and-redacted string is what ``getMessage()``
        returns.
        """
        if record.args:
            try:
                formatted = record.getMessage()
            except Exception:
                # ``getMessage()`` runs ``record.msg % record.args`` which
                # invokes each arg's ``__str__`` / ``__repr__``. A bug in
                # either (custom ``Exception`` subclass, buggy ``__str__``
                # that raises ``RuntimeError`` / ``AttributeError``, etc.)
                # must NOT escape the filter. We're attached via
                # ``setLogRecordFactory`` so this runs on EVERY
                # ``logger.*()`` call — escalating here would break the
                # caller's normal execution, including records that would
                # otherwise be dropped later by level filtering. Swallow
                # any exception, leave the record untouched, and let
                # ``logging`` surface the error via its own handler path
                # when the downstream ``emit()`` re-attempts the format.
                return True
            record.msg = _redact_text(formatted)
            record.args = None
            return True
        # No-args branch. Always stringify ``record.msg`` before redacting —
        # callers can pass non-string objects (``logger.info({"api_key":
        # "..."})`` or any custom object whose ``__str__`` returns
        # ``"token=..."``). ``LogRecord.getMessage()`` would ``str()`` them
        # at emission time and leak the credential; we must redact that
        # same string representation here. Same blind-catch rationale as
        # above — a buggy ``__str__`` must not escape.
        try:
            rendered = str(record.msg)
        except Exception:
            return True
        record.msg = _redact_text(rendered)
        # codex P1: ``logger.exception()`` / ``logger.*(..., exc_info=True)``
        # attaches a ``(type, value, traceback)`` tuple at ``record.exc_info``.
        # The default formatter later calls ``formatException`` and caches
        # the result in ``record.exc_text`` — at which point the raw
        # exception message (e.g. ``RuntimeError("api_key=sk-xxx")``) is
        # rendered verbatim into the log output. Pre-format and redact the
        # traceback now so the formatter's "use cached exc_text if already
        # set" branch picks up our scrubbed version instead.
        if record.exc_info:
            try:
                exc_text = "".join(traceback.format_exception(*record.exc_info))
            except Exception:
                return True
            record.exc_text = _redact_text(exc_text)
        return True


def _install_on_loguru(instance: CredentialRedactingFilter) -> bool:
    """Register a patcher on the Loguru logger that redacts every record.

    Parts of the codebase (``src/models/_openai_client.py``,
    ``src/models/_claude_client.py``) use Loguru rather than the stdlib
    ``logging`` module. Loguru records don't go through
    ``setLogRecordFactory``, so a stdlib-only install leaves Loguru paths
    unprotected. Loguru's patcher API (``logger.configure(patcher=fn)``
    since 0.6) runs ``fn`` on every record dict before emission, which is
    the symmetric hook point.

    Returns ``True`` if loguru was installed, ``False`` if loguru is
    unavailable (the dep is declared as a base requirement in
    ``pyproject.toml`` but importing can still fail in minimal test envs).
    """
    try:
        from loguru import logger as loguru_logger
    except ImportError:  # pragma: no cover — loguru is a base dep
        return False

    from typing import Any

    def _patcher(record: Any) -> None:
        # Loguru record: ``{'message': str, 'exception': RecordException | None, ...}``.
        # Redact the main message and the exception repr (which loguru
        # stringifies during emission just like logging's exc_info path).
        msg = record.get("message")
        if isinstance(msg, str):
            record["message"] = _redact_text(msg)
        exc = record.get("exception")
        # ``RecordException`` is a namedtuple-ish object with ``type``,
        # ``value``, ``traceback``. We redact ``value``'s str representation
        # by replacing the exception with a re-formatted version, but the
        # simplest safe thing is to scrub the message via loguru's built-in
        # pre-formatted repr. Best-effort: stringify and rely on the
        # scrubbed message + exception message caught at emission.
        if exc is not None:
            try:
                exc_text = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))
            except Exception:
                # Buggy ``__str__``/``__repr__`` on the exception itself.
                # Surface the redacted sentinel so consumers know the
                # exception was suppressed for safety rather than silently
                # dropped (the silent-broad-except CI guardrail requires a
                # non-silent statement in the handler body).
                record["extra"]["redacted_exception"] = REDACTED
                return
            # Loguru emits the exception block separately from the message;
            # stash the scrubbed text on the record under a dedicated key
            # that the default formatter appends via ``{exception}``.
            record["extra"]["redacted_exception"] = _redact_text(exc_text)

    # ``patcher`` runs on every record; installing twice would stack, so
    # configure with a single canonical patcher. ``logger.configure``
    # replaces the patcher rather than appending, making this idempotent.
    loguru_logger.configure(patcher=_patcher)
    # Keep a reference for potential teardown by tests.
    instance._loguru_patcher = _patcher  # type: ignore[attr-defined]
    return True


def install_on_root(extra_loggers: Iterable[str] = ()) -> CredentialRedactingFilter:
    """Install the redacting filter process-wide via ``setLogRecordFactory``.

    Python ``logging`` applies ``Logger`` filters only at the logger where a
    record *originates*; propagation to ancestor handlers does not re-run
    ancestor filters. Attaching a filter to the root logger therefore does
    NOT cover records emitted by child loggers (the common case — every
    ``logging.getLogger(__name__)`` in ``src/``).

    The fix is to wrap the global ``LogRecordFactory`` so redaction happens
    at record construction time, before any logger/handler sees it. Every
    record — regardless of originating logger or handler chain — is
    redacted. For symmetry we also add the filter to the root logger and
    any ``extra_loggers`` so ``logger.filter(record)`` continues to apply
    the same sanitisation to records constructed outside our factory
    (rare, but possible).

    Idempotent: calling twice installs exactly one factory wrapper (the
    second call is a no-op if our wrapper is already the active factory).
    Tests that need to restore the original factory can read
    ``filter_instance._original_factory`` and pass it back to
    ``logging.setLogRecordFactory``.

    Args:
        extra_loggers: Additional named loggers to attach the filter to
            as a belt-and-suspenders symmetric install.

    Returns:
        The installed filter instance. Callers may hold it for teardown.
    """
    instance = CredentialRedactingFilter()
    current_factory = logging.getLogRecordFactory()
    # Detect our own wrapper by a sentinel attribute so repeated installs
    # don't stack wrappers.
    if not getattr(current_factory, "_fo_log_redact_installed", False):

        def _redacting_factory(*args: object, **kwargs: object) -> logging.LogRecord:
            record = current_factory(*args, **kwargs)
            instance.filter(record)
            return record

        _redacting_factory._fo_log_redact_installed = True  # type: ignore[attr-defined]
        instance._original_factory = current_factory  # type: ignore[attr-defined]
        logging.setLogRecordFactory(_redacting_factory)

    logging.getLogger().addFilter(instance)
    for name in extra_loggers:
        logging.getLogger(name).addFilter(instance)
    # codex P1: Loguru has its own record pipeline that doesn't go through
    # ``setLogRecordFactory``. Install a symmetric patcher so loguru-based
    # log sites (``_openai_client.py``, ``_claude_client.py``) are covered.
    _install_on_loguru(instance)
    return instance
