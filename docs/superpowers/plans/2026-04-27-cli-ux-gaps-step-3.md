# CLI UX Gaps (Step 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three CLI-surface UX gaps that make beta tester bug reports hard to triage: (a) no `--debug` flag and stack traces are swallowed; (b) the `setup_completed` first-run gate only protects `fo organize` and `fo preview`; (c) config validation errors lack hints. Together these turn "something failed" into actionable reports.

**Architecture:** All three changes target `src/cli/main.py:62-88` (the global Typer callback) and adjacent CLI files. `--debug` is a new global flag added to `main_callback`. The setup gate is promoted to a callback-level check that runs for every subcommand except a configurable allowlist (`setup`, `version`, `doctor`, `update`, `recover`, `config`). Config validation hints come from extending `cli/config_cli.py`'s error paths with "valid values" lists.

**Tech Stack:** Typer, loguru, existing `cli.state.CLIState`, existing `_check_setup_completed` from `src/cli/organize.py:17`.

**Out of scope:** Remote crash reporting (Sentry-style telemetry) — we surface the traceback locally via `--debug`, but don't ship it anywhere. Migrating away from `print(f"[red]Error: {exc}[/red]")` to a unified exception handler — that's a larger refactor; this plan adds `--debug` alongside the existing pattern without ripping it out.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/cli/state.py` | Modify | Add `debug: bool` to `CLIState` |
| `src/cli/main.py` | Modify | Add `--debug` global flag; install loguru handler when set; promote setup gate to callback |
| `src/cli/organize.py` | Modify | Drop the now-redundant inline `_check_setup_completed()` calls (logic moved to callback) |
| `src/cli/config_cli.py` | Modify | Replace bare validation errors with hint-rich messages |
| `src/utils/cli_errors.py` | Create | Helper `format_validation_error(field, value, valid_values)` returning a styled string |
| `tests/cli/test_debug_flag.py` | Create | Tests for `--debug` enabling traceback output |
| `tests/cli/test_setup_gate.py` | Create | Tests for the global setup gate covering allowlisted and non-allowlisted commands |
| `tests/cli/test_config_validation_hints.py` | Create | Tests for config error message format |
| `docs/troubleshooting.md` | Modify | Document `--debug` and link from beta-bug template |

Plan conventions: see [2A plan](2026-04-27-audio-model-wiring-2a.md) "Conventions for this plan" section.

---

## Group A: --debug flag

### Task A1: Add `debug: bool` to CLIState

**Files:**
- Modify: `src/cli/state.py`
- Test: `tests/cli/test_debug_flag.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_debug_flag.py`:

```python
"""Tests for the global --debug flag."""
from __future__ import annotations
import pytest
from cli.state import CLIState


@pytest.mark.unit
def test_cli_state_has_debug_field() -> None:
    state = CLIState(verbose=False, dry_run=False, json_output=False, yes=False, no_interactive=False, debug=True)
    assert state.debug is True


@pytest.mark.unit
def test_cli_state_debug_defaults_false() -> None:
    state = CLIState(verbose=False, dry_run=False, json_output=False, yes=False, no_interactive=False)
    assert state.debug is False
