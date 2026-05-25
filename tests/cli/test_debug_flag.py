"""Tests for the global --debug flag.

Pin the contract: `--debug` (a) populates `CLIState.debug=True`, (b)
installs a loguru DEBUG-level stderr handler at callback time, and (c)
surfaces a Rich traceback via `console.print_exception` when a CLI
command's exception handler fires. Without `--debug` only the red
one-liner appears.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cli.main import app
from cli.state import CLIState


@pytest.mark.unit
@pytest.mark.ci
def test_cli_state_has_debug_field() -> None:
    """The dataclass exposes the new `debug` field."""
    state = CLIState(debug=True)
    assert state.debug is True


@pytest.mark.unit
@pytest.mark.ci
def test_cli_state_debug_defaults_false() -> None:
    """Default-constructed CLIState has debug off — backwards-compat
    contract for any callsite that doesn't explicitly set the flag."""
    state = CLIState()
    assert state.debug is False


@pytest.mark.unit
@pytest.mark.ci
def test_debug_flag_installs_loguru_debug_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--debug attaches a loguru sink at level=DEBUG with backtrace=True, diagnose=False.

    backtrace=True gives frame-linked tracebacks for swallowed exceptions.
    diagnose=False is intentional: diagnose annotates frames with local
    variable values, which can expose credentials or API keys when the
    output is shared in a bug report.
    """
    captured: list[dict[str, Any]] = []

    def _spy_add(_sink: Any, **kwargs: Any) -> int:
        captured.append(kwargs)
        return 1  # handler id

    def _noop_remove(_handler_id: int) -> None:
        pass  # ctx.call_on_close fires remove(); no real handler exists in tests

    monkeypatch.setattr("loguru.logger.add", _spy_add)
    monkeypatch.setattr("loguru.logger.remove", _noop_remove)

    runner = CliRunner()
    result = runner.invoke(app, ["--debug", "logs", "--list"])
    assert result.exit_code == 0
    debug_handlers = [c for c in captured if c.get("level") == "DEBUG"]
    assert len(debug_handlers) >= 1
    assert debug_handlers[0].get("backtrace") is True
    # diagnose=False keeps local variable values out of shared bug reports.
    assert debug_handlers[0].get("diagnose") is False


@pytest.mark.unit
@pytest.mark.ci
def test_no_debug_flag_skips_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --debug, the callback must not install our stderr DEBUG sink.

    Loguru's default sink stays in place; we only assert that we don't
    *additionally* attach a DEBUG stderr handler (no-debug path keeps
    stderr zero-overhead). The session log DEBUG handler is always
    installed (for post-run analysis), but it writes to a file, not stderr.
    """
    captured: list[dict[str, Any]] = []

    def _spy_add(_sink: Any, **kwargs: Any) -> int:
        captured.append({"sink": str(_sink), **kwargs})
        return 1

    monkeypatch.setattr("loguru.logger.add", _spy_add)

    runner = CliRunner()
    result = runner.invoke(app, ["logs", "--list"])
    assert result.exit_code == 0
    # No DEBUG handler to stderr (sys.stderr) without --debug flag
    # (session log DEBUG handler writes to a file, not stderr)
    stderr_debug_handlers = [
        c
        for c in captured
        if c.get("level") == "DEBUG" and "stderr" in str(c.get("sink", "")).lower()
    ]
    assert len(stderr_debug_handlers) == 0, "No DEBUG stderr handler without --debug"


@pytest.mark.integration
@pytest.mark.ci
def test_debug_surfaces_traceback_on_organize_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failing `fo --debug organize ...` shows the traceback in addition
    to the red error line. Without `--debug` only the red line appears.

    Marked integration so it can call into FileOrganizer's import chain
    realistically; the gate-bypass + organize.organize patch keep the
    test fast and deterministic.
    """
    # Bypass first-run gate (this test exercises the error-handler path,
    # not the gate path).
    monkeypatch.setattr("cli.organize._check_setup_completed", lambda: True)

    # Named raiser per T14: `(_ for _ in ()).throw(...)` is banned —
    # use a named function so intent is clear.
    def _raise_runtime(*_a: object, **_kw: object) -> None:
        raise RuntimeError("boom-from-test")

    monkeypatch.setattr("core.organizer.FileOrganizer.organize", _raise_runtime)

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(app, ["--debug", "organize", str(in_dir), str(out_dir)])

    assert result.exit_code == 1
    # The red one-liner is always there.
    assert "boom-from-test" in result.output
    # Under --debug the Rich traceback header must appear — this pins the actual
    # contract (traceback rendered) rather than a generic ".py" substring that
    # could match the error message itself.
    assert "Traceback" in result.output
    assert ".py" in result.output  # at least one frame's file path (secondary check)


@pytest.mark.integration
@pytest.mark.ci
def test_no_debug_omits_traceback_on_organize_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without --debug, only the red one-liner appears — no traceback noise.

    Pins the no-debug contract; if a future refactor accidentally
    surfaces the traceback unconditionally, this test fails.
    """
    monkeypatch.setattr("cli.organize._check_setup_completed", lambda: True)

    # Named raiser per T14.
    def _raise_quiet(*_a: object, **_kw: object) -> None:
        raise RuntimeError("boom-quiet")

    monkeypatch.setattr("core.organizer.FileOrganizer.organize", _raise_quiet)

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(app, ["organize", str(in_dir), str(out_dir)])

    assert result.exit_code == 1
    assert "boom-quiet" in result.output
    # No "Traceback" header from Rich's print_exception.
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# C5: Rotating file log integration tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.ci
def test_rotating_log_file_created(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """main_callback creates fo.log under the canonical logs directory."""
    log_dir = tmp_path / "logs"

    def _fake_paths() -> dict[str, Path]:
        return {"logs": log_dir}

    monkeypatch.setattr("config.path_manager.get_canonical_paths", _fake_paths)
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    result = runner.invoke(app, ["hardware-info"])
    assert result.exit_code == 0
    assert log_dir.exists(), "log directory should be created"
    log_files = list(log_dir.glob("fo.log*"))
    assert log_files, "fo.log should be created in the logs directory"


@pytest.mark.unit
@pytest.mark.ci
def test_debug_flag_lowers_file_log_level(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--debug sets the file sink level to DEBUG instead of WARNING."""
    captured: list[dict[str, Any]] = []

    original_add = __import__("loguru").logger.add

    def _spy_add(_sink: Any, **kwargs: Any) -> int:
        captured.append({"sink": str(_sink), **kwargs})
        return original_add(_sink, **kwargs)

    log_dir = tmp_path / "logs"
    monkeypatch.setattr("config.path_manager.get_canonical_paths", lambda: {"logs": log_dir})
    monkeypatch.setattr("loguru.logger.add", _spy_add)
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    result = runner.invoke(app, ["--debug", "logs", "--list"])
    assert result.exit_code == 0

    file_sinks = [c for c in captured if "fo.log" in c.get("sink", "")]
    assert file_sinks, "file sink should have been added"
    assert file_sinks[0].get("level") == "DEBUG"


@pytest.mark.unit
@pytest.mark.ci
def test_unwritable_log_dir_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unwritable log directory causes graceful degradation — CLI exits 0."""

    def _raise_oserror() -> dict[str, Path]:
        raise OSError("permission denied")

    monkeypatch.setattr("config.path_manager.get_canonical_paths", _raise_oserror)
    monkeypatch.setattr("loguru.logger.remove", lambda _id: None)

    runner = CliRunner()
    result = runner.invoke(app, ["hardware-info"])
    assert result.exit_code == 0
