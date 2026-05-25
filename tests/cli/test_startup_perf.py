"""Startup performance regression test for ``fo version`` (issue #404).

These checks run in a subprocess so the in-process pytest collection state
doesn't pollute sys.modules with imports another test or fixture already
brought in.  PEP 562 lazy ``__getattr__`` on ``undo/__init__.py`` is what
keeps the lazy contract — if a future change accidentally adds a
module-level eager import that pulls ``undo.rollback`` /
``undo.undo_manager`` / ``undo.viewer``, the assertions below fail.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.ci]


def _run_probe(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_main_does_not_eagerly_import_undo_or_history() -> None:
    """Importing cli.main must not pull undo.rollback / history / sqlite3."""
    probe = _run_probe(
        """
        import sys
        import cli.main  # noqa: F401

        heavy = sorted(
            m for m in sys.modules
            if m.startswith(("undo.rollback", "undo.undo_manager", "undo.viewer",
                              "history.", "history"))
            and m not in ("history",)  # ``history`` package init alone is acceptable
        )
        print("HEAVY:" + ",".join(heavy))
        print("SQLITE3:" + ("yes" if "sqlite3" in sys.modules else "no"))
        """
    )
    assert probe.returncode == 0, probe.stderr
    assert "HEAVY:\n" in probe.stdout or "HEAVY:" + "\n" in probe.stdout, probe.stdout
    # No undo.rollback / undo.undo_manager / undo.viewer / history.* loaded
    assert "HEAVY:\n" in (probe.stdout + "\n") or probe.stdout.strip().endswith("HEAVY:"), (
        f"unexpected eager imports: {probe.stdout!r}"
    )
    assert "SQLITE3:no" in probe.stdout, f"sqlite3 was eagerly imported: {probe.stdout!r}"


def test_import_undo_package_alone_does_not_pull_rollback() -> None:
    """``import undo`` must NOT trigger imports of undo.rollback or undo.viewer."""
    probe = _run_probe(
        """
        import sys
        import undo  # noqa: F401

        for mod in ("undo.rollback", "undo.undo_manager", "undo.viewer", "sqlite3"):
            print(f"{mod}:" + ("yes" if mod in sys.modules else "no"))
        """
    )
    assert probe.returncode == 0, probe.stderr
    assert "undo.rollback:no" in probe.stdout
    assert "undo.undo_manager:no" in probe.stdout
    assert "undo.viewer:no" in probe.stdout
    assert "sqlite3:no" in probe.stdout


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