```

- [ ] **Step 2: Run — expected failure**

```bash
pytest tests/cli/test_debug_flag.py -v
```

- [ ] **Step 3: Add `debug: bool = False` to CLIState**

Open `src/cli/state.py`, find the `CLIState` dataclass, add `debug: bool = False` as a new field after `no_interactive`.

- [ ] **Step 4: Run — expected pass**

- [ ] **Step 5: Commit**

```bash
git add src/cli/state.py tests/cli/test_debug_flag.py
git commit -m "feat(cli): add debug field to CLIState"
```

---

### Task A2: Wire `--debug` flag into main_callback

**Files:**
- Modify: `src/cli/main.py:62-88`
- Test: `tests/cli/test_debug_flag.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/cli/test_debug_flag.py`:

```python
@pytest.mark.unit
def test_debug_flag_sets_state_and_installs_loguru_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When --debug is passed, the loguru stderr handler is installed at DEBUG."""
    from typer.testing import CliRunner
    from cli.main import app

    captured_levels: list[str] = []

    def _fake_add(sink, **kwargs):  # type: ignore[no-untyped-def]
        captured_levels.append(kwargs.get("level", ""))
        return 1  # handler id

    monkeypatch.setattr("loguru.logger.add", _fake_add)

    runner = CliRunner()
    result = runner.invoke(app, ["--debug", "version"])
    assert result.exit_code == 0
    assert "DEBUG" in captured_levels


@pytest.mark.unit
def test_no_debug_flag_does_not_install_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner
    from cli.main import app

    captured_levels: list[str] = []
    monkeypatch.setattr(
        "loguru.logger.add",
        lambda sink, **kwargs: captured_levels.append(kwargs.get("level", "")) or 1,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    # No DEBUG-level handler installed by us (loguru may have a default; we
    # only assert that --debug, when absent, does not install ours)
    assert "DEBUG" not in captured_levels
```

- [ ] **Step 2: Run — expected failure**

- [ ] **Step 3: Add the `--debug` option to `main_callback`**

In `src/cli/main.py`, modify `main_callback` to accept a new option:

```python
    debug: bool = typer.Option(
        False,
        "--debug",
        help=(
            "Enable verbose logging and surface tracebacks on errors. "
            "Required for filing useful beta bug reports."
        ),
    ),
```

In the function body, before `ctx.obj = CLIState(...)`:

```python
    if debug:
        import sys
        from loguru import logger as _loguru_logger
        _loguru_logger.add(sys.stderr, level="DEBUG", backtrace=True, diagnose=True)
```

And include `debug=debug` in the `CLIState(...)` constructor.

- [ ] **Step 4: Run — expected pass**

- [ ] **Step 5: Commit**

```bash
git add src/cli/main.py tests/cli/test_debug_flag.py
git commit -m "feat(cli): add --debug flag, install loguru DEBUG handler when set"
```

---

### Task A3: Surface tracebacks via `--debug` in CLI error handlers

**Files:**
- Modify: `src/cli/organize.py:146-148` (and similar `except Exception as exc` blocks elsewhere — grep first)

- [ ] **Step 1: Find all `except Exception as exc:` followed by `console.print(f"[red]Error..."` patterns**

```bash
grep -rn -B 0 -A 3 "except Exception as exc:" src/cli/
```

For each match, plan to surface the traceback when `_get_state().debug` is true.

- [ ] **Step 2: Write the failing test**

Append to `tests/cli/test_debug_flag.py`:

```python
@pytest.mark.unit
def test_debug_surfaces_traceback_on_organize_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typer.testing import CliRunner
    from cli.main import app

    monkeypatch.setattr("cli.organize._check_setup_completed", lambda: True)

    def _boom(*a, **kw):
        raise RuntimeError("boom-from-test")

    monkeypatch.setattr("core.organizer.FileOrganizer.organize", _boom)

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(app, ["--debug", "organize", str(in_dir), str(out_dir)])

    assert result.exit_code == 1
    # Without --debug we'd see only "[red]Error: boom-from-test[/red]"
    # With --debug we expect the traceback to be surfaced (file:line refs)
    assert "boom-from-test" in result.output
    assert "Traceback" in result.output or ".py" in result.output
```

- [ ] **Step 3: Run — expected failure**

- [ ] **Step 4: Update each error handler to surface tracebacks when debug is on**

Replace patterns like:

```python
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
```

with:

```python
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        if _get_state().debug:
            console.print_exception(show_locals=False)
        raise typer.Exit(code=1) from exc
```

(Rich's `Console.print_exception` emits the formatted traceback. Grep for every match and apply consistently.)

- [ ] **Step 5: Run — expected pass**

- [ ] **Step 6: Commit**

```bash
git add src/cli/ tests/cli/test_debug_flag.py
git commit -m "feat(cli): surface tracebacks via console.print_exception when --debug"
```

---

## Group B: Setup gate covers all entry commands

### Task B1: Identify allowlist of commands that don't need setup

**Files:**
- Read only: `src/cli/main.py`

- [ ] **Step 1: Decide the allowlist**

Commands that MUST work pre-setup (they're either bootstrap or read-only diagnostics):

- `setup` — the wizard itself
- `version`, `--version` — version reporting
- `doctor` — diagnostic
- `update` (and subcommands) — updater
- `recover` — emergency recovery
- `config` — viewing/editing config (the wizard isn't the only way)
- `hardware-info` — hardware detection diagnostic

Everything else (`organize`, `preview`, `search`, `analyze`, `dedupe`, `suggest`, `autotag`, `copilot`, `daemon`, `rules`, `model`, `analytics`, `undo`, `redo`, `history`, `benchmark`, `profile`) requires setup.

- [ ] **Step 2: Capture the list as a constant**

This is a code-only step in the next task; this step is just confirming the list before writing code.

---

### Task B2: Promote setup gate to global callback

**Files:**
- Modify: `src/cli/main.py:62-132` (the `main_callback`)
- Modify: `src/cli/organize.py:115,187` — remove the now-redundant `_check_setup_completed()` calls
- Test: `tests/cli/test_setup_gate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_setup_gate.py`:

```python
"""Test that the setup gate runs for all commands except an allowlist."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cli.main import app


