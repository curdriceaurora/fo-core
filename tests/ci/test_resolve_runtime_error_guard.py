"""Tests for the F11-resolve rail (``scripts/check_resolve_runtime_error.py``).

The rail blocks regressions of the symlink-loop / non-existent-path defence
pattern from PRs #168, #173, and #195: any ``.resolve()`` call inside a
``try`` block whose ``except`` clauses do not cover ``RuntimeError`` is
flagged.

``Path.resolve()`` raises ``RuntimeError`` on Python < 3.13 for symlink loops
and ``OSError`` on Python >= 3.13. The canonical wrapper
``src/cli/path_validation.py`` already handles both; this rail prevents other
``try`` blocks from silently dropping the ``RuntimeError`` case.

T10 predicate negative-case coverage: every detection path has a positive test
(the rail flags the pattern) AND a negative test (the rail does NOT flag when
the guard, allowlist, or opt-out applies).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_resolve_runtime_error.py"

sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_resolve_runtime_error import (  # noqa: E402
    _ALLOWLISTED_EXACT,
    _ALLOWLISTED_PREFIXES,
    _exc_type_covers_runtime_error,
    _try_covers_runtime_error,
    find_violations,
)


def _synth(tmp_path: Path, content: str) -> Path:
    """Write *content* to a synthetic Python file outside ``src/``.

    Placing the file outside ``src/`` ensures the allowlist logic for
    known-safe paths (``src/utils/``, ``src/cli/path_validation.py``) does not
    interfere with synthetic-file unit tests.
    """
    src = tmp_path / "synth" / "mod.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(content, encoding="utf-8")
    return src


# ---------------------------------------------------------------------------
# Unit tests: exception-type coverage helpers
# ---------------------------------------------------------------------------


class TestExcTypeCoverage:
    """``_exc_type_covers_runtime_error`` recognises all covering forms."""

    def test_runtime_error_name_is_covering(self) -> None:
        import ast

        node = ast.parse("try:\n    pass\nexcept RuntimeError:\n    pass").body[0].handlers[0].type
        assert _exc_type_covers_runtime_error(node)

    def test_exception_name_is_covering(self) -> None:
        import ast

        node = ast.parse("try:\n    pass\nexcept Exception:\n    pass").body[0].handlers[0].type
        assert _exc_type_covers_runtime_error(node)

    def test_base_exception_name_is_covering(self) -> None:
        import ast

        node = ast.parse("try:\n    pass\nexcept BaseException:\n    pass").body[0].handlers[0].type
        assert _exc_type_covers_runtime_error(node)

    def test_tuple_with_runtime_error_is_covering(self) -> None:
        import ast

        node = (
            ast.parse("try:\n    pass\nexcept (ValueError, RuntimeError):\n    pass")
            .body[0]
            .handlers[0]
            .type
        )
        assert _exc_type_covers_runtime_error(node)

    def test_tuple_with_oserror_only_is_not_covering(self) -> None:
        """T10 negative: a tuple containing only OSError does not cover RuntimeError."""
        import ast

        node = (
            ast.parse("try:\n    pass\nexcept (ValueError, OSError):\n    pass")
            .body[0]
            .handlers[0]
            .type
        )
        assert not _exc_type_covers_runtime_error(node)

    def test_os_error_alone_is_not_covering(self) -> None:
        """T10 negative: OSError does not cover RuntimeError on Python < 3.13."""
        import ast

        node = ast.parse("try:\n    pass\nexcept OSError:\n    pass").body[0].handlers[0].type
        assert not _exc_type_covers_runtime_error(node)

    def test_value_error_alone_is_not_covering(self) -> None:
        """T10 negative: ValueError alone does not cover RuntimeError."""
        import ast

        node = ast.parse("try:\n    pass\nexcept ValueError:\n    pass").body[0].handlers[0].type
        assert not _exc_type_covers_runtime_error(node)


class TestTryCoverage:
    """``_try_covers_runtime_error`` evaluates full Try nodes correctly."""

    def test_bare_except_covers(self) -> None:
        import ast

        try_node = ast.parse("try:\n    pass\nexcept:\n    pass").body[0]
        assert _try_covers_runtime_error(try_node)

    def test_runtime_error_handler_covers(self) -> None:
        import ast

        try_node = ast.parse("try:\n    pass\nexcept RuntimeError:\n    pass").body[0]
        assert _try_covers_runtime_error(try_node)

    def test_tuple_handler_with_runtime_error_covers(self) -> None:
        import ast

        try_node = ast.parse(
            "try:\n    pass\nexcept (ValueError, RuntimeError, OSError):\n    pass"
        ).body[0]
        assert _try_covers_runtime_error(try_node)

    def test_os_error_only_handler_does_not_cover(self) -> None:
        """T10 negative: except OSError only — does not cover RuntimeError."""
        import ast

        try_node = ast.parse("try:\n    pass\nexcept OSError:\n    pass").body[0]
        assert not _try_covers_runtime_error(try_node)

    def test_value_error_only_handler_does_not_cover(self) -> None:
        """T10 negative: except ValueError only — does not cover RuntimeError."""
        import ast

        try_node = ast.parse("try:\n    pass\nexcept ValueError:\n    pass").body[0]
        assert not _try_covers_runtime_error(try_node)


# ---------------------------------------------------------------------------
# Unit tests: violation detection on synthetic files
# ---------------------------------------------------------------------------


class TestResolveInsideTry:
    """Core detection: .resolve() in a try body without RuntimeError is flagged."""

    def test_oserror_only_guard_is_flagged(self, tmp_path: Path) -> None:
        """A try/except OSError without RuntimeError is a violation."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        return p.resolve()
                    except OSError:
                        return None
            """),
        )
        violations = find_violations(target)
        assert len(violations) == 1
        assert "resolve" in violations[0][1]

    def test_value_error_only_guard_is_flagged(self, tmp_path: Path) -> None:
        """A try/except ValueError without RuntimeError is a violation."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        return p.resolve()
                    except ValueError:
                        return None
            """),
        )
        assert len(find_violations(target)) == 1

    def test_runtime_error_guard_is_not_flagged(self, tmp_path: Path) -> None:
        """T10 negative: try/except RuntimeError — no violation."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        return p.resolve()
                    except RuntimeError:
                        return None
            """),
        )
        assert find_violations(target) == []

    def test_exception_broad_guard_is_not_flagged(self, tmp_path: Path) -> None:
        """T10 negative: except Exception covers RuntimeError — no violation."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        return p.resolve()
                    except Exception:
                        return None
            """),
        )
        assert find_violations(target) == []

    def test_bare_except_is_not_flagged(self, tmp_path: Path) -> None:
        """T10 negative: bare except covers everything — no violation."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        return p.resolve()
                    except:
                        return None
            """),
        )
        assert find_violations(target) == []

    def test_tuple_with_runtime_error_is_not_flagged(self, tmp_path: Path) -> None:
        """T10 negative: (ValueError, RuntimeError, OSError) covers it — no violation."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        return p.resolve()
                    except (ValueError, RuntimeError, OSError):
                        return None
            """),
        )
        assert find_violations(target) == []

    def test_resolve_outside_any_try_is_not_flagged(self, tmp_path: Path) -> None:
        """T10 negative: .resolve() with no enclosing try — not in scope of this rail."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    return p.resolve()
            """),
        )
        assert find_violations(target) == []

    def test_resolve_in_except_handler_is_not_flagged(self, tmp_path: Path) -> None:
        """T10 negative: .resolve() inside an except handler body — not guarded by outer try."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        x = 1
                    except ValueError:
                        return p.resolve()
            """),
        )
        assert find_violations(target) == []

    def test_resolve_in_finally_is_not_flagged(self, tmp_path: Path) -> None:
        """T10 negative: .resolve() in finally is not covered by the try's except."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        x = 1
                    except ValueError:
                        pass
                    finally:
                        return p.resolve()
            """),
        )
        assert find_violations(target) == []


class TestNestedTry:
    """Nested try blocks: innermost enclosing try is the relevant one."""

    def test_inner_try_covers_outer_does_not(self, tmp_path: Path) -> None:
        """T10 negative: inner try covers RuntimeError even if outer doesn't."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        try:
                            return p.resolve()
                        except (RuntimeError, OSError):
                            return None
                    except ValueError:
                        return None
            """),
        )
        assert find_violations(target) == []

    def test_outer_covers_but_inner_does_not(self, tmp_path: Path) -> None:
        """Outer try covers RuntimeError but inner (innermost) does not — violation."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        try:
                            return p.resolve()
                        except ValueError:
                            return None
                    except RuntimeError:
                        return None
            """),
        )
        violations = find_violations(target)
        assert len(violations) == 1


# ---------------------------------------------------------------------------
# Unit tests: opt-out marker
# ---------------------------------------------------------------------------


class TestOptOutMarker:
    """``# noqa: F11-resolve`` exempts the call from the rail."""

    def test_noqa_on_call_line_exempts(self, tmp_path: Path) -> None:
        """Marker on the same line as the call exempts it."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        return p.resolve()  # noqa: F11-resolve — caller handles it
                    except OSError:
                        return None
            """),
        )
        assert find_violations(target) == []

    def test_noqa_one_line_above_exempts(self, tmp_path: Path) -> None:
        """Marker on the line immediately above the call exempts it."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        # noqa: F11-resolve — generator consumed by outer try
                        return p.resolve()
                    except OSError:
                        return None
            """),
        )
        assert find_violations(target) == []

    def test_noqa_two_lines_above_exempts(self, tmp_path: Path) -> None:
        """Marker two lines above the call still exempts it."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        # noqa: F11-resolve — two-line lookback
                        x = 1
                        return p.resolve()
                    except OSError:
                        return None
            """),
        )
        assert find_violations(target) == []

    def test_noqa_three_lines_above_does_not_exempt(self, tmp_path: Path) -> None:
        """T10 negative: marker three lines above is outside the window."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        # noqa: F11-resolve — too far above
                        x = 1
                        y = 2
                        return p.resolve()
                    except OSError:
                        return None
            """),
        )
        assert len(find_violations(target)) == 1

    def test_noqa_inside_string_does_not_exempt(self, tmp_path: Path) -> None:
        """T10 negative: marker text inside a string literal cannot bypass the rail."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    msg = "noqa: F11-resolve"
                    try:
                        return p.resolve()
                    except OSError:
                        return None
            """),
        )
        assert len(find_violations(target)) == 1


