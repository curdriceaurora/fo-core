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


def _redact_args(args: object) -> object:
    """Redact each positional format arg.

    ``record.args`` is either a tuple, a mapping (``%(name)s`` style), or a
    single value. Preserve the shape — ``logging`` will try to format with
    whatever we return.
    """
    if isinstance(args, tuple):
        return tuple(_redact_text(str(a)) if isinstance(a, str) else a for a in args)
    if isinstance(args, dict):
        return {k: _redact_text(str(v)) if isinstance(v, str) else v for k, v in args.items()}
    if isinstance(args, str):
        return _redact_text(args)
    return args


class CredentialRedactingFilter(logging.Filter):
    """Redact credential-shaped substrings in log records. Never drops.

    Attach to the root logger (or any parent logger) so every child logger
    inherits the filter. Per the ``logging`` module semantics, a filter on
    a logger applies to records that *originate* at that logger; filters
    on a handler apply to records routed through it. For maximum coverage
    the attachment point is the root logger in ``cli.main``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        if record.args:
            record.args = _redact_args(record.args)  # type: ignore[assignment]
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
    return instance
