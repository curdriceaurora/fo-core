"""defusedxml-fallback rail — CI test.

Companion to ``scripts/check_defusedxml_fallback.py``. Verifies the detector
flags the silent ``defusedxml → stdlib xml`` import-fallback pattern (and
nothing else), and pins the current advisory baseline.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_defusedxml_fallback.py"

sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_defusedxml_fallback import find_violations  # noqa: E402

# One known site at filing time (src/services/deduplication/extractor.py:31).
_BASELINE_VIOLATIONS = 1


def _synth(tmp_path: Path, source: str) -> Path:
    p = tmp_path / "synth.py"
    p.write_text(source, encoding="utf-8")
    return p


class TestDetectorOnSyntheticInputs:
    def test_flags_stdlib_reimport_in_except(self, tmp_path: Path) -> None:
        source = (
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    import xml.etree.ElementTree as _ET\n"
        )
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1
        assert violations[0][0] == 2

    def test_flags_module_level_stdlib_with_swallowed_except(self, tmp_path: Path) -> None:
        source = (
            "import xml.etree.ElementTree as _stdlib_ET\n"
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    _ET = _stdlib_ET\n"
        )
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1
        assert violations[0][0] == 3

    def test_no_flag_when_handler_reraises(self, tmp_path: Path) -> None:
        source = (
            "import xml.etree.ElementTree as _stdlib_ET\n"
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    raise\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_no_flag_when_no_stdlib_xml_anywhere(self, tmp_path: Path) -> None:
        source = (
            "try:\n    import defusedxml.ElementTree as _ET\nexcept ImportError:\n    _ET = None\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_no_flag_for_unrelated_try_except_import(self, tmp_path: Path) -> None:
        source = "try:\n    import sklearn\nexcept ImportError:\n    sklearn = None\n"
        assert find_violations(_synth(tmp_path, source)) == []

    def test_opt_out_marker_exempts(self, tmp_path: Path) -> None:
        source = (
            "import xml.etree.ElementTree as _stdlib_ET\n"
            "try:\n"
            "    import defusedxml.ElementTree as _ET  # defusedxml-fallback: ok — test\n"
            "except ImportError:\n"
            "    _ET = _stdlib_ET\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_no_flag_when_module_xml_unrelated_to_except(self, tmp_path: Path) -> None:
        """Unrelated module-level stdlib xml import shouldn't false-positive.

        Regression rail for coderabbit PR-329 finding: variant-2 detection
        was a module-wide boolean ("any stdlib xml import anywhere"). A
        defusedxml try block whose except body doesn't actually reference
        the stdlib xml import should NOT be flagged just because some
        unrelated code in the same module imports xml.
        """
        source = (
            "import xml.etree.ElementTree as _used_only_for_pretty_print\n"
            "def pretty(x):\n"
            "    return _used_only_for_pretty_print.tostring(x)\n"
            "\n"
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    _ET = None\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_flag_when_except_actually_references_module_xml(self, tmp_path: Path) -> None:
        """If the except body DOES reference the stdlib alias, still flag."""
        source = (
            "import xml.etree.ElementTree as _stdlib_ET\n"
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    _ET = _stdlib_ET\n"
        )
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1

    def test_function_local_xml_import_not_module_level(self, tmp_path: Path) -> None:
        """A function-local xml import is not a module-level fallback bridge.

        Regression rail for codex PR-329 second-round finding:
        `_module_xml_import_names` used `ast.walk(tree)`, collecting xml
        aliases from inside helper functions. An except body that
        references such a name would actually raise `NameError` at
        module-import time (i.e. fail closed), not silently bridge to
        the stdlib — so it must not be flagged.
        """
        source = (
            "def helper():\n"
            "    import xml.etree.ElementTree as _local_ET\n"
            "    return _local_ET\n"
            "\n"
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    _ET = _local_ET  # would actually be NameError at module load\n"
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

    def test_emits_report(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--advisory"],
            capture_output=True,
            text=True,
            cwd=_FO_ROOT,
        )
        assert "[defusedxml-fallback]" in result.stderr


class TestBaselineRatchet:
    def test_no_regression_beyond_baseline(self) -> None:
        from check_defusedxml_fallback import _scan_all

        actual = len(_scan_all())
        assert actual <= _BASELINE_VIOLATIONS, (
            f"defusedxml-fallback count rose: baseline={_BASELINE_VIOLATIONS}, "
            f"current={actual}. Fix or mark with opt-out."
        )
        if actual < _BASELINE_VIOLATIONS:
            pytest.skip(
                f"baseline now {actual} (was {_BASELINE_VIOLATIONS}); "
                f"lower _BASELINE_VIOLATIONS in this file."
            )
