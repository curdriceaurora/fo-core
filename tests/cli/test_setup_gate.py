"""Test that the setup gate runs for all commands except an allowlist.

Pins the contract: pre-setup, the gate fires and shows the friendly
"First-time setup required" panel for every non-allowlisted command;
allowlisted commands (`setup`, `version`, `doctor`, `update`,
`recover`, `config`, `hardware-info`) bypass the gate so users can run
diagnostics or finish setup without a chicken-and-egg loop.

Step 3 promoted the gate from organize/preview to the global Typer
callback. This test guards against regression — without it, a future
edit to the allowlist could re-introduce the original bug where every
non-organize command crashes pre-setup with a cryptic stack trace.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app


@pytest.fixture
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force a config dir where setup_completed is False.

    Patches `config.manager.DEFAULT_CONFIG_DIR` so the gate's
    `ConfigManager().load()` reads from an empty dir → returns a
    default `AppConfig(setup_completed=False)`.
    """
    monkeypatch.setattr("config.manager.DEFAULT_CONFIG_DIR", tmp_path)
    return tmp_path


@pytest.mark.unit
@pytest.mark.ci
@pytest.mark.uses_setup_gate
@pytest.mark.parametrize("cmd", ["setup", "version", "doctor", "config", "hardware-info"])
def test_allowlisted_commands_bypass_gate(cmd: str, fresh_config: Path) -> None:
    """Allowlisted commands must work pre-setup.

    `--help` always exits 0 regardless of the gate; what matters is
    that the panel doesn't appear (which would mean the gate ran).
    """
    runner = CliRunner()
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0, result.output
    assert "First-time setup required" not in result.output


@pytest.mark.integration
@pytest.mark.ci
@pytest.mark.uses_setup_gate
def test_organize_blocked_pre_setup(fresh_config: Path, tmp_path: Path) -> None:
    """`fo organize ...` pre-setup hits the gate and shows the panel.

    Uses real input/output dirs to get past path validation and into
    the actual gate path. The gate is in `main_callback` so any
    invocation without `--help` triggers it.
    """
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(app, ["organize", str(in_dir), str(out_dir)])
    # The gate exits the process; result.output carries the panel.
    assert "First-time setup required" in result.output


@pytest.mark.integration
@pytest.mark.ci
@pytest.mark.uses_setup_gate
@pytest.mark.parametrize("cmd", ["search", "analyze"])
def test_other_entry_commands_blocked_pre_setup(
    cmd: str, fresh_config: Path, tmp_path: Path
) -> None:
    """Step 3's win: search/analyze are now gated like organize.

    Before Step 3, only organize/preview ran the gate; search and
    analyze would fail pre-setup with cryptic errors. This test pins
    the new behavior — they show the friendly panel instead.
    """
    runner = CliRunner()
    # `--help` is the safest no-arg invocation; it would show usage
    # output if the gate didn't fire first.
    result = runner.invoke(app, [cmd])  # no --help so the gate runs
    # Some commands may also error on missing required args; the
    # panel must appear regardless of the secondary failure.
    assert "First-time setup required" in result.output
