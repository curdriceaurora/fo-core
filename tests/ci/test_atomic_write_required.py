"""Tests for the atomic-write rail (``scripts/check_atomic_write.py``).

The rail blocks regressions of the persistent-state-write hardening landed in
PRs #176, #195, #197, #203, #204. Any new ``Path.write_text``,
``Path.write_bytes``, or ``open(p, "w"|"wb"|"a"|"ab")`` call in ``src/`` must
either use the ``utils.atomic_write`` helpers or carry an explicit
``# atomic-write: ok — <reason>`` opt-out comment.

T10 predicate negative-case backfill: every exemption category has a positive
test (the rail flags the surface shape) AND a negative test (the rail does
NOT flag when the marker / file allowlist applies).
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
    _has_opt_out,
    _is_comment_line,
    _is_in_docstring_or_string,
    _matches_forbidden,
    find_violations,
)

# ---------------------------------------------------------------------------
# Unit tests: forbidden-pattern detection
# ---------------------------------------------------------------------------


class TestPatternDetection:
    """The pattern set must match the four forbidden write forms."""

    @pytest.mark.parametrize(
        "line",
        [
            'path.write_text("data")',
            'p.write_bytes(b"\\x00\\x01")',
            'with open(p, "w") as f:',
            'with open(p, "wb") as f:',
            'with open(p, "a") as f:',
            'with open(p, "ab") as f:',
            "fh = open(lock, 'a', encoding='utf-8')",
            'open(target, "w", encoding="utf-8")',
        ],
    )
    def test_matches_forbidden(self, line: str) -> None:
        assert _matches_forbidden(line)

    @pytest.mark.parametrize(
        "line",
        [
            # Read modes are not flagged (T10 negative cases)
            'with open(p, "r") as f:',
            'with open(p, "rb") as f:',
            "data = path.read_text()",
            "data = path.read_bytes()",
            # Method-name false friends — must not match
            "rewrite_text(payload)",
            "writer.writeheader()",
            # Fragment in identifier — not a write call
            "open_writer = make_writer()",
        ],
    )
    def test_does_not_match_non_write(self, line: str) -> None:
        assert not _matches_forbidden(line)


class TestOptOutMarker:
    """The opt-out marker must be detected only in its canonical form."""

    @pytest.mark.parametrize(
        "line",
        [
            'path.write_text("x")  # atomic-write: ok',
            'path.write_text("x")  # atomic-write: ok — user output',
            'path.write_text("x")  # atomic-write:  ok',  # extra space
            'path.write_text("x")  # atomic-write: ok (legacy single-shot)',
        ],
    )
    def test_recognizes_opt_out(self, line: str) -> None:
        assert _has_opt_out(line)

    @pytest.mark.parametrize(
        "line",
        [
            # Marker variations that must NOT count (typos / wrong prefix)
            'path.write_text("x")  # atomic-write fine',
            'path.write_text("x")  # atomic_write: ok',  # underscore not hyphen
            'path.write_text("x")  # ok — atomic write',
            'path.write_text("x")',  # no marker at all
        ],
    )
    def test_rejects_non_canonical(self, line: str) -> None:
        assert not _has_opt_out(line)


class TestCommentAndDocstringHeuristics:
    """Comments and docstrings must not trip the rail."""

    @pytest.mark.parametrize(
        "line",
        [
            "# path.write_text('foo')",
            "    # historically used path.write_text",
        ],
    )
    def test_comment_lines_are_skipped(self, line: str) -> None:
        assert _is_comment_line(line)

    def test_non_comment_with_inline_comment_is_not_a_comment_line(self) -> None:
        # The whole line isn't a comment, just trailing portion.
        assert not _is_comment_line("path.write_text('x')  # note")

    @pytest.mark.parametrize(
        "line",
        [
            '"""Example: path.write_text("data")"""',
            "'''Example: open(p, \"w\")'''",
            '"""docstring start',
            "'''docstring start",
        ],
    )
    def test_docstring_or_string_lines_are_skipped(self, line: str) -> None:
        assert _is_in_docstring_or_string(line)