# ---------------------------------------------------------------------------
# Unit tests: allowlist
# ---------------------------------------------------------------------------


class TestAllowlist:
    """Allowlisted files and directories are never flagged."""

    def test_path_validation_py_is_allowlisted(self) -> None:
        """src/cli/path_validation.py is in the exact allowlist."""
        assert "src/cli/path_validation.py" in _ALLOWLISTED_EXACT

    def test_utils_prefix_is_allowlisted(self) -> None:
        """src/utils/ is in the prefix allowlist."""
        assert any(p == "src/utils/" for p in _ALLOWLISTED_PREFIXES)

    def test_allowlisted_exact_file_is_not_scanned(self) -> None:
        """find_violations returns [] for the canonical path_validation module."""
        path_validation = _FO_ROOT / "src" / "cli" / "path_validation.py"
        assert path_validation.exists(), "canonical wrapper must exist"
        assert find_violations(path_validation) == []

    def test_allowlisted_prefix_file_is_not_scanned(self, tmp_path: Path) -> None:
        """A file whose relative path starts with src/utils/ is not flagged."""
        # Build a fake src/utils/ file rooted at _FO_ROOT equivalent so the
        # allowlist relative-path check fires. The allowlist check compares
        # path.relative_to(_ROOT), so we need the file under _FO_ROOT/src/utils/.
        utils_dir = _FO_ROOT / "src" / "utils"
        target = utils_dir / "_test_synth_f11_allowlist.py"
        try:
            target.write_text(
                dedent("""\
                    from pathlib import Path
                    def f(p: Path):
                        try:
                            return p.resolve()
                        except OSError:
                            return None
                """),
                encoding="utf-8",
            )
            assert find_violations(target) == []
        finally:
            target.unlink(missing_ok=True)

    def test_non_allowlisted_src_file_is_scanned(self, tmp_path: Path) -> None:
        """T10 negative: a file outside the allowlist IS flagged for violations."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        return p.resolve()
                    except OSError:
                        return None
            """),
        )
        assert len(find_violations(target)) == 1


