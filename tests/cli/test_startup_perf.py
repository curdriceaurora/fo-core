"""Startup performance regression test for ``fo version`` (issue #404).

These checks run in a subprocess so the in-process pytest collection state
doesn't pollute sys.modules with imports another test or fixture already
brought in.  PEP 562 lazy ``__getattr__`` on ``undo/__init__.py`` is what
keeps the lazy contract — if a future change accidentally adds a
module-level eager import that pulls ``undo.rollback`` /
``undo.undo_manager`` / ``undo.viewer`` / ``history.tracker``, the
assertions below fail.

Note: we deliberately avoid asserting on ``sqlite3`` directly. Stdlib
sqlite3 can be pulled by unrelated dependencies (e.g. some test fixtures
in the parent process leak into a subprocess that inherits the env);
asserting on the project-owned heavy modules is the meaningful check.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.ci]

_HEAVY_MODULES = (
    "undo.rollback",
    "undo.undo_manager",
    "undo.viewer",
    "history.tracker",
    "history.database",
)


def _run_probe(script: str) -> subprocess.CompletedProcess[str]:
    # Inherit the parent's PYTHONPATH so the subprocess can locate the
    # in-tree `src/` packages (cli, undo, …). Without this the subprocess
    # only sees site-packages and reports ModuleNotFoundError.
    import os

    env = os.environ.copy()
    repo_src = Path(__file__).resolve().parents[2] / "src"
    env["PYTHONPATH"] = (
        str(repo_src) + os.pathsep + env.get("PYTHONPATH", "")
        if env.get("PYTHONPATH")
        else str(repo_src)
    )
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_cli_main_does_not_eagerly_import_undo_or_history() -> None:
    """Importing cli.main must not pull undo.rollback / history.tracker."""
    probe = _run_probe(
        """
        import sys
        import cli.main  # noqa: F401

        heavy = sorted(
            m for m in sys.modules
            if m.startswith(("undo.rollback", "undo.undo_manager", "undo.viewer",
                              "history.tracker", "history.database"))
        )
        print("HEAVY:" + ",".join(heavy))
        """
    )
    assert probe.returncode == 0, probe.stderr
    # No undo.rollback / undo.undo_manager / undo.viewer / history.tracker loaded
    assert "HEAVY:\n" in probe.stdout or probe.stdout.strip() == "HEAVY:", (
        f"unexpected eager imports of project heavy modules: {probe.stdout!r}"
    )


def test_import_undo_package_alone_does_not_pull_rollback() -> None:
    """``import undo`` must NOT trigger imports of undo.rollback / undo.viewer / history.tracker."""
    probe = _run_probe(
        """
        import sys
        import undo  # noqa: F401

        for mod in ("undo.rollback", "undo.undo_manager", "undo.viewer",
                    "history.tracker", "history.database"):
            print(f"{mod}:" + ("yes" if mod in sys.modules else "no"))
        """
    )
    assert probe.returncode == 0, probe.stderr
    for mod in _HEAVY_MODULES:
        assert f"{mod}:no" in probe.stdout, (
            f"{mod} was eagerly imported on `import undo`: {probe.stdout!r}"
        )


def test_lazy_attribute_access_still_works() -> None:
    """``from undo import RollbackExecutor`` resolves correctly via PEP 562."""
    probe = _run_probe(
        """
        from undo import RollbackExecutor, UndoManager, ValidationResult
        print("OK:" + RollbackExecutor.__name__ + "," + UndoManager.__name__
              + "," + ValidationResult.__name__)
        """
    )
    assert probe.returncode == 0, probe.stderr
    assert "OK:RollbackExecutor,UndoManager,ValidationResult" in probe.stdout


def test_unknown_attribute_raises_attribute_error() -> None:
    """``from undo import nonexistent`` raises a clean AttributeError."""
    probe = _run_probe(
        """
        try:
            from undo import nonexistent_thing  # noqa: F401
        except ImportError as exc:
            print(f"OK:{exc}")
        else:
            print("FAIL: import succeeded")
        """
    )
    assert probe.returncode == 0, probe.stderr
    assert "OK:" in probe.stdout
    assert "nonexistent_thing" in probe.stdout