# ---------------------------------------------------------------------------
# Unit tests: file-level allowlist
# ---------------------------------------------------------------------------


class TestAllowlist:
    """Files that own the atomic-write primitives are exempt."""

    def test_atomic_write_module_is_allowlisted(self) -> None:
        assert "src/utils/atomic_write.py" in _ALLOWLISTED_FILES

    def test_atomic_io_module_is_allowlisted(self) -> None:
        assert "src/utils/atomic_io.py" in _ALLOWLISTED_FILES

    def test_arbitrary_src_file_is_not_allowlisted(self) -> None:
        assert "src/services/intelligence/preference_store.py" not in _ALLOWLISTED_FILES


# ---------------------------------------------------------------------------
# Unit tests: find_violations on synthetic files
# ---------------------------------------------------------------------------


class TestFindViolationsSynthetic:
    """End-to-end checks against tmp_path source files."""

    def _write(self, tmp_path: Path, content: str) -> Path:
        # Mirror the path layout the detector expects so any future
        # path-relative checks still resolve correctly.
        src = tmp_path / "src" / "x"
        src.mkdir(parents=True)
        target = src / "mod.py"
        target.write_text(content)
        return target

    def test_unmarked_write_text_is_flagged(self, tmp_path: Path) -> None:
        target = self._write(tmp_path, 'p.write_text("payload")\n')
        violations = find_violations(target)
        assert len(violations) == 1
        assert "write_text" in violations[0][1]

    def test_marked_write_text_is_not_flagged(self, tmp_path: Path) -> None:
        target = self._write(
            tmp_path,
            'p.write_text("payload")  # atomic-write: ok — manual temp+replace\n',
        )
        assert find_violations(target) == []

    def test_unmarked_open_w_is_flagged(self, tmp_path: Path) -> None:
        target = self._write(tmp_path, 'with open(p, "w") as f:\n    f.write("x")\n')
        violations = find_violations(target)
        assert len(violations) == 1

    def test_marked_open_w_is_not_flagged(self, tmp_path: Path) -> None:
        target = self._write(
            tmp_path,
            'with open(p, "w") as f:  # atomic-write: ok — user output\n    f.write("x")\n',
        )
        assert find_violations(target) == []

    def test_open_read_mode_is_not_flagged(self, tmp_path: Path) -> None:
        # T10 negative case: the same surface (open with mode str) must
        # not flag for read modes.
        target = self._write(tmp_path, 'with open(p, "r") as f:\n    data = f.read()\n')
        assert find_violations(target) == []

    def test_marker_on_following_line_is_not_flagged(self, tmp_path: Path) -> None:
        # ruff format frequently splits long calls across lines, leaving the
        # # atomic-write: ok comment on the closing-paren line. The detector
        # must scan a lookahead window forward.
        target = self._write(
            tmp_path,
            'p.write_text(\n    "payload"\n)  # atomic-write: ok — manual temp+replace\n',
        )
        assert find_violations(target) == []

    def test_marker_too_far_after_call_is_still_flagged(self, tmp_path: Path) -> None:
        # The lookahead is bounded; a marker many lines later does not
        # silently exempt the call (T10 negative case for the lookahead).
        body = 'p.write_text("payload")\n' + ("x = 1\n" * 10) + "# atomic-write: ok\n"
        target = self._write(tmp_path, body)
        violations = find_violations(target)
        assert len(violations) == 1

    def test_docstring_example_is_not_flagged(self, tmp_path: Path) -> None:
        # Triple-quoted block containing a forbidden pattern must not trip.
        target = self._write(
            tmp_path,
            '"""Module docstring.\n\nExample::\n\n    p.write_text("foo")\n"""\n',
        )
        assert find_violations(target) == []


# ---------------------------------------------------------------------------
# Full-suite assertion: no unmarked violations on the current branch
# ---------------------------------------------------------------------------


class TestFullSuite:
    """The rail must pass against the live ``src/`` tree."""

    def test_no_unmarked_violations_on_current_tree(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"atomic-write rail flagged unmarked violation(s):\n{result.stderr}"
        )
