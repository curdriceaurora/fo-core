"""TextIOWrapper-detach rail — CI test.

Companion to ``scripts/check_textiowrapper_detach.py``. The PR #276 fix
(`text_stream.detach()` in cad.py / iges_file readers) is already in
place; this rail catches *regression* — a new fileobj-accepting reader
that wraps `io.TextIOWrapper(...)` without `.detach()` (or any future
reader that drops the existing detach call).

Baseline = 0 at filing time. Any new violation fails the regression test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_textiowrapper_detach.py"

sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_textiowrapper_detach import find_violations  # noqa: E402

_BASELINE_VIOLATIONS = 0


def _synth(tmp_path: Path, source: str) -> Path:
    p = tmp_path / "synth.py"
    p.write_text(source, encoding="utf-8")
    return p


class TestDetectorOnSyntheticInputs:
    def test_flags_fileobj_wrap_without_detach(self, tmp_path: Path) -> None:
        source = (
            "import io\n"
            "def reader(fileobj):\n"
            "    text_stream = io.TextIOWrapper(fileobj, encoding='utf-8')\n"
            "    data = text_stream.read()\n"
            "    return data\n"
        )
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1
        assert violations[0][0] == 3

    def test_no_flag_when_detach_called(self, tmp_path: Path) -> None:
        source = (
            "import io\n"
            "def reader(fileobj):\n"
            "    text_stream = io.TextIOWrapper(fileobj, encoding='utf-8')\n"
            "    try:\n"
            "        data = text_stream.read()\n"
            "    finally:\n"
            "        text_stream.detach()\n"
            "    return data\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_no_flag_when_no_fileobj_param(self, tmp_path: Path) -> None:
        source = (
            "import io\n"
            "def reader(path):\n"
            "    with open(path, 'rb') as fileobj:\n"
            "        text_stream = io.TextIOWrapper(fileobj, encoding='utf-8')\n"
            "        return text_stream.read()\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_flags_stream_param(self, tmp_path: Path) -> None:
        """`stream` is also a recognised fileobj-style param name."""
        source = (
            "import io\n"
            "def reader(stream):\n"
            "    wrapped = io.TextIOWrapper(stream, encoding='utf-8')\n"
            "    return wrapped.read()\n"
        )
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1

    def test_opt_out_marker_exempts(self, tmp_path: Path) -> None:
        source = (
            "import io\n"
            "def reader(fileobj):\n"
            "    text_stream = io.TextIOWrapper(fileobj, encoding='utf-8')  "
            "# textiowrapper-detach: ok — synthetic test\n"
            "    return text_stream.read()\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []


class TestAdvisoryRunOnLiveTree:
    def test_advisory_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--advisory"],
            capture_output=True,
            text=True,
            cwd=_FO_ROOT,
        )
        assert result.returncode == 0


class TestBaselineRatchet:
    def test_no_regression_beyond_baseline(self) -> None:
        from check_textiowrapper_detach import _scan_all

        actual = len(_scan_all())
        assert actual <= _BASELINE_VIOLATIONS, (
            f"textiowrapper-detach count rose: baseline={_BASELINE_VIOLATIONS}, "
            f"current={actual}. Fix the new site (call .detach()) or mark with opt-out."
        )
