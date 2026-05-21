"""SafeDir-ValueError rail — CI test.

Companion to ``scripts/check_safedir_valueerror.py``. The rail is in
**advisory mode** while the gap documented in issue #323 is being fixed;
this test pins the current violation count as a baseline so that any new
regression (a fresh try/except that catches OSError but not ValueError
around a SafeDir call) fails CI even before the rail goes enforcing.

Three checks:

1. The detector itself works on synthetic positive / negative inputs.
2. Running ``check_safedir_valueerror.py --advisory`` against the real
   ``src/`` exits 0 (advisory contract).
3. The current baseline count (13 as of issue #323 filing) is recorded.
   Any new violation makes the count differ from the baseline and the
   test fails — promoting fixes is the only way to lower the baseline.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_safedir_valueerror.py"

# Add the scripts dir to sys.path so we can import the detector for unit tests.
sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_safedir_valueerror import find_violations  # noqa: E402

# Baseline = number of unexempted violations at the time issue #323 was
# filed. Any future PR that lowers this should also lower the constant.
# Any PR that raises it (regression) fails ``test_no_regression_beyond_baseline``.
_BASELINE_VIOLATIONS = 13


def _synth(tmp_path: Path, source: str) -> Path:
    """Write *source* to a temp file and return the path."""
    p = tmp_path / "synth.py"
    p.write_text(source, encoding="utf-8")
    return p


class TestDetectorOnSyntheticInputs:
    """Verify the AST detector flags the right calls and only the right calls."""

    def test_flags_method_call_without_valueerror_catch(self, tmp_path: Path) -> None:
        source = (
            "def f(safe_dir, name):\n"
            "    try:\n"
            "        safe_dir.open_for_reader(name)\n"
            "    except (SymlinkRejected, OSError):\n"
            "        pass\n"
        )
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1
        assert violations[0][0] == 3

    def test_no_flag_when_except_includes_valueerror(self, tmp_path: Path) -> None:
        source = (
            "def f(safe_dir, name):\n"
            "    try:\n"
            "        safe_dir.open_for_reader(name)\n"
            "    except (SymlinkRejected, OSError, ValueError):\n"
            "        pass\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_no_flag_when_except_is_bare(self, tmp_path: Path) -> None:
        source = (
            "def f(safe_dir, name):\n"
            "    try:\n"
            "        safe_dir.open_for_reader(name)\n"
            "    except:  # noqa: E722\n"
            "        pass\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_no_flag_when_except_is_exception(self, tmp_path: Path) -> None:
        source = (
            "def f(safe_dir, name):\n"
            "    try:\n"
            "        safe_dir.open_for_reader(name)\n"
            "    except Exception:\n"
            "        pass\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_flags_helper_function_call(self, tmp_path: Path) -> None:
        source = (
            "def f(path, trusted_root):\n"
            "    try:\n"
            "        read_file_via_safedir_anchored(path, trusted_root=trusted_root)\n"
            "    except OSError:\n"
            "        pass\n"
        )
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1

    def test_no_flag_when_no_safedir_call(self, tmp_path: Path) -> None:
        source = (
            "def f(path):\n"
            "    try:\n"
            "        path.open('rb').read()\n"
            "    except OSError:\n"
            "        pass\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_opt_out_marker_exempts_call(self, tmp_path: Path) -> None:
        source = (
            "def f(safe_dir, name):\n"
            "    try:\n"
            "        safe_dir.open_for_reader(name)  # safedir-valueerror: ok — synth test\n"
            "    except OSError:\n"
            "        pass\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_opt_out_marker_in_string_does_not_exempt(self, tmp_path: Path) -> None:
        """Marker text inside a string literal is NOT an opt-out."""
        source = (
            "def f(safe_dir, name):\n"
            '    note = "# safedir-valueerror: ok — bypass attempt"\n'
            "    try:\n"
            "        safe_dir.open_for_reader(name)\n"
            "    except OSError:\n"
            "        pass\n"
        )
        assert len(find_violations(_synth(tmp_path, source))) == 1


class TestAdvisoryRunOnLiveTree:
    """Run the script against real ``src/`` in advisory mode."""

    def test_advisory_exits_zero(self) -> None:
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

    def test_advisory_emits_report(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--advisory"],
            capture_output=True,
            text=True,
            cwd=_FO_ROOT,
        )
        assert "[safedir-valueerror]" in result.stderr


class TestBaselineRatchet:
    """The current violation count must not exceed the recorded baseline.

    Any PR that adds a new ``try: safe_dir.X() except OSError`` site without
    catching ValueError increases the count and fails here. Lowering the
    count (i.e. fixing a violation in place) requires lowering the constant.
    """

    def test_no_regression_beyond_baseline(self) -> None:
        from check_safedir_valueerror import _scan_all

        actual = len(_scan_all())
        assert actual <= _BASELINE_VIOLATIONS, (
            f"safedir-valueerror count rose: baseline={_BASELINE_VIOLATIONS}, "
            f"current={actual}. Either fix the new site or, if intentional, "
            f"add `# safedir-valueerror: ok — <reason>` to the call line."
        )
        # If the count dropped, prompt the author to lower the baseline too.
        # Soft assertion to avoid blocking honest fixes that forgot the
        # bookkeeping — we just emit a warning that the constant is stale.
        if actual < _BASELINE_VIOLATIONS:
            pytest.skip(
                f"baseline now {actual} (was {_BASELINE_VIOLATIONS}); "
                f"lower _BASELINE_VIOLATIONS in this file."
            )