@pytest.fixture
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force a config dir where setup_completed is False."""
    monkeypatch.setattr("config.manager.DEFAULT_CONFIG_DIR", tmp_path)
    return tmp_path


@pytest.mark.unit
@pytest.mark.parametrize("cmd", ["organize", "search", "analyze", "dedupe"])
def test_unsetup_blocks_command(cmd: str, fresh_config: Path) -> None:
    runner = CliRunner()
    # Use placeholder args; gate runs before arg parsing for the command
    result = runner.invoke(app, [cmd, "--help"])  # --help should still work
    # Gate should NOT block --help, but should block actual invocation
    assert result.exit_code == 0  # --help bypasses gate

    # Now invoke without --help: should hit the gate
    if cmd in ("organize",):
        result = runner.invoke(app, [cmd, "/nonexistent", "/nonexistent"])
        assert "First-time setup required" in result.output


@pytest.mark.unit
@pytest.mark.parametrize("cmd", ["setup", "version", "doctor", "update", "config"])
def test_allowlisted_commands_bypass_gate(cmd: str, fresh_config: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0
    assert "First-time setup required" not in result.output
```

- [ ] **Step 2: Run — expected mixed (some pass with current behavior; the global gate test fails)**

- [ ] **Step 3: Add allowlist constant in `src/cli/main.py`**

After the existing imports, before `main_callback`:

```python
_SETUP_GATE_ALLOWLIST: frozenset[str] = frozenset({
    "setup",
    "version",
    "doctor",
    "update",
    "recover",
    "config",
    "hardware-info",
})
"""Commands that work pre-setup. Adding to this list relaxes the gate
for that command — verify it doesn't write or organize files first."""
```

- [ ] **Step 4: Add gate logic in `main_callback`**

After the existing `set_flags(...)` line, before the function returns:

```python
    if (
        ctx.invoked_subcommand
        and ctx.invoked_subcommand not in _SETUP_GATE_ALLOWLIST
    ):
        from cli.organize import _check_setup_completed
        _check_setup_completed()
```

- [ ] **Step 5: Remove the redundant inline calls in `src/cli/organize.py`**

Remove `_check_setup_completed()` from both `organize` (line 115) and `preview` (line 187) — the global gate now covers them.

- [ ] **Step 6: Run — expected pass**

- [ ] **Step 7: Commit**

```bash
git add src/cli/main.py src/cli/organize.py tests/cli/test_setup_gate.py
git commit -m "feat(cli): global setup gate via main_callback with allowlist"
```

---

## Group C: Config validation hints

### Task C1: Helper for hint-rich validation errors

**Files:**
- Create: `src/utils/cli_errors.py`
- Test: `tests/utils/test_cli_errors.py`

- [ ] **Step 1: Write the failing test**

Create `tests/utils/test_cli_errors.py`:

```python
"""Tests for the CLI validation-error formatter."""
from __future__ import annotations
import pytest


@pytest.mark.unit
def test_format_validation_error_lists_valid_values() -> None:
    from utils.cli_errors import format_validation_error

    msg = format_validation_error(
        field="device",
        value="unknown",
        valid_values=["auto", "cpu", "cuda", "mps"],
    )
    assert "device" in msg
    assert "unknown" in msg
    assert "auto, cpu, cuda, mps" in msg


@pytest.mark.unit
def test_format_validation_error_suggests_close_match() -> None:
    from utils.cli_errors import format_validation_error

    msg = format_validation_error(
        field="device",
        value="cdua",  # typo for "cuda"
        valid_values=["auto", "cpu", "cuda", "mps"],
    )
    assert "did you mean" in msg.lower()
    assert "cuda" in msg
```

- [ ] **Step 2: Run — expected failure**

- [ ] **Step 3: Implement the helper**

Create `src/utils/cli_errors.py`:

```python
"""Helpers for hint-rich CLI validation errors."""

from __future__ import annotations

import difflib
from collections.abc import Iterable


def format_validation_error(
    *,
    field: str,
    value: object,
    valid_values: Iterable[str],
) -> str:
    """Format a validation error with valid-values list and 'did you mean'."""
    valid = list(valid_values)
    base = (
        f"Invalid value {value!r} for {field}. "
        f"Valid values: {', '.join(valid)}."
    )
    if isinstance(value, str):
        close = difflib.get_close_matches(value, valid, n=1, cutoff=0.6)
        if close:
            base += f" Did you mean {close[0]!r}?"
    return base
