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
            attached = [
                f for f in root.filters if isinstance(f, CredentialRedactingFilter)
            ]
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
            for f in [
                f for f in root.filters if isinstance(f, CredentialRedactingFilter)
            ]:
                root.removeFilter(f)
            for f in preexisting:
                root.addFilter(f)
