"""Unit tests for cli.dedupe_strategy helpers.

Covers the `_resolve_console` helper, which previously deferred to
`cli.dedupe.console` via a try/except fallback. Epic D.cleanup (#157 /
PR #167) dropped that v1 lookup in favour of an unconditional `Console()`
when no console is passed, so this test pins both branches of the helper.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from cli.dedupe_strategy import _resolve_console


@pytest.mark.ci
class TestResolveConsole:
    def test_returns_injected_console_when_provided(self) -> None:
        injected = Console()
        assert _resolve_console(injected) is injected

    def test_returns_fresh_console_when_none(self) -> None:
        resolved = _resolve_console(None)
        assert isinstance(resolved, Console)
