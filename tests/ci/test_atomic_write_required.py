"""Tests for the atomic-write rail (``scripts/check_atomic_write.py``).

The rail blocks regressions of the persistent-state-write hardening landed in
PRs #176, #195, #197, #203, #204. Any new ``Path.write_text``,
``Path.write_bytes``, or ``open(p, "w"|"wb"|"a"|"ab")`` call in ``src/`` must
either use the ``utils.atomic_write`` helpers or carry an explicit
``# atomic-write: ok — <reason>`` opt-out comment.

The detector is AST-based (codex r219 #1 + #2) — regex-on-raw-lines failed
to handle nested parentheses in ``open(Path(name).with_suffix('.json'), 'w')``
and false-flagged forbidden patterns inside string literals.

T10 predicate negative-case backfill: every exemption category has a positive
test (the rail flags the surface shape) AND a negative test (the rail does
NOT flag when the marker / file allowlist applies / the pattern is inside a
string literal).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_atomic_write.py"

# Import the detector directly so we can unit-test helpers without spawning
# a subprocess for every case.
sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_atomic_write import (  # noqa: E402
    _ALLOWLISTED_FILES,
    _FORBIDDEN_MODES,
    _has_opt_out_in_window,
    find_violations,
)


def _synth(tmp_path: Path, content: str) -> Path:
    """Write *content* to a synthetic Python file under ``tmp_path/src/x/mod.py``."""
    src = tmp_path / "src" / "x"
    src.mkdir(parents=True)
    target = src / "mod.py"
    target.write_text(content)
    return target


# ---------------------------------------------------------------------------
# Unit tests: forbidden mode set
# ---------------------------------------------------------------------------


class TestForbiddenModes:
    """The mode set defines what counts as a write/append open()."""

    @pytest.mark.parametrize("mode", ["w", "wb", "a", "ab", "w+", "wb+", "a+", "ab+"])
    def test_write_modes_in_set(self, mode: str) -> None:
        """Write modes in set."""
        assert mode in _FORBIDDEN_MODES

    @pytest.mark.parametrize("mode", ["r", "rb", "r+", "rb+"])
    def test_read_modes_not_in_set(self, mode: str) -> None:
        """Read modes not in set."""
        assert mode not in _FORBIDDEN_MODES


# ---------------------------------------------------------------------------
# Unit tests: file-level allowlist
# ---------------------------------------------------------------------------


class TestAllowlist:
    """Files that own the atomic-write primitives are exempt."""

    def test_atomic_write_module_is_allowlisted(self) -> None:
        """Atomic write module is allowlisted."""
        assert "src/utils/atomic_write.py" in _ALLOWLISTED_FILES

    def test_atomic_io_module_is_allowlisted(self) -> None:
        """Atomic io module is allowlisted."""
        assert "src/utils/atomic_io.py" in _ALLOWLISTED_FILES

    def test_arbitrary_src_file_is_not_allowlisted(self) -> None:
        """Arbitrary src file is not allowlisted."""
        assert "src/services/intelligence/preference_store.py" not in _ALLOWLISTED_FILES


# ---------------------------------------------------------------------------
# Unit tests: marker window detection
# ---------------------------------------------------------------------------


class TestMarkerWindow:
    """Markers inside the ±N-line window around the call line are recognised."""

    def test_marker_on_call_line_is_recognised(self) -> None:
        """Marker on call line is recognised."""
        lines = ['p.write_text("x")  # atomic-write: ok — user output']
        assert _has_opt_out_in_window(lines, call_line=1)

    def test_marker_on_line_above_is_recognised(self) -> None:
        # New placement (codex r219 + diff-coverage fix): a standalone
        # comment line immediately above the call is accepted.
        """Marker on line above is recognised."""
        lines = [
            "# atomic-write: ok — manual temp+replace",
            'p.write_text("x")',
        ]
        assert _has_opt_out_in_window(lines, call_line=2)

    def test_marker_on_closing_paren_line_is_recognised(self) -> None:
        # ruff format splits long calls; marker may land on the )-line.
        """Marker on closing paren line is recognised."""
        lines = [
            "p.write_text(",
            '    "payload"',
            ")  # atomic-write: ok — user output",
        ]
        assert _has_opt_out_in_window(lines, call_line=1)

    def test_marker_too_far_above_is_not_recognised(self) -> None:
        """Marker too far above is not recognised."""
        lines = [
            "# atomic-write: ok",
            "x = 1",
            "x = 2",
            "x = 3",
            "x = 4",
            'p.write_text("x")',
        ]
        assert not _has_opt_out_in_window(lines, call_line=6)

    def test_marker_too_far_below_is_not_recognised(self) -> None:
        """Marker too far below is not recognised."""
        lines = ['p.write_text("x")'] + ["x = 1"] * 10 + ["# atomic-write: ok"]
        assert not _has_opt_out_in_window(lines, call_line=1)


# ---------------------------------------------------------------------------
# Unit tests: AST-based detection on synthetic files
# ---------------------------------------------------------------------------


class TestWriteTextAndBytes:
    """Both ``Path.write_text`` and ``Path.write_bytes`` are flagged."""

    def test_unmarked_write_text_is_flagged(self, tmp_path: Path) -> None:
        """Unmarked write text is flagged."""
        target = _synth(tmp_path, 'p.write_text("payload")\n')
        assert len(find_violations(target)) == 1

    def test_unmarked_write_bytes_is_flagged(self, tmp_path: Path) -> None:
        """Unmarked write bytes is flagged."""
        target = _synth(tmp_path, 'p.write_bytes(b"\\x00")\n')
        assert len(find_violations(target)) == 1

    def test_marked_write_text_is_not_flagged(self, tmp_path: Path) -> None:
        """Marked write text is not flagged."""
        target = _synth(
            tmp_path,
            'p.write_text("payload")  # atomic-write: ok — manual temp+replace\n',
        )
        assert find_violations(target) == []

    def test_write_text_with_marker_above_is_not_flagged(self, tmp_path: Path) -> None:
        # New placement: marker on the line immediately above the call.
        """Write text with marker above is not flagged."""
        target = _synth(
            tmp_path,
            '# atomic-write: ok — manual temp+replace\np.write_text("payload")\n',
        )
        assert find_violations(target) == []


class TestOpenCalls:
    """``open()`` is flagged only for write/append modes."""

    @pytest.mark.parametrize("mode", ["w", "wb", "a", "ab"])
    def test_write_modes_are_flagged(self, mode: str, tmp_path: Path) -> None:
        """Write modes are flagged."""
        target = _synth(tmp_path, f'with open(p, "{mode}") as f:\n    f.write("x")\n')
        assert len(find_violations(target)) == 1

    @pytest.mark.parametrize("mode", ["r", "rb"])
    def test_read_modes_are_not_flagged(self, mode: str, tmp_path: Path) -> None:
        # T10 negative case: same call shape, read mode → not flagged.
        """Read modes are not flagged."""
        target = _synth(tmp_path, f'with open(p, "{mode}") as f:\n    f.read()\n')
        assert find_violations(target) == []

    def test_mode_keyword_argument_is_recognised(self, tmp_path: Path) -> None:
        """Mode keyword argument is recognised."""
        target = _synth(tmp_path, 'with open(p, mode="w") as f:\n    f.write("x")\n')
        assert len(find_violations(target)) == 1

    def test_open_with_nested_call_in_first_arg_is_flagged(self, tmp_path: Path) -> None:
        # Codex r219 #1 — the regex-based predecessor stopped at the first
        # ``)`` so this shape silently bypassed the rail. The AST detector
        # walks the call structure and sees the mode regardless of how
        # nested the path expression is.
        """Open with nested call in first arg is flagged."""
        target = _synth(
            tmp_path,
            'with open(Path(name).with_suffix(".json"), "w") as f:\n    f.write("x")\n',
        )
        assert len(find_violations(target)) == 1

    def test_method_named_open_is_not_flagged(self, tmp_path: Path) -> None:
        # T10 negative case: ``self.open(...)`` is a method call, not the
        # builtin. The AST detector matches only ``ast.Name(id='open')``.
        """Method named open is not flagged."""
        target = _synth(tmp_path, 'self.open(p, "w")\n')
        assert find_violations(target) == []

    def test_dynamic_mode_is_not_flagged(self, tmp_path: Path) -> None:
        # The mode is a variable reference; we can't statically determine
        # if it's a write or read mode, so we conservatively skip rather
        # than false-flag. ``mode`` could be either.
        """Dynamic mode is not flagged."""
        target = _synth(tmp_path, "with open(p, mode) as f:\n    pass\n")
        assert find_violations(target) == []


class TestStringLiteralFalsePositives:
    """Forbidden patterns inside string literals must NOT be flagged.

    Codex r219 #2: regex-on-raw-lines flagged ``logger.debug("open(path, 'w')")``
    even though the ``open(...)`` is inside a string literal, not a real
    call. The AST detector only matches actual ``ast.Call`` nodes.
    """

    def test_open_inside_string_literal_not_flagged(self, tmp_path: Path) -> None:
        """Open inside string literal not flagged."""
        target = _synth(
            tmp_path,
            "logger.debug(\"open(path, 'w')\")\n",
        )
        assert find_violations(target) == []

    def test_write_text_inside_string_literal_not_flagged(self, tmp_path: Path) -> None:
        """Write text inside string literal not flagged."""
        target = _synth(
            tmp_path,
            'message = "use path.write_text(content) for atomic writes"\n',
        )
        assert find_violations(target) == []

    def test_open_in_docstring_not_flagged(self, tmp_path: Path) -> None:
        """Open in docstring not flagged."""
        target = _synth(
            tmp_path,
            '"""Module docstring.\n\nExample::\n\n    open(p, "w")\n"""\n',
        )
        assert find_violations(target) == []


class TestMultilineOpen:
    """``open()`` calls split across lines by ruff format are still detected."""

    def test_multiline_open_without_marker_is_flagged(self, tmp_path: Path) -> None:
        """Multiline open without marker is flagged."""
        target = _synth(
            tmp_path,
            'with open(\n    p,\n    "w",\n) as f:\n    f.write("x")\n',
        )
        assert len(find_violations(target)) == 1

    def test_multiline_open_marker_on_closing_paren_is_not_flagged(self, tmp_path: Path) -> None:
        """Multiline open marker on closing paren is not flagged."""
        target = _synth(
            tmp_path,
            'with open(\n    p,\n    "w",\n) as f:  # atomic-write: ok — user output\n'
            '    f.write("x")\n',
        )
        assert find_violations(target) == []

    def test_multiline_open_marker_above_is_not_flagged(self, tmp_path: Path) -> None:
        """Multiline open marker above is not flagged."""
        target = _synth(
            tmp_path,
            "# atomic-write: ok — user output\n"
            'with open(\n    p,\n    "w",\n) as f:\n    f.write("x")\n',
        )
        assert find_violations(target) == []


class TestSyntaxError:
    """A file that fails to parse yields zero violations (rail bows out)."""

    def test_syntax_error_yields_no_violations(self, tmp_path: Path) -> None:
        """Syntax error yields no violations."""
        target = _synth(tmp_path, "def broken(\n")
        assert find_violations(target) == []


# ---------------------------------------------------------------------------
# Full-suite assertion: no unmarked violations on the current branch
# ---------------------------------------------------------------------------


class TestFullSuite:
    """The rail must pass against the live ``src/`` tree."""

    def test_no_unmarked_violations_on_current_tree(self) -> None:
        """No unmarked violations on current tree."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"atomic-write rail flagged unmarked violation(s):\n{result.stderr}"
        )
