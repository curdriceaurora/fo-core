"""Tests for the G3 rail (``scripts/check_cli_path_validation.py``).

G3 blocks CLI commands that accept a ``Path`` argument without routing
it through ``resolve_cli_path()`` / ``validate_pair()`` /
``validate_within_roots()`` (or a ``_validate_*`` wrapper).

See Epic A.cli (hardening roadmap #154 §2.5) for the motivating
invariant: every user-supplied path argument must hit the CLI-boundary
validator before any service-layer code sees it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_cli_path_validation.py"

sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_cli_path_validation import (  # noqa: E402
    _is_command_decorator,
    _is_path_annotation,
    find_violations,
)


class TestPathAnnotationRecognition:
    """_is_path_annotation must recognize the annotation variants that
    CLI code actually uses."""

    def test_bare_path(self) -> None:
        import ast

        node = ast.parse("def f(p: Path): ...").body[0].args.args[0].annotation
        assert _is_path_annotation(node)

    def test_path_or_none(self) -> None:
        import ast

        node = ast.parse("def f(p: Path | None): ...").body[0].args.args[0].annotation
        assert _is_path_annotation(node)

    def test_optional_path(self) -> None:
        import ast

        node = ast.parse("def f(p: Optional[Path]): ...").body[0].args.args[0].annotation
        assert _is_path_annotation(node)

    def test_non_path_rejected(self) -> None:
        """T10 surface negative: str looks like a path but isn't Path typed."""
        import ast

        node = ast.parse("def f(p: str): ...").body[0].args.args[0].annotation
        assert not _is_path_annotation(node)

    def test_int_rejected(self) -> None:
        import ast

        node = ast.parse("def f(p: int): ...").body[0].args.args[0].annotation
        assert not _is_path_annotation(node)


class TestCommandDecoratorRecognition:
    """_is_command_decorator matches ``@<x>.command(...)`` and its
    siblings, not other decorators."""

    def test_with_parens(self) -> None:
        import ast

        dec = ast.parse("@app.command()\ndef f(): ...").body[0].decorator_list[0]
        assert _is_command_decorator(dec)

    def test_with_args(self) -> None:
        import ast

        dec = ast.parse('@app.command(name="hardware")\ndef f(): ...').body[0].decorator_list[0]
        assert _is_command_decorator(dec)

    def test_pytest_fixture_rejected(self) -> None:
        """T10 surface negative: ``@pytest.fixture`` has a similar shape
        but is not a command decorator."""
        import ast

        dec = ast.parse("@pytest.fixture\ndef f(): ...").body[0].decorator_list[0]
        assert not _is_command_decorator(dec)

    def test_callback_rejected(self) -> None:
        """T10 surface negative: ``@app.callback()`` is structurally
        similar but different."""
        import ast

        dec = ast.parse("@app.callback()\ndef f(): ...").body[0].decorator_list[0]
        assert not _is_command_decorator(dec)


