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
    _collect_marker_comment_lines,
    _has_opt_out_in_window,
    _mode_is_forbidden,
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
    """The mode-classifier flags every write/append/exclusive-create form."""

    @pytest.mark.parametrize(
        "mode",
        [
            # Canonical forms enumerated in _FORBIDDEN_MODES
            "w",
            "wb",
            "wt",
            "a",
            "ab",
            "at",
            "x",
            "xb",
            "xt",
            "w+",
            "wb+",
            "w+b",
            "a+",
            "ab+",
            "a+b",
            "x+",
            # Codex r219 #3 — alias / mixed-flag forms that bypassed the
            # original literal set.
            "wt+",
            "w+t",
            "at+",
            "a+t",
            "xb+",
            "x+b",
        ],
    )
    def test_write_modes_classified_forbidden(self, mode: str) -> None:
        """Every write/append/exclusive form must be classified forbidden."""
        assert _mode_is_forbidden(mode)

    @pytest.mark.parametrize("mode", ["r", "rb", "rt", "r+", "rb+", "r+b", "rt+", "r+t"])
    def test_read_modes_classified_allowed(self, mode: str) -> None:
        """Pure read modes (no w/a/x) must be classified allowed."""
        assert not _mode_is_forbidden(mode)

    @pytest.mark.parametrize("mode", ["w", "wb", "a", "ab", "w+", "wb+", "a+", "ab+"])
    def test_canonical_modes_in_legacy_set(self, mode: str) -> None:
        """The legacy ``_FORBIDDEN_MODES`` set still contains the canonical strings."""
        assert mode in _FORBIDDEN_MODES


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
    """Markers inside the -2 / +6 line window around the call line are recognised.

    Each test sets up a synthetic source string, runs it through
    ``_collect_marker_comment_lines`` (which tokenises and only collects real
    comment-token markers), then asks ``_has_opt_out_in_window`` whether the
    call line falls inside any marker's window.
    """

    def _check(self, source: str, call_line: int) -> bool:
        """Return ``_has_opt_out_in_window`` result for *source* at *call_line*."""
        lines = source.splitlines()
        marker_lines = _collect_marker_comment_lines(source)
        return _has_opt_out_in_window(marker_lines, call_line, len(lines))

    def test_marker_on_call_line_is_recognised(self) -> None:
        """Marker on call line is recognised."""
        source = 'p.write_text("x")  # atomic-write: ok — user output\n'
        assert self._check(source, call_line=1)

    def test_marker_on_line_above_is_recognised(self) -> None:
        """Standalone comment line immediately above the call is accepted (codex r219 + diff-coverage fix)."""
        source = '# atomic-write: ok — manual temp+replace\np.write_text("x")\n'
        assert self._check(source, call_line=2)

    def test_marker_on_closing_paren_line_is_recognised(self) -> None:
        """ruff format splits long calls; marker may land on the )-line."""
        source = 'p.write_text(\n    "payload"\n)  # atomic-write: ok — user output\n'
        assert self._check(source, call_line=1)

    def test_marker_too_far_above_is_not_recognised(self) -> None:
        """Marker too far above is not recognised."""
        source = "# atomic-write: ok\nx = 1\nx = 2\nx = 3\nx = 4\np.write_text('x')\n"
        assert not self._check(source, call_line=6)

    def test_marker_too_far_below_is_not_recognised(self) -> None:
        """Marker too far below is not recognised."""
        source = "p.write_text('x')\n" + ("x = 1\n" * 10) + "# atomic-write: ok\n"
        assert not self._check(source, call_line=1)

    def test_marker_inside_string_literal_is_ignored(self) -> None:
        """Marker text inside a string literal must NOT exempt a real call.

        CodeRabbit r219 #2: the previous regex-on-raw-lines marker check
        treated ``msg = "# atomic-write: ok"`` as an opt-out, which would
        let any forbidden write within the window through. The tokeniser
        only emits ``COMMENT`` tokens for real comments, so the string
        literal is filtered out.
        """
        source = 'msg = "# atomic-write: ok — bypass attempt"\np.write_text("payload")\n'
        assert not self._check(source, call_line=2)

    def test_marker_inside_string_literal_does_not_match_via_collector(self) -> None:
        """``_collect_marker_comment_lines`` ignores string literals containing the marker."""
        source = 'msg = "# atomic-write: ok"\n'
        assert _collect_marker_comment_lines(source) == set()


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

    @pytest.mark.parametrize(
        "mode",
        [
            "w",
            "wb",
            "wt",
            "a",
            "ab",
            "at",
            "x",
            "xb",
            "xt",
            "w+",
            "wb+",
            "w+b",
            "wt+",
            "w+t",
            "a+",
            "ab+",
            "a+b",
            "x+",
            "xb+",
        ],
    )
    def test_write_modes_are_flagged(self, mode: str, tmp_path: Path) -> None:
        """Every write/append/exclusive mode (including alias forms) is flagged.

        Codex r219 #3: the previous literal-set check silently let
        ``"wt"``, ``"at"``, ``"w+b"``, ``"a+t"``, etc. bypass the rail.
        """
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

    def test_method_call_open_with_write_mode_is_flagged(self, tmp_path: Path) -> None:
        """Codex r219 #5: ``Path("out.json").open("w")`` is also a forbidden write.

        The detector now matches both ``open(...)`` (builtin Name) and
        ``<obj>.open(...)`` (Attribute call). Bare-method bypass closed.
        """
        target = _synth(tmp_path, 'Path("out.json").open("w")\n')
        assert len(find_violations(target)) == 1

    def test_method_call_open_with_read_mode_is_not_flagged(self, tmp_path: Path) -> None:
        """T10 negative: a method-style ``open()`` with a read mode stays unflagged."""
        target = _synth(tmp_path, 'Path("out.json").open("r")\n')
        assert find_violations(target) == []

    def test_method_call_open_without_mode_is_not_flagged(self, tmp_path: Path) -> None:
        """T10 negative: a method-style ``open()`` with no mode arg defaults to read; not flagged."""
        target = _synth(tmp_path, 'Path("out.json").open()\n')
        assert find_violations(target) == []

    def test_module_level_attribute_open_with_path_at_args0_is_flagged(
        self, tmp_path: Path
    ) -> None:
        """Codex r219 #6: ``tarfile.open(path, "w")`` and ``gzip.open(path, "wb")``.

        These are module-level functions called via attribute access. The
        path is at args[0] and the mode is at args[1] — same positional
        layout as the builtin ``open(path, mode)``. The detector must NOT
        treat args[0] (the path) as the mode.
        """
        target = _synth(
            tmp_path,
            'tarfile.open(p, "w")\ngzip.open(p, "wb")\n',
        )
        assert len(find_violations(target)) == 2

    def test_module_level_attribute_open_with_string_path_not_misread_as_mode(
        self, tmp_path: Path
    ) -> None:
        """T10 negative: ``tarfile.open("write.log", "wb")``.

        Codex r219 #6 regression case: with a literal string path that
        happens to contain the letter ``w``, the detector must still pick
        the real mode out of args[1], not misread args[0] as a mode.
        Strict mode-literal pattern (``[rwaxbt+]{1,4}`` with exactly one
        primary action char) ensures ``"write.log"`` is rejected as a
        candidate mode.
        """
        target = _synth(tmp_path, 'tarfile.open("write.log", "wb")\n')
        violations = find_violations(target)
        assert len(violations) == 1
        # The violation should be on the call line itself (line 1), not
        # silently dropped by the path being misread as a mode.

    def test_module_level_attribute_open_read_mode_not_flagged(self, tmp_path: Path) -> None:
        """``tarfile.open(path, "r")`` is a read; not flagged."""
        target = _synth(tmp_path, 'tarfile.open(p, "r")\n')
        assert find_violations(target) == []

    def test_open_with_mode_like_path_string_at_args0_uses_args1(self, tmp_path: Path) -> None:
        """Codex r219 #7: ``open("r", "w")`` — args[0] looks like read but real mode is "w" at args[1].

        Previous "first-matching-wins" returned ``"r"`` (read) and silently
        let the write through. The position-priority logic (args[1] before
        args[0] when both are present) returns ``"w"`` and flags it.
        """
        target = _synth(tmp_path, 'open("r", "w")\n')
        assert len(find_violations(target)) == 1

    def test_open_with_append_like_path_and_read_mode_not_falsely_flagged(
        self, tmp_path: Path
    ) -> None:
        """Codex r219 #7: ``open("a", "r")`` — args[0] is path ``"a"``, args[1] ``"r"`` is the mode.

        The position priority avoids the false-positive that
        "first-matching-wins" produced when args[0] looked like an
        append-mode literal.
        """
        target = _synth(tmp_path, 'open("a", "r")\n')
        assert find_violations(target) == []

    def test_path_open_with_buffering_positional_still_extracts_mode(self, tmp_path: Path) -> None:
        """``Path("x").open("w", -1)`` — args[1] (buffering) isn't a mode; fall back to args[0]."""
        target = _synth(tmp_path, 'Path("x").open("w", -1)\n')
        assert len(find_violations(target)) == 1

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

    def test_marker_inside_string_literal_does_not_exempt_real_call(self, tmp_path: Path) -> None:
        """End-to-end CodeRabbit r219 case: a real write call within window of a string-literal marker IS flagged.

        Before the tokeniser fix, ``msg = "# atomic-write: ok"`` near a real
        ``open(p, "w")`` would silently exempt the write. The tokeniser-based
        marker collector ignores string literals, so the rail correctly flags
        the write.
        """
        target = _synth(
            tmp_path,
            'msg = "# atomic-write: ok — bypass attempt"\n'
            'with open(p, "w") as f:\n'
            '    f.write("data")\n',
        )
        violations = find_violations(target)
        assert len(violations) == 1

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
