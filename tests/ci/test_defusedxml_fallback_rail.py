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

    def test_function_local_xml_bridge_is_flagged(self, tmp_path: Path) -> None:
        """Function-scope bridge is a real silent fallback — must flag.

        Regression rail for codex PR-329 round-3 finding: my round-2 fix
        over-corrected by collecting *only* module-level xml names. A
        function-local ``import xml... as _stdlib_ET`` followed by an
        ``except ImportError: _ET = _stdlib_ET`` *in the same function*
        IS a real silent fallback — both bindings live in that function's
        scope. Must flag.
        """
        source = (
            "def load_parser():\n"
            "    import xml.etree.ElementTree as _stdlib_ET\n"
            "    try:\n"
            "        import defusedxml.ElementTree as _ET\n"
            "    except ImportError:\n"
            "        _ET = _stdlib_ET\n"
            "    return _ET\n"
        )
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1

    def test_incidental_stdlib_read_without_rebind_does_not_flag(self, tmp_path: Path) -> None:
        """Reading the stdlib alias for logging — without re-binding the
        defusedxml target — is not a bridge.

        Regression rail for codex PR-329 round-6 finding: previously the
        detector flagged any ``ast.Load`` of a stdlib xml alias in the
        handler, even when the handler genuinely cleared the defusedxml
        target (fail closed).
        """
        source = (
            "import xml.etree.ElementTree as _stdlib_ET\n"
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    logger.warning('defusedxml missing — stdlib alias is %s', _stdlib_ET)\n"
            "    _ET = None\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_rebind_to_stdlib_attribute_still_flags(self, tmp_path: Path) -> None:
        """Re-binding the defusedxml target to ``_stdlib_ET.parse`` IS a bridge."""
        source = (
            "import xml.etree.ElementTree as _stdlib_ET\n"
            "try:\n"
            "    from defusedxml.ElementTree import parse as _parse\n"
            "except ImportError:\n"
            "    _parse = _stdlib_ET.parse\n"
        )
        violations = find_violations(_synth(tmp_path, source))
        assert len(violations) == 1

    def test_rebind_to_unrelated_target_does_not_flag(self, tmp_path: Path) -> None:
        """``_other = _stdlib_ET`` doesn't bridge — wrong LHS."""
        source = (
            "import xml.etree.ElementTree as _stdlib_ET\n"
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    _other = _stdlib_ET  # not the defusedxml target\n"
            "    _ET = None  # genuine fail-closed\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

    def test_class_body_uses_source_order_not_late_binding(self, tmp_path: Path) -> None:
        """Class bodies execute at definition time — source order matters.

        Regression rail for codex PR-329 round-5 finding
        (PRRT_kwDOR_Rkws6DzN0E): unlike function bodies (late binding at
        call time), class bodies execute immediately at the ``class X:``
        line. An ``except ImportError: _ET = _stdlib_ET`` inside a class
        body cannot see an ``import xml... as _stdlib_ET`` placed later
        in the enclosing scope — at class-definition time, the binding
        doesn't exist yet → NameError (fail closed).
        """
        source = (
            "class Parser:\n"
            "    try:\n"
            "        import defusedxml.ElementTree as _ET\n"
            "    except ImportError:\n"
            "        _ET = _stdlib_ET\n"
            "\n"
            "import xml.etree.ElementTree as _stdlib_ET\n"
        )
        # At class-definition time _stdlib_ET is not yet bound → fail closed.
        assert find_violations(_synth(tmp_path, source)) == []

    def test_xml_import_after_try_does_not_make_it_visible(self, tmp_path: Path) -> None:
        """An xml import later in the file isn't visible at the Try.

        Regression rail for codex PR-329 round-4 finding
        (PRRT_kwDOR_Rkws6Dy_8n): my round-3 fix used the full scope-level
        xml alias set, which conflated names imported AFTER the Try with
        names imported before. At runtime, a handler that reads
        ``_stdlib_ET`` would NameError if the import comes later — so
        that's fail-closed, not silent fallback.
        """
        source = (
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    _ET = _stdlib_ET\n"
            "import xml.etree.ElementTree as _stdlib_ET\n"
        )
        # The except body reads `_stdlib_ET`, but that name is imported
        # AFTER the try at line 5. At runtime: NameError → fail closed.
        # The rail must NOT flag this.
        assert find_violations(_synth(tmp_path, source)) == []

    def test_handler_assigns_only_does_not_flag(self, tmp_path: Path) -> None:
        """Assignment-only (no Load) doesn't bridge — must NOT flag.

        Regression rail for coderabbit PR-329 round-3 finding:
        ``_handler_references_any`` previously matched any ``ast.Name``
        regardless of ``ctx``. A handler that just clears the binding
        (``parse = None``) is not a real bridge — it's nulling the
        stdlib alias, not handing it back to callers.
        """
        source = (
            "from xml.etree.ElementTree import parse\n"
            "try:\n"
            "    import defusedxml.ElementTree as _ET\n"
            "except ImportError:\n"
            "    parse = None\n"
        )
        assert find_violations(_synth(tmp_path, source)) == []

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
