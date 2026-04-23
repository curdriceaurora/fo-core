"""CI-tagged smoke tests: every CLI command wired to ``resolve_cli_path``
rejects a non-existent directory with exit code 2 (``typer.BadParameter``).

The detailed helper semantics live in ``test_path_validation.py``. This file
exists so PR-CI's diff-coverage gate sees the call sites in
``src/cli/{daemon,organize,main,rules,utilities,benchmark,suggest,dedupe_v2,autotag_v2}.py``
as exercised under the ``-m ci`` marker (the integration tests that drive
these commands end-to-end run in the main-push suite, not in PR-CI).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.daemon import daemon_app
from cli.main import app

pytestmark = [pytest.mark.ci, pytest.mark.unit]

runner = CliRunner()


@pytest.fixture(autouse=True)
def _bypass_setup_wizard() -> Iterator[None]:
    """Skip the organize/preview setup-wizard check; the wiring test only
    needs to reach ``resolve_cli_path``."""
    with patch("cli.organize._check_setup_completed", return_value=True):
        yield


def _bogus(tmp_path: Path) -> str:
    return str(tmp_path / "does_not_exist")


# (app, argv-template) — each template uses ``{p}`` for the bogus path.
# Commands that take two paths get two bogus substitutions; one rejected path
# is enough to trigger the ``resolve_cli_path`` branch.
_COMMANDS: list[tuple[str, list[str]]] = [
    ("app", ["organize", "{p}", "{p2}"]),
    ("app", ["preview", "{p}"]),
    ("app", ["benchmark", "run", "{p}"]),
    ("app", ["suggest", "files", "{p}"]),
    ("app", ["suggest", "apply", "{p}"]),
    ("app", ["suggest", "patterns", "{p}"]),
    ("app", ["search", "{p}", "query"]),
    ("app", ["analyze", "{p}"]),
    ("app", ["analytics", "{p}"]),
    ("app", ["autotag", "suggest", "{p}"]),
    ("app", ["autotag", "apply", "{p}", "dummy-tag"]),
    ("app", ["autotag", "batch", "{p}"]),
    ("app", ["dedupe", "scan", "{p}"]),
    ("app", ["dedupe", "resolve", "{p}"]),
    ("app", ["dedupe", "report", "{p}"]),
    ("app", ["rules", "preview", "{p}"]),
    ("app", ["rules", "import", "{p}"]),
    ("daemon", ["process", "{p}", "{p2}"]),
    ("daemon", ["start", "--watch-dir", "{p}", "--output-dir", "{p2}"]),
    ("daemon", ["watch", "{p}"]),
]


@pytest.mark.parametrize(("app_key", "argv_template"), _COMMANDS)
def test_cli_command_rejects_missing_path(
    app_key: str, argv_template: list[str], tmp_path: Path
) -> None:
    target = daemon_app if app_key == "daemon" else app
    bogus = _bogus(tmp_path)
    argv = [a.replace("{p}", bogus).replace("{p2}", bogus + "_other") for a in argv_template]
    result = runner.invoke(target, argv)
    # typer.BadParameter → exit code 2 (POSIX usage-error convention).
    assert result.exit_code == 2, (
        f"expected exit 2 for {argv}; got {result.exit_code}\noutput: {result.output}"
    )
    assert "does not exist" in result.output.lower()
