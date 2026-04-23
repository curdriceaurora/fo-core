"""Tests for cli.utils.log_redact — credential-redacting logging filter (A3).

A3 adds a project-wide ``logging.Filter`` that masks credential-looking
values in log records so a future code path that naively logs
``config.api_key`` or stuffs a secret into an exception message doesn't
leak it to a file / stdout / collected log.

The filter must cover the forms credentials commonly appear in:

- ``key=value`` / ``key: value`` / ``key="value"`` / ``key='value'`` for
  common credential keys (api_key, token, secret, password, bearer).
- ``Authorization: Bearer <token>`` HTTP-header style.
- Credentials passed via ``logger.info(..., extra_arg)`` substitution args.

Contract: the filter *always* returns True (never drops records) — its
job is redaction, not filtering. Downstream filters / handler levels
still decide emission.
"""

from __future__ import annotations

import logging

import pytest

from utils.log_redact import REDACTED, CredentialRedactingFilter

pytestmark = [pytest.mark.ci, pytest.mark.unit]


def _make_record(msg: str, *args: object) -> logging.LogRecord:
    """Build a minimal ``LogRecord`` for filter tests."""
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=args or None,
        exc_info=None,
    )


class TestRedactsKeyValueForms:
    """The common ``key=value`` and ``key: value`` leak shapes."""

    def _assert_redacts(self, leaked: str, secret: str = "sk-super-secret-xyz123") -> None:
        f = CredentialRedactingFilter()
        record = _make_record(leaked)
        assert f.filter(record) is True  # never drops records
        assert secret not in record.getMessage()
        assert REDACTED in record.getMessage()

    def test_api_key_equals(self) -> None:
        self._assert_redacts("Calling provider with api_key=sk-super-secret-xyz123")

    def test_api_key_colon(self) -> None:
        self._assert_redacts("config: api_key: sk-super-secret-xyz123 loaded")

    def test_api_key_quoted_double(self) -> None:
        self._assert_redacts('api_key="sk-super-secret-xyz123" provided')

    def test_api_key_quoted_single(self) -> None:
        self._assert_redacts("api_key='sk-super-secret-xyz123' provided")

    def test_hyphenated_api_key(self) -> None:
        self._assert_redacts("api-key=sk-super-secret-xyz123")

    def test_token(self) -> None:
        self._assert_redacts("token=sk-super-secret-xyz123")

    def test_secret(self) -> None:
        self._assert_redacts("secret=sk-super-secret-xyz123")

    def test_password(self) -> None:
        self._assert_redacts("password=sk-super-secret-xyz123")

    def test_case_insensitive_key(self) -> None:
        self._assert_redacts("API_KEY=sk-super-secret-xyz123")


class TestRedactsBearerHeader:
    """HTTP ``Authorization: Bearer <token>`` leaks."""

    def test_bearer_token_redacted(self) -> None:
        f = CredentialRedactingFilter()
        record = _make_record("request sent: Authorization: Bearer abc123.xyz_def")
        assert f.filter(record) is True
        assert "abc123.xyz_def" not in record.getMessage()
        assert REDACTED in record.getMessage()

    def test_bearer_lowercase(self) -> None:
        f = CredentialRedactingFilter()
        record = _make_record("authorization: bearer abc123")
        assert f.filter(record) is True
        assert "abc123" not in record.getMessage()
        assert REDACTED in record.getMessage()


class TestRedactsArgs:
    """Credentials passed as ``%s`` arguments, not inline in the message."""

    def test_redacts_single_string_arg(self) -> None:
        f = CredentialRedactingFilter()
        record = _make_record("auth=%s", "api_key=sk-super-secret-xyz123")
        assert f.filter(record) is True
        assert "sk-super-secret-xyz123" not in record.getMessage()
        assert REDACTED in record.getMessage()

    def test_redacts_mixed_args(self) -> None:
        """Only the credential-shaped arg is redacted; the other survives."""
        f = CredentialRedactingFilter()
        record = _make_record("user=%s auth=%s", "alice", "token=abc123.xyz")
        assert f.filter(record) is True
        rendered = record.getMessage()
        assert "alice" in rendered  # non-credential arg preserved
        assert "abc123.xyz" not in rendered
        assert REDACTED in rendered


