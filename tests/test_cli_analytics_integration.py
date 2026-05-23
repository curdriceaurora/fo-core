"""Integration tests for CLI analytics command."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


@pytest.mark.unit
class TestAnalyticsCommand:
    """Tests for ``fo analytics``."""

    def test_analytics_help(self) -> None:
        result = runner.invoke(app, ["analytics", "--help"])
        assert result.exit_code == 0
        assert "analytics" in result.output.lower() or "Usage" in result.output

    def test_analytics_missing_directory_exits_2(self) -> None:
        """Analytics without a required directory argument exits 2 (usage error)."""
        result = runner.invoke(app, ["analytics"])
        assert result.exit_code == 2

    def test_analytics_with_directory(self, tmp_path: pytest.TempPathFactory) -> None:
        """Analytics with a valid directory exits 0."""
        result = runner.invoke(app, ["analytics", str(tmp_path)])
        assert result.exit_code == 0

    def test_analytics_verbose(self, tmp_path: pytest.TempPathFactory) -> None:
        """Analytics with --verbose exits 0."""
        result = runner.invoke(app, ["analytics", str(tmp_path), "--verbose"])
        assert result.exit_code == 0