```

- [ ] **Step 4: Run — expected pass**

- [ ] **Step 5: Commit**

```bash
git add src/utils/cli_errors.py tests/utils/test_cli_errors.py
git commit -m "feat(utils): hint-rich validation-error formatter"
```

---

### Task C2: Use the helper in config_cli error paths

**Files:**
- Modify: `src/cli/config_cli.py`
- Test: `tests/cli/test_config_validation_hints.py`

- [ ] **Step 1: Locate validation paths in config_cli**

```bash
grep -n "Invalid\|must be one of\|raise typer\|raise ValueError" src/cli/config_cli.py | head -20
```

Capture the spots where the user-facing message is emitted.

- [ ] **Step 2: Write the failing test**

Create `tests/cli/test_config_validation_hints.py`:

```python
"""Test that config edit errors include valid-values hints."""
from __future__ import annotations
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app


@pytest.mark.unit
def test_config_edit_invalid_device_includes_valid_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("config.manager.DEFAULT_CONFIG_DIR", tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["config", "edit", "--device", "cdua"])
    assert result.exit_code != 0
    assert "auto" in result.output
    assert "cuda" in result.output
    assert "did you mean" in result.output.lower()
```

- [ ] **Step 3: Run — expected failure**

- [ ] **Step 4: Replace bare validation messages with the helper**

For each match found in step 1, swap from a bare error to:

```python
from utils.cli_errors import format_validation_error
...
console.print(f"[red]{format_validation_error(field='device', value=device, valid_values=['auto', 'cpu', 'cuda', 'mps'])}[/red]")
raise typer.Exit(code=1)
```

Repeat for `methodology` (`['none', 'para', 'jd']`), `framework` (`['ollama', 'llama_cpp', 'mlx', 'openai', 'claude']`), and any other enum-shaped fields validated in config_cli.

- [ ] **Step 5: Run — expected pass**

- [ ] **Step 6: Commit**

```bash
git add src/cli/config_cli.py tests/cli/test_config_validation_hints.py
git commit -m "feat(config_cli): hint-rich errors with valid-values + did-you-mean"
```

---

## Task D: Document --debug in troubleshooting

**Files:**
- Modify: `docs/troubleshooting.md`

- [ ] **Step 1: Append a "Filing a bug report" section**

````markdown
## Filing a bug report

If you hit an error, the most useful thing you can attach to a bug report is
the output with `--debug` enabled:

```bash
fo --debug <your command and args>
```

`--debug` enables verbose logging and surfaces the full traceback when an
error occurs. Without it, the CLI prints only the error summary, which is
often not enough for triage.

Your bug report should include:

- The full output of `fo --debug <command>` (use the [Beta bug
  template](https://github.com/curdriceaurora/fo-core/issues/new?template=beta-bug.md))
- Output of `fo doctor` (Ollama + dependency check)
- Your OS, Python version, and `fo version`
- Minimal reproduction steps
````

- [ ] **Step 2: Lint**

```bash
pymarkdown -c .pymarkdown.json scan docs/troubleshooting.md
```

- [ ] **Step 3: Commit**

```bash
git add docs/troubleshooting.md
git commit -m "docs(troubleshooting): document --debug for bug reports"
```

---

## Task E: Pre-commit + CI + PR

- [ ] **Step 1: Validation**

```bash
bash .claude/scripts/pre-commit-validation.sh
pytest -m "ci" -v
pytest -m "unit" tests/cli/test_debug_flag.py tests/cli/test_setup_gate.py tests/cli/test_config_validation_hints.py tests/utils/test_cli_errors.py -v
```

- [ ] **Step 2: Code review** via `/code-reviewer`. Focus areas:
  - `--debug` interaction with `--verbose` (both can be set; --debug strictly wider)
  - Setup gate allowlist completeness — every other CLI command must be on the gated path
  - `format_validation_error` doesn't leak secrets if `value` is a credential (it shouldn't be — config_cli doesn't validate api_keys with this helper)

- [ ] **Step 3: Push and open PR**

Title: `feat(cli): --debug flag, global setup gate, hint-rich config errors`

Body should reference §2 of `docs/release/beta-criteria.md` (closes the `--debug flag wired`, the implicit "first-run gate consistent" UX requirement, and contributes to the doc-honesty pass).

---

## Verification checklist

- `fo --debug organize <bad-args>` produces a Rich-formatted traceback in addition to the red error line.
- `fo organize <args>` without prior `fo setup` shows the yellow "First-time setup required" panel (not a stack trace).
- `fo search <query>` without prior `fo setup` shows the same panel (the gate is no longer organize-only).
- `fo config edit --device cdua` produces an error message listing valid values AND suggesting `cuda`.
- `docs/troubleshooting.md` has a "Filing a bug report" section pointing at `--debug`.
- All three §2 entry-checklist rows for UX (`--debug flag wired`, the doc-honesty pass for troubleshooting, `setup_completed` consistency) advance toward done. The bug-report template lands in Step 5.