class TestPreservesNonCredentials:
    """Boundary: the filter must not over-redact."""

    def test_plain_message_unchanged(self) -> None:
        f = CredentialRedactingFilter()
        record = _make_record("processing file.pdf")
        assert f.filter(record) is True
        assert record.getMessage() == "processing file.pdf"

    def test_key_equals_without_credential_key_unchanged(self) -> None:
        """``user=alice`` is not a credential shape; must not be redacted."""
        f = CredentialRedactingFilter()
        record = _make_record("user=alice processed 5 files")
        assert f.filter(record) is True
        assert "alice" in record.getMessage()


class TestContract:
    """Invariants any consumer can rely on."""

    def test_always_returns_true(self) -> None:
        """The filter is redact-only, never drops. Contract for the
        attachment site in cli.main — downstream handlers / levels still
        decide emission.
        """
        f = CredentialRedactingFilter()
        record = _make_record("anything at all")
        assert f.filter(record) is True

    def test_redacted_sentinel_is_public(self) -> None:
        """``REDACTED`` is a module-level public constant so tests and
        downstream consumers (e.g. log assertions in integration tests)
        can reference the exact replacement token without duplicating it.
        """
        assert isinstance(REDACTED, str)
        assert len(REDACTED) > 0

    def test_record_args_after_filter_formats_without_error(self) -> None:
        """After the filter runs, ``record.getMessage()`` must still format
        cleanly — i.e. the filter preserves arg arity / format-string
        compatibility.
        """
        f = CredentialRedactingFilter()
        record = _make_record("auth=%s user=%s", "token=abc", "alice")
        assert f.filter(record) is True
        rendered = record.getMessage()
        assert "alice" in rendered
        assert "abc" not in rendered

    def test_malformed_template_is_passed_through_unchanged(self) -> None:
        """A record whose ``msg % args`` raises (wrong arg count, etc.) must
        be left as-is so Python's logging error handler can surface the
        original formatting error. The filter must still return ``True``
        (never drops) and must not mutate ``record.msg`` or ``record.args``.
        """
        f = CredentialRedactingFilter()
        # Template expects 2 %s, only 1 arg provided → getMessage() raises
        # TypeError when invoked.
        record = _make_record("need=%s and=%s", "one-arg-only")
        original_msg = record.msg
        original_args = record.args
        assert f.filter(record) is True
        assert record.msg == original_msg
        assert record.args == original_args

    def test_non_string_msg_dict_is_redacted(self) -> None:
        """codex P1: ``logger.info({"api_key": "sk-xxx"})`` — ``record.msg``
        is a ``dict``, ``record.args`` is empty. Previously the filter
        skipped redaction because ``isinstance(record.msg, str)`` was
        False; ``LogRecord.getMessage()`` would later ``str()`` the dict
        and leak the credential. The filter must always stringify and
        redact.
        """
        f = CredentialRedactingFilter()
        record = _make_record({"api_key": "sk-super-secret-xyz123"})  # type: ignore[arg-type]
        assert f.filter(record) is True
        rendered = record.getMessage()
        assert "sk-super-secret-xyz123" not in rendered
        assert REDACTED in rendered

    def test_non_string_msg_with_leaking_custom_str(self) -> None:
        """Custom object whose ``__str__`` returns a credential-shaped
        string. Same leak shape as the dict case but via an object.
        """

        class _LeakyObj:
            def __str__(self) -> str:
                return "token=sk-leaked-secret"

        f = CredentialRedactingFilter()
        record = _make_record(_LeakyObj())  # type: ignore[arg-type]
        assert f.filter(record) is True
        rendered = record.getMessage()
        assert "sk-leaked-secret" not in rendered
        assert REDACTED in rendered

    def test_redacts_exc_info_when_args_also_present(self) -> None:
        """codex P1 (PRRT_kwDOR_Rkws59HjtQ): ``logger.error("msg: %s",
        value, exc_info=True)`` and ``logger.exception("msg: %s", value)``
        take the args branch. Before the fix, the args branch returned
        before ``exc_info`` scrubbing ran, leaving the traceback
        unredacted even though the main message was sanitized. Both
        branches must now redact the traceback.
        """
        import sys

        f = CredentialRedactingFilter()
        try:
            raise RuntimeError("api_key=sk-super-secret-xyz123 inside error")
        except RuntimeError:
            exc_info = sys.exc_info()
        record = _make_record("failed to %s", "process")
        record.exc_info = exc_info
        assert f.filter(record) is True
        assert record.exc_text is not None
        assert "sk-super-secret-xyz123" not in record.exc_text
        assert REDACTED in record.exc_text

    def test_redacts_exception_traceback_text(self) -> None:
        """codex P1: ``logger.exception(...)`` / ``exc_info=True`` attaches
        a ``(type, value, traceback)`` tuple. The default formatter caches
        ``formatException`` output in ``record.exc_text``. If the exception
        message itself contains a credential (``RuntimeError("api_key=
        sk-xxx")``), it leaks into the log output verbatim unless
        ``exc_text`` is also redacted.
        """
        f = CredentialRedactingFilter()
        try:
            raise RuntimeError("api_key=sk-super-secret-xyz123 inside error")
        except RuntimeError:
            import sys

            exc_info = sys.exc_info()
        record = _make_record("caught an error")
        record.exc_info = exc_info
        assert f.filter(record) is True
        assert record.exc_text is not None
        assert "sk-super-secret-xyz123" not in record.exc_text
        assert REDACTED in record.exc_text

    def test_buggy_msg_str_does_not_escape_filter(self) -> None:
        """If ``str(record.msg)`` itself raises (buggy custom ``__str__``
        with no args present), the filter must swallow and return True.
        Same contract as ``test_buggy_arg_str_does_not_escape_filter`` but
        for the no-args branch.
        """

        class _PoisonObj:
            def __str__(self) -> str:
                raise RuntimeError("buggy __str__")

        f = CredentialRedactingFilter()
        original = _PoisonObj()
        record = _make_record(original)  # type: ignore[arg-type]
        assert f.filter(record) is True
        assert record.msg is original  # untouched

    def test_buggy_arg_str_does_not_escape_filter(self) -> None:
        """codex P2: the filter is attached via ``setLogRecordFactory`` so it
        runs on every ``logger.*()`` call. If a ``%s`` arg's ``__str__`` /
        ``__repr__`` raises an exception unrelated to format errors
        (``RuntimeError``, ``AttributeError``, custom ``Exception``
        subclass), that exception must NOT propagate out of the filter —
        otherwise the caller's normal code path breaks, including for
        records that would later be dropped by handler level.
        """

        class _PoisonArg:
            def __str__(self) -> str:
                raise RuntimeError("buggy __str__")

            def __repr__(self) -> str:
                raise RuntimeError("buggy __repr__")

        f = CredentialRedactingFilter()
        record = _make_record("auth=%s", _PoisonArg())
        original_msg = record.msg
        original_args = record.args
        # Must return True (never drops) and must not let RuntimeError escape.
        assert f.filter(record) is True
        # Record left untouched so logging's own emit() path can surface the
        # error via its own handler chain.
        assert record.msg == original_msg
        assert record.args == original_args

    def test_loguru_patcher_replaces_exception_with_sanitized_version(self) -> None:
        """codex P1 (PRRT_kwDOR_Rkws59HjtX): the loguru patcher must
        overwrite ``record["exception"]`` with a sanitized
        ``RecordException`` — loguru renders ``{exception}`` from that
        field, never from ``record["extra"][...]``. Before the fix the
        redacted text was stashed in an unused extra and the original
        exception still leaked.
        """
        from utils.log_redact import _install_on_loguru

        instance = CredentialRedactingFilter()
        _install_on_loguru(instance)
        patcher = getattr(instance, "_loguru_patcher", None)
        assert patcher is not None, "loguru patcher was not installed"

        try:
            raise RuntimeError("api_key=sk-super-secret-xyz123 inside error")
        except RuntimeError as exc:
            from loguru._recattrs import RecordException

            orig_exc = RecordException(type(exc), exc, exc.__traceback__)

        record = {
            "message": "an error occurred",
            "exception": orig_exc,
            "extra": {},
        }
        patcher(record)

        # The exception payload must be REPLACED, not left in extra.
        new_exc = record["exception"]
        assert new_exc is not orig_exc, (
            "patcher left original exception in record['exception'] — leak"
        )
        rendered = str(new_exc.value)
        assert "sk-super-secret-xyz123" not in rendered
        assert REDACTED in rendered

    def test_install_on_root_installs_loguru_patcher(self) -> None:
        """codex P1: ``install_on_root`` must also install a patcher on the
        Loguru logger (``src/models/_openai_client.py`` + ``_claude_client.py``
        use loguru). Records emitted through loguru bypass
        ``setLogRecordFactory``, so a stdlib-only install leaves loguru
        paths unprotected. Installing via ``logger.configure(patcher=...)``
        covers them.
        """
        from utils.log_redact import install_on_root

        original_factory = logging.getLogRecordFactory()
        try:
            installed = install_on_root()
            # The filter instance stashes the loguru patcher when the
            # loguru integration succeeded.
            assert getattr(installed, "_loguru_patcher", None) is not None, (
                "install_on_root did not install the loguru patcher"
            )
        finally:
            logging.setLogRecordFactory(original_factory)
            for f in [
                filt
                for filt in logging.getLogger().filters
                if isinstance(filt, CredentialRedactingFilter)
            ]:
                logging.getLogger().removeFilter(f)

    def test_install_on_root_honours_extra_loggers(self) -> None:
        """``install_on_root(extra_loggers=["foo"])`` attaches the filter to
        each named logger so it fires for records originating there. Belt-
        and-suspenders for the ``setLogRecordFactory`` install.
        """
        from utils.log_redact import install_on_root

        original_factory = logging.getLogRecordFactory()
        named = "test_log_redact_extra"
        target_logger = logging.getLogger(named)
        pre_filters = list(target_logger.filters)
        try:
            installed = install_on_root(extra_loggers=[named])
            assert installed in target_logger.filters, (
                "install_on_root did not attach filter to the named logger"
            )
        finally:
            logging.setLogRecordFactory(original_factory)
            target_logger.filters = pre_filters

    def test_regression_template_placeholder_preserved_with_credential_key_in_msg(
        self,
    ) -> None:
        """codex P1: ``logger.info("api_key=%s", secret)`` has a credential
        key in ``record.msg`` with a ``%s`` placeholder and the secret in
        ``record.args``. Naively redacting ``record.msg`` could strip the
        ``%s`` (capture group consumes it), leaving args intact —
        ``record.getMessage()`` then raises ``TypeError``, Python's logging
        error handler prints the raw args tuple (including the secret) to
        stderr, and the filter ends up LEAKING the credential it was meant
        to mask. The filter must format first, redact the result, and
        clear ``args`` so downstream ``getMessage()`` never raises.
        """
        f = CredentialRedactingFilter()
        record = _make_record("api_key=%s", "sk-super-secret-xyz123")
        assert f.filter(record) is True
        # getMessage() must not raise — the classic bug symptom.
        rendered = record.getMessage()
        # And the secret must be absent from the rendered output.
        assert "sk-super-secret-xyz123" not in rendered
        assert REDACTED in rendered


