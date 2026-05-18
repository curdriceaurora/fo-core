"""SafeDir rail — CI test (security hardening tracking: #264).

Companion to ``scripts/check_safedir_required.py``. The rail is in **advisory
mode** for PR1 — every test in this file passes regardless of how many
violations the detector finds. As PRs #267 / #268 / #269 / #270 migrate
call sites to SafeDir, the rail's enforcement scope expands per-directory.

The CI test verifies three things:

1. The detector itself works on synthetic inputs (positive + negative cases).
2. Running ``check_safedir_required.py --advisory`` against the live tree
   exits 0 (no false-positive on the implementation modules).
3. The current baseline count is recorded so any regression that adds new
   call sites in PRs targeting unrelated areas is visible in CI logs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_safedir_required.py"

sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_safedir_required import (  # noqa: E402  — sys.path manipulation above
    _ALLOWLISTED_FILES,
    _FLAGGED_CALLS,
    _call_name,
    _collect_marker_comment_lines,
    _has_opt_out_in_window,
    find_violations,
    scan_tree,
)


def _synth(tmp_path: Path, content: str, name: str = "mod.py") -> Path:
    src = tmp_path / "src" / "x"
    src.mkdir(parents=True)
    target = src / name
    target.write_text(content)
    return target


# ---------------------------------------------------------------------------
# Self-test: detector flags the expected surface
# ---------------------------------------------------------------------------


class TestDetectorPositiveCases:
    """The detector must flag every call form on the watch list."""

    @pytest.mark.parametrize(
        "source",
        [
            "import fitz\nfitz.open('/foo/bar.pdf')\n",
            "from PIL import Image\nImage.open('/foo/bar.jpg')\n",
            "import shutil\nshutil.copy2('/a', '/b')\n",
            "import shutil\nshutil.move('/a', '/b')\n",
            "import shutil\nshutil.copytree('/a', '/b')\n",
            "from docx import Document\nDocument('/foo/bar.docx')\n",
            "import docx\ndocx.Document('/foo/bar.docx')\n",
            "from pptx import Presentation\nPresentation('/foo/bar.pptx')\n",
            "from pypdf import PdfReader\nPdfReader(open('/foo/bar.pdf', 'rb'))\n",
            "from openpyxl import load_workbook\nload_workbook('/foo/bar.xlsx')\n",
            "import tarfile\ntarfile.open('/foo/bar.tar')\n",
            "from py7zr import SevenZipFile\nSevenZipFile('/foo/bar.7z')\n",
            "import rarfile\nrarfile.RarFile('/foo/bar.rar')\n",
            "import zipfile\nzipfile.ZipFile('/foo/bar.zip')\n",
            "from zipfile import ZipFile\nZipFile('/foo/bar.zip')\n",
        ],
    )
    def test_flagged_call(self, tmp_path: Path, source: str) -> None:
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1, f"expected 1 violation, got {violations}"


class TestDetectorOptOut:
    """Calls carrying the ``# safedir: ok`` marker are not flagged."""

    def test_trailing_comment_opt_out(self, tmp_path: Path) -> None:
        source = "import shutil\nshutil.copy2('/a', '/b')  # safedir: ok — one-shot user export\n"
        assert find_violations(_synth(tmp_path, source)) == []

    def test_preceding_comment_opt_out(self, tmp_path: Path) -> None:
        source = (
            "import shutil\n"
            "# safedir: ok — backup of app-owned state, not user-root data\n"
            "shutil.copy2('/a', '/b')\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_string_literal_marker_does_not_opt_out(self, tmp_path: Path) -> None:
        """A string containing the marker text cannot bypass the rail."""
        source = "import shutil\nmsg = \"# safedir: ok — fake marker\"\nshutil.copy2('/a', '/b')\n"
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1

    def test_bare_marker_without_reason_does_not_opt_out(self, tmp_path: Path) -> None:
        """``# safedir: ok`` without a separator + reason is rejected.

        The documented contract is ``# safedir: ok — <reason>``. A bare
        marker would let drive-by exemptions slip through review.
        """
        source = "import shutil\nshutil.copy2('/a', '/b')  # safedir: ok\n"
        assert len(find_violations(_synth(tmp_path, source))) == 1

    def test_marker_with_hyphen_separator_opts_out(self, tmp_path: Path) -> None:
        """Both ``— em-dash`` and ``- hyphen`` are accepted separators."""
        source = "import shutil\nshutil.copy2('/a', '/b')  # safedir: ok - one-shot user export\n"
        assert find_violations(_synth(tmp_path, source)) == []


class TestDetectorNegativeCases:
    """The detector must NOT flag unrelated calls."""

    @pytest.mark.parametrize(
        "source",
        [
            # Read-only that doesn't follow symlinks for content
            "from pathlib import Path\nPath('/foo').exists()\n",
            # safe_walk is the safe primitive
            "from core.path_guard import safe_walk\nlist(safe_walk('/foo'))\n",
            # open() with a read mode — covered by atomic-write rail, not this one
            "open('/foo', 'r')\n",
            # logger noise
            "import logging\nlogging.info('shutil.copy2 was called')\n",
        ],
    )
    def test_not_flagged(self, tmp_path: Path, source: str) -> None:
        assert find_violations(_synth(tmp_path, source)) == []


class TestDetectorHelpers:
    """Unit-coverage on the AST helper functions."""

    def test_call_name_dotted(self, tmp_path: Path) -> None:
        import ast as _ast

        tree = _ast.parse("a.b.c(1)")
        call = next(n for n in _ast.walk(tree) if isinstance(n, _ast.Call))
        assert _call_name(call) == "a.b.c"

    def test_call_name_bare(self, tmp_path: Path) -> None:
        import ast as _ast

        tree = _ast.parse("Document('x')")
        call = next(n for n in _ast.walk(tree) if isinstance(n, _ast.Call))
        assert _call_name(call) == "Document"

    def test_call_name_dynamic_returns_none(self, tmp_path: Path) -> None:
        import ast as _ast

        tree = _ast.parse("getattr(x, 'open')(1)")
        call = next(n for n in _ast.walk(tree) if isinstance(n, _ast.Call))
        assert _call_name(call) is None

    def test_marker_window_bounds(self) -> None:
        """Marker on the call line + 6 below = inside; 7 below = outside."""
        # call at line 10; window covers 8..16 inclusive
        assert _has_opt_out_in_window({16}, call_line=10, total_lines=100) is True
        assert _has_opt_out_in_window({17}, call_line=10, total_lines=100) is False
        assert _has_opt_out_in_window({8}, call_line=10, total_lines=100) is True
        assert _has_opt_out_in_window({7}, call_line=10, total_lines=100) is False

    def test_marker_comment_only_real_comments(self) -> None:
        """A docstring or string literal containing the marker doesn't count."""
        source = (
            '"""# safedir: ok — fake"""\n'
            'x = "# safedir: ok — also fake"\n'
            "# safedir: ok — real\n"
            "y = 1\n"
        )
        marker_lines = _collect_marker_comment_lines(source)
        assert marker_lines == {3}


class TestPredicateNegativeCases:
    """T10 backfill — predicate-style detectors need negative cases that
    pass the same surface shape with wrong context and assert ``False``.

    ``_call_name`` returns the dotted attribute chain. The detector then
    matches against ``_FLAGGED_CALLS``. Two false-positive shapes worth
    verifying explicitly:
    """

    def test_attribute_chain_with_different_base_not_flagged(self, tmp_path: Path) -> None:
        """``other.copy2`` (not ``shutil.copy2``) must not match."""
        source = "class X:\n    def copy2(self, a, b): pass\nx = X()\nx.copy2('a', 'b')\n"
        # _call_name returns "x.copy2" — not in _FLAGGED_CALLS.
        assert find_violations(_synth(tmp_path, source)) == []

    def test_unrelated_open_method_not_flagged(self, tmp_path: Path) -> None:
        """A method called ``open`` on an unrelated receiver is not flagged.

        Exercises the dotted-call path: ``s.open(...)`` parses to
        ``Attribute(Name('s'), 'open')``, so ``_call_name`` returns
        ``"s.open"`` — not in ``_FLAGGED_CALLS``. Compare with calling
        ``S()`` directly, where ``_call_name`` would return ``None``
        because the base is a ``Call`` rather than a ``Name`` (that
        false-negative path is tested separately in
        ``TestDetectorHelpers.test_call_name_dynamic_returns_none``).
        """
        source = "class S:\n    def open(self, addr): pass\ns = S()\ns.open('localhost')\n"
        assert find_violations(_synth(tmp_path, source)) == []


# ---------------------------------------------------------------------------
# Live-tree advisory run
# ---------------------------------------------------------------------------


class TestLiveTreeAdvisory:
    """Run the detector against the real ``src/`` in advisory mode."""

    def test_advisory_run_exits_zero(self) -> None:
        """The rail is advisory in PR1 — must not fail CI on the current tree."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--advisory"],
            capture_output=True,
            text=True,
            cwd=_FO_ROOT,
        )
        assert result.returncode == 0, (
            f"advisory rail unexpectedly failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_baseline_visible_in_stderr(self) -> None:
        """The advisory run prints the violation count so PR reviewers see drift.

        Phase-1 baseline: ~42 call sites across read-side, dedupe, undo, and
        watcher paths. The exact number is implementation-noise (one shuffle
        of ``shutil.copy2`` lands ±1). We assert the report is present and
        the per-callee breakdown emits something useful, not the exact count.
        """
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--advisory"],
            capture_output=True,
            text=True,
            cwd=_FO_ROOT,
        )
        assert "[safedir-rail]" in result.stderr
        assert "call site(s)" in result.stderr
        assert "Breakdown by callee:" in result.stderr
        assert "ADVISORY mode" in result.stderr

    def test_allowlist_files_not_scanned(self) -> None:
        """Allowlisted implementation files don't generate self-flags."""
        violations = scan_tree()
        flagged_files = {p.relative_to(_FO_ROOT).as_posix() for p, _, _, _ in violations}
        for allowlisted in _ALLOWLISTED_FILES:
            assert allowlisted not in flagged_files, (
                f"allowlisted file {allowlisted} appeared in violations"
            )


class TestFlaggedCallsContract:
    """Lock in the set of detected calls so silent changes are visible."""

    def test_flagged_calls_covers_the_audited_surface(self) -> None:
        """The minimum surface flagged by phase 1 — listed in #265."""
        required = {
            "fitz.open",
            "Image.open",
            "Presentation",
            "load_workbook",
            "PdfReader",
            "tarfile.open",
            "SevenZipFile",
            "RarFile",
            "shutil.copy2",
            "shutil.move",
            "shutil.copytree",
            "zipfile.ZipFile",
            "ZipFile",
        }
        missing = required - _FLAGGED_CALLS
        assert not missing, f"watch list dropped flagged calls: {missing}"


# ---------------------------------------------------------------------------
# Bare-open detection — added in PR3g (deferred from #271)
# ---------------------------------------------------------------------------


class TestBareOpenDetectionDirScoped:
    """Bare ``open(path, "r"...)`` / ``Path.open("r"...)`` / ``io.open(path, "r"...)``
    calls are only flagged inside directories listed in
    ``_READ_OPEN_ENFORCED_DIRS``. PR3g adds the detection with an
    empty enforced-dirs set as a placeholder; PR3i populates it for
    migrated reader / dedup directories.
    """

    def test_bare_open_not_flagged_when_dir_set_empty(self, tmp_path: Path) -> None:
        """With no directories enforced, bare-open reads aren't flagged
        even in synthetic files — the detection class is opt-in.
        """
        source = "with open('/x/y.txt', 'rb') as f: pass\n"
        # Synthetic path under tmp/src/x — not in _READ_OPEN_ENFORCED_DIRS.
        # Empty set means no file is enforced.
        violations = find_violations(_synth(tmp_path, source))
        assert violations == []

    @pytest.mark.parametrize(
        "source",
        [
            "with open('/x/y.txt', 'rb') as f: pass\n",
            'with open("/x", mode="r") as f: pass\n',
            "with open('/x') as f: pass\n",  # default mode = "r"
            "import io\nwith io.open('/x', 'rb') as f: pass\n",
            "from pathlib import Path\nPath('/x').open('rb')\n",
            "from pathlib import Path\nPath('/x').open()\n",  # default mode
            "from pathlib import Path\nPath('/x').open(mode='r')\n",
        ],
    )
    def test_bare_open_detected_via_helper(self, tmp_path: Path, source: str) -> None:
        """Direct unit-test of the AST helper — bypasses the dir-scope
        gate so we can verify the detection logic itself.
        """
        from check_safedir_required import _bare_open_violation

        tree = __import__("ast").parse(source)
        calls = [n for n in __import__("ast").walk(tree) if isinstance(n, __import__("ast").Call)]
        flagged = [_bare_open_violation(c) for c in calls if _bare_open_violation(c) is not None]
        assert len(flagged) == 1, f"expected exactly one read-mode call, got {flagged}"

    @pytest.mark.parametrize(
        "source",
        [
            # Write modes are not reads.
            "with open('/x', 'wb') as f: pass\n",
            'with open("/x", mode="w") as f: pass\n',
            "with open('/x', 'a') as f: pass\n",
            "from pathlib import Path\nPath('/x').open('wb')\n",
            "from pathlib import Path\nPath('/x').open(mode='a')\n",
            "import io\nio.open('/x', 'wb')\n",
        ],
    )
    def test_write_modes_not_flagged(self, tmp_path: Path, source: str) -> None:
        """Write-only modes (``"w"``/``"a"``/``"x"`` without ``"+"``) are
        legitimate writes, not reads — handled by the atomic-write rail
        instead. The bare-open read detector must not flag them.
        """
        from check_safedir_required import _bare_open_violation

        tree = __import__("ast").parse(source)
        calls = [n for n in __import__("ast").walk(tree) if isinstance(n, __import__("ast").Call)]
        flagged = [_bare_open_violation(c) for c in calls if _bare_open_violation(c) is not None]
        assert flagged == [], f"write-mode call wrongly flagged: {flagged}"

    def test_read_plus_modes_flagged(self) -> None:
        """``"r+"`` / ``"w+"`` / ``"a+"`` reads-and-writes still open
        the underlying file and could dereference a symlink."""
        from check_safedir_required import _bare_open_violation

        for source in [
            "open('/x', 'r+')\n",
            "open('/x', 'w+')\n",
            "open('/x', 'a+')\n",
        ]:
            tree = __import__("ast").parse(source)
            call = next(
                n for n in __import__("ast").walk(tree) if isinstance(n, __import__("ast").Call)
            )
            assert _bare_open_violation(call) is not None, f"missed read-write mode: {source!r}"