# ---------------------------------------------------------------------------
# Unit tests: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Miscellaneous edge cases that must not crash or produce false results."""

    def test_empty_file_yields_no_violations(self, tmp_path: Path) -> None:
        """An empty Python file yields no violations."""
        target = _synth(tmp_path, "")
        assert find_violations(target) == []

    def test_syntax_error_yields_no_violations(self, tmp_path: Path) -> None:
        """A file with a syntax error yields no violations (safe default)."""
        target = _synth(tmp_path, "def f(\n")
        assert find_violations(target) == []

    def test_non_path_resolve_call_in_try_is_flagged(self, tmp_path: Path) -> None:
        """T10 negative: a ``.resolve()`` call on a non-Path object is still flagged
        because the rail is conservative (it cannot know the receiver type)."""
        target = _synth(
            tmp_path,
            dedent("""\
                def f(r):
                    try:
                        return r.resolve()
                    except OSError:
                        return None
            """),
        )
        # Conservative: any .resolve() call in an unguarded try is flagged.
        assert len(find_violations(target)) == 1

    def test_resolve_with_kwargs_in_try_is_flagged(self, tmp_path: Path) -> None:
        """`.resolve(strict=False)` is flagged the same as `.resolve()`."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path):
                    try:
                        return p.resolve(strict=False)
                    except ValueError:
                        return None
            """),
        )
        assert len(find_violations(target)) == 1

    def test_multiple_resolve_calls_both_flagged(self, tmp_path: Path) -> None:
        """Two unguarded resolve() calls in separate try blocks are both reported."""
        target = _synth(
            tmp_path,
            dedent("""\
                from pathlib import Path
                def f(p: Path, q: Path):
                    try:
                        a = p.resolve()
                    except OSError:
                        a = p
                    try:
                        b = q.resolve()
                    except ValueError:
                        b = q
                    return a, b
            """),
        )
        assert len(find_violations(target)) == 2


# ---------------------------------------------------------------------------
# Integration test: repo scan is clean
# ---------------------------------------------------------------------------


class TestRepoScanClean:
    """The real ``src/`` tree must have no F11-resolve violations."""

    def test_src_has_no_violations(self) -> None:
        """Running the detector against the real ``src/`` directory exits 0."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, "F11-resolve violations found in src/:\n" + result.stderr
