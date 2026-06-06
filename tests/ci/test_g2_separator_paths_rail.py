"""Tests for the G2-sep rail (``scripts/check_test_separator_paths.py``).

The rail flags separator-sensitive absolute POSIX path literals assigned to
path-like variables in test files — the class of Windows-only breakage fixed
in PR #464 (``test_doctor.py`` ``test_detect_pipx_via_pipx_home_env``). The
detector runs as both an advisory pre-commit hook and as the baseline CI test
in this file.

Tests cover the literal predicate, the path-like-name predicate (with T10
negative cases for each), the opt-out exemption, the adversarial-input
exemption, end-to-end ``find_violations`` behaviour, and the baseline assertion
that no unexempted violations exist on ``main``.

Sample separator-sensitive literals are built via concatenation and assigned to
non-path-like names so this self-test file does not trip its own rail.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci

_FO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _FO_ROOT / "scripts" / "check_test_separator_paths.py"

# Import the detector directly so helpers can be unit-tested without spawning a
# subprocess for every case.
sys.path.insert(0, str(_FO_ROOT / "scripts"))
from check_test_separator_paths import (  # noqa: E402
    _ADVERSARIAL_INPUTS,
    _scan_all,
    find_violations,
    is_pathish_name,
    is_separator_sensitive,
    main,
)

# Built via concatenation so the line is not a raw ``<name> = "<literal>"``
# assignment the rail would flag in this file.
_SEP = "/custom" + "/pipx/venvs/fo-core/bin/python"
_SEP2 = "/custom" + "/pipx"

# Pinned baseline for the advisory rail. PR #464 fixed the only occurrence, so
# the count is 0; any regression must drive this test red.
_BASELINE_VIOLATIONS = 0


# ---------------------------------------------------------------------------
# Unit tests: is_separator_sensitive
# ---------------------------------------------------------------------------


class TestSeparatorSensitivePredicate:
    """Multi-segment absolute POSIX literals match; near-misses do not."""

    @pytest.mark.parametrize(
        "value",
        [
            _SEP,
            _SEP2,
            "/opt" + "/app/bin/python",
            "/srv" + "/data/file.txt",
        ],
    )
    def test_matches_separator_sensitive(self, value: str) -> None:
        assert is_separator_sensitive(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "relative/path/segment",  # not absolute
            "/single",  # only one segment
            "/single/",  # trailing slash, second segment empty
            "https://example.com/a/b",  # URL
            "file://" + "/srv/x/y",  # URL scheme
            "/etc/passwd",  # adversarial input — exempt
            "/dev/null",  # adversarial input — exempt
            "plain string",
            "",
        ],
    )
    def test_rejects_non_separator_sensitive(self, value: str) -> None:
        assert is_separator_sensitive(value) is False


# ---------------------------------------------------------------------------
# Unit tests: is_pathish_name (T10 — surface-similar negatives)
# ---------------------------------------------------------------------------


class TestPathishName:
    """Snake_case names with a path-like component match; others do not."""

    @pytest.mark.parametrize(
        "name",
        ["fake_exe", "custom_home", "share_dir", "exe_path", "venv_root", "config_file"],
    )
    def test_pathish_names_match(self, name: str) -> None:
        assert is_pathish_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "profile",  # contains "file" but not as a component
            "result",
            "value",
            "data",
            "endpoint",
            "url",
            "directory_listing_count",  # "directory" != "dir" component
        ],
    )
    def test_non_pathish_names_do_not_match(self, name: str) -> None:
        assert is_pathish_name(name) is False


# ---------------------------------------------------------------------------
# Unit tests: adversarial-input exemption
# ---------------------------------------------------------------------------


class TestAdversarialExemption:
    """Documented path-validation inputs are never separator-sensitive hits."""

    @pytest.mark.parametrize("adv_path", _ADVERSARIAL_INPUTS)
    def test_known_adversarial_path_is_exempt(self, adv_path: str) -> None:
        # Even a multi-segment absolute path is exempt when it is a documented
        # adversarial input.
        assert is_separator_sensitive(adv_path + "/x") is False

    def test_unrelated_etc_path_is_not_exempt(self) -> None:
        # /etc/hosts is NOT in the adversarial list, so it is treated as a
        # genuine separator-sensitive literal.
        assert is_separator_sensitive("/etc" + "/hosts/file") is True


# ---------------------------------------------------------------------------
# Integration: find_violations against synthetic files
# ---------------------------------------------------------------------------


class TestFindViolations:
    """End-to-end detection on per-case tmp files."""

    def test_flags_pr464_shape(self, tmp_path: Path) -> None:
        f = tmp_path / "test_bug.py"
        f.write_text(
            "def test_detect():\n"
            f'    custom_home = "{_SEP2}"\n'
            f'    fake_exe = "{_SEP}"\n'
            "    assert custom_home and fake_exe\n"
        )
        violations = find_violations(f)
        assert len(violations) == 2
        assert violations[0] == (2, "custom_home", _SEP2)
        assert violations[1] == (3, "fake_exe", _SEP)

    def test_non_pathish_variable_is_not_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "test_url.py"
        f.write_text(f'def test_x():\n    endpoint = "{_SEP}"\n')
        assert find_violations(f) == []

    def test_constructed_path_is_not_flagged(self, tmp_path: Path) -> None:
        # The recommended fix (os.path.join) assigns a Call, not a Constant —
        # the rail must not flag it.
        f = tmp_path / "test_fixed.py"
        f.write_text(
            "import os\n"
            "def test_x():\n"
            '    fake_exe = os.path.join(os.sep, "custom", "pipx", "bin", "python")\n'
        )
        assert find_violations(f) == []

    def test_adversarial_input_is_not_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "test_adv.py"
        f.write_text('def test_x():\n    exe_path = "/etc/passwd"\n')
        assert find_violations(f) == []

    def test_url_is_not_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "test_url2.py"
        f.write_text('def test_x():\n    base_path = "https://example.com/a/b"\n')
        assert find_violations(f) == []

    def test_opt_out_marker_skips_line(self, tmp_path: Path) -> None:
        f = tmp_path / "test_optout.py"
        f.write_text(f'def test_x():\n    fake_exe = "{_SEP}"  # g2sep: ok — linux-only fixture\n')
        assert find_violations(f) == []

    def test_flags_fstring_with_hardcoded_absolute_prefix(self, tmp_path: Path) -> None:
        # f-strings build the same hazard as plain literals: the hardcoded "/"
        # prefix never matches os.path.join(...) + os.sep on Windows.
        f = tmp_path / "test_fstring.py"
        f.write_text('def test_x(name):\n    fake_exe = f"/custom/pipx/venvs/{name}/bin/python"\n')
        violations = find_violations(f)
        assert len(violations) == 1
        assert violations[0] == (2, "fake_exe", "/custom/pipx/venvs/{...}/bin/python")

    def test_fstring_leading_interpolation_is_not_flagged(self, tmp_path: Path) -> None:
        # Rooted at a variable, not a hardcoded "/" — the skeleton does not start
        # with "/", so it is correctly skipped.
        f = tmp_path / "test_fstring_var.py"
        f.write_text('def test_x(base):\n    home_dir = f"{base}/sub/path"\n')
        assert find_violations(f) == []

    def test_fstring_url_is_not_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "test_fstring_url.py"
        f.write_text('def test_x(host):\n    base_path = f"https://{host}/a/b"\n')
        assert find_violations(f) == []

    def test_annotated_assignment_is_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "test_ann.py"
        f.write_text(f'def test_x():\n    home_dir: str = "{_SEP2}"\n')
        violations = find_violations(f)
        assert len(violations) == 1
        assert violations[0] == (2, "home_dir", _SEP2)


# ---------------------------------------------------------------------------
# Baseline: the project's own tests/ must hold at zero violations
# ---------------------------------------------------------------------------


class TestBaselineEnforcement:
    """The detector must report no more than the pinned baseline on tests/.

    The rail ships advisory: the pre-commit hook (and ``main`` while
    ``_ENFORCING`` is False) exits 0 even on violation, so the script's return
    code is NOT the gate. This test queries the scanned violation list directly
    so a regression fails CI regardless of advisory exit semantics. Promote the
    hook to enforcing once this baseline has held at zero.
    """

    def test_no_regression_beyond_baseline(self) -> None:
        actual = len(_scan_all())
        assert actual <= _BASELINE_VIOLATIONS, (
            f"G2-sep count rose: baseline={_BASELINE_VIOLATIONS}, actual={actual}. "
            "A separator-sensitive absolute POSIX literal was assigned to a "
            "path-like variable in tests/. Build it with os.path.join / os.sep "
            "(or use tmp_path), or add `# g2sep: ok — <reason>`."
        )

    def test_script_runs_and_exits_zero_in_advisory(self) -> None:
        # Smoke test: the script is invokable as a hook and stays advisory.
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--advisory"],
            capture_output=True,
            text=True,
            cwd=_FO_ROOT,
            check=False,
        )
        assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Contract: advisory vs enforcing exit codes
# ---------------------------------------------------------------------------


class TestMainContract:
    """``main`` exits 0 in advisory mode even when violations are present."""

    def test_advisory_returns_zero_with_violations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        synthetic_tests = tmp_path / "tests"
        synthetic_tests.mkdir()
        (synthetic_tests / "test_bad.py").write_text(f'def test_x():\n    fake_exe = "{_SEP}"\n')

        import check_test_separator_paths as mod

        monkeypatch.setattr(mod, "_TESTS_DIR", synthetic_tests)
        monkeypatch.setattr(mod, "_ROOT", tmp_path)

        assert mod.main(["--advisory"]) == 0

    def test_clean_tree_returns_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        synthetic_tests = tmp_path / "tests"
        synthetic_tests.mkdir()
        (synthetic_tests / "test_ok.py").write_text(
            'def test_x():\n    endpoint = "/api/v1/upload"\n'
        )

        import check_test_separator_paths as mod

        monkeypatch.setattr(mod, "_TESTS_DIR", synthetic_tests)
        monkeypatch.setattr(mod, "_ROOT", tmp_path)

        assert main(["--advisory"]) == 0