class TestFindViolations:
    """End-to-end ``find_violations`` against synthetic CLI files."""

    def test_flags_unvalidated_path_param(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path
                import typer
                app = typer.Typer()

                @app.command()
                def show(file: Path) -> None:
                    print(file.read_text())
                """
            )
        )
        violations = find_violations(f)
        assert len(violations) == 1
        _lineno, func_name, param_name = violations[0]
        assert func_name == "show"
        assert param_name == "file"

    def test_accepts_resolve_cli_path_call(self, tmp_path: Path) -> None:
        f = tmp_path / "ok.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path
                import typer
                from cli.path_validation import resolve_cli_path
                app = typer.Typer()

                @app.command()
                def show(file: Path) -> None:
                    file = resolve_cli_path(file)
                    print(file.read_text())
                """
            )
        )
        assert find_violations(f) == []

    def test_accepts_validate_pair_call(self, tmp_path: Path) -> None:
        f = tmp_path / "pair.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path
                import typer
                from cli.path_validation import validate_pair
                app = typer.Typer()

                @app.command()
                def organize(input_dir: Path, output_dir: Path) -> None:
                    validate_pair(input_dir, output_dir)
                """
            )
        )
        assert find_violations(f) == []

    def test_accepts_validator_helper(self, tmp_path: Path) -> None:
        """Helpers named ``_validate_*`` are trusted as validators
        (they're expected to wrap the primary validator)."""
        f = tmp_path / "helper.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path
                import typer
                app = typer.Typer()

                def _validate_compare(p):
                    return p

                @app.command()
                def run(compare_path: Path | None = None) -> None:
                    compare_path = _validate_compare(compare_path)
                """
            )
        )
        assert find_violations(f) == []

    def test_accepts_noqa_marker(self, tmp_path: Path) -> None:
        f = tmp_path / "noqa.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path
                import typer
                app = typer.Typer()

                @app.command()  # noqa: G3 (display-only)
                def describe(p: Path) -> None:
                    print(p)
                """
            )
        )
        assert find_violations(f) == []

    def test_non_path_params_not_flagged(self, tmp_path: Path) -> None:
        """T10 surface negative: str / int params should not be flagged
        even without any validator call."""
        f = tmp_path / "non_path.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path
                import typer
                app = typer.Typer()

                @app.command()
                def search(query: str, limit: int) -> None:
                    print(query, limit)
                """
            )
        )
        assert find_violations(f) == []

    def test_non_command_function_ignored(self, tmp_path: Path) -> None:
        """T10 surface negative: a plain function with a Path parameter
        is not a CLI entry point — must not be flagged."""
        f = tmp_path / "plain.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path

                def helper(p: Path) -> None:
                    print(p)
                """
            )
        )
        assert find_violations(f) == []


class TestFullCliEnforcement:
    """The real ``src/cli/`` tree must pass the rail."""

    def test_cli_dir_is_clean(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=_FO_ROOT,
            check=False,
        )
        assert result.returncode == 0, (
            f"G3 rail reported unvalidated Path parameters in src/cli/:\n\n{result.stderr}"
        )


class TestNestedScopeIsolation:
    """A validator call that lives inside a nested ``def``/``class``/
    ``lambda`` MUST NOT satisfy the rail for an unrelated outer-scope
    Path parameter. Regression for codex P2 finding on PR #184: the
    visitor walked every Call node in the body including those inside
    dead inner functions, so an unused helper that called
    ``resolve_cli_path(p)`` would make the outer command appear to
    validate ``p`` even when the outer body never actually did.
    """

    def test_nested_def_does_not_satisfy_outer_param(self, tmp_path: Path) -> None:
        f = tmp_path / "nested_def.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path
                import typer
                from cli.path_validation import resolve_cli_path
                app = typer.Typer()

                @app.command()
                def show(file: Path) -> None:
                    def _unused_helper():
                        # validator call lives in a nested function that is
                        # never invoked — must NOT satisfy the rail for `file`
                        return resolve_cli_path(file)
                    print(file.read_text())
                """
            )
        )
        violations = find_violations(f)
        assert len(violations) == 1
        _lineno, func_name, param_name = violations[0]
        assert func_name == "show"
        assert param_name == "file"

    def test_nested_class_does_not_satisfy_outer_param(self, tmp_path: Path) -> None:
        f = tmp_path / "nested_class.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path
                import typer
                from cli.path_validation import resolve_cli_path
                app = typer.Typer()

                @app.command()
                def show(file: Path) -> None:
                    class _Inner:
                        def run(self):
                            return resolve_cli_path(file)
                    print(file.read_text())
                """
            )
        )
        violations = find_violations(f)
        assert len(violations) == 1
        _lineno, func_name, param_name = violations[0]
        assert func_name == "show"
        assert param_name == "file"

    def test_lambda_body_does_not_satisfy_outer_param(self, tmp_path: Path) -> None:
        f = tmp_path / "nested_lambda.py"
        f.write_text(
            dedent(
                """
                from pathlib import Path
                import typer
                from cli.path_validation import resolve_cli_path
                app = typer.Typer()

                @app.command()
                def show(file: Path) -> None:
                    _ = lambda: resolve_cli_path(file)
                    print(file.read_text())
                """
            )
        )
        violations = find_violations(f)
        assert len(violations) == 1
        _lineno, func_name, param_name = violations[0]
        assert func_name == "show"
        assert param_name == "file"