class TestCLIIntegration:
    """End-to-end: invoking the CLI main-callback installs the filter on root.

    Regression guard for the attachment site in ``cli.main.main_callback`` —
    without this, a refactor that drops the ``install_on_root()`` call could
    silently regress the safety net. The test runs a trivial command that
    logs a credential-shaped message and asserts the captured log output is
    redacted.
    """

    def test_filter_installed_by_cli_main_callback(self) -> None:
        from typer.testing import CliRunner

        from cli.main import app

        root = logging.getLogger()
        preexisting = [f for f in root.filters if isinstance(f, CredentialRedactingFilter)]
        original_factory = logging.getLogRecordFactory()
        for f in preexisting:
            root.removeFilter(f)
        try:
            # Invoke a real command so main_callback runs (``--help`` and
            # ``--version`` are eager and short-circuit the callback).
            result = CliRunner().invoke(app, ["version"])
            assert result.exit_code == 0
            # Filter attached to root.
            attached = [f for f in root.filters if isinstance(f, CredentialRedactingFilter)]
            assert len(attached) >= 1, (
                "cli.main.main_callback did not install CredentialRedactingFilter"
            )
            # Propagated records from any logger get redacted via the
            # ``setLogRecordFactory`` wrapper. Drive a record through
            # ``logging.getLogger("integration.test")`` and verify the
            # factory-redacted message.
            record = logging.getLogger("integration.test").makeRecord(
                "integration.test",
                logging.INFO,
                __file__,
                0,
                "api_key=sk-leaked-secret-xyz",
                None,
                None,
            )
            rendered = record.getMessage()
            assert "sk-leaked-secret-xyz" not in rendered
            assert REDACTED in rendered
        finally:
            logging.setLogRecordFactory(original_factory)
            for f in [f for f in root.filters if isinstance(f, CredentialRedactingFilter)]:
                root.removeFilter(f)
            for f in preexisting:
                root.addFilter(f)
