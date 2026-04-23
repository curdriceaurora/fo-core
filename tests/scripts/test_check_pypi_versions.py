"""Unit tests for the E3 cap-or-marker rule in .claude/scripts/check_pypi_versions.py.

The rule enforces that every pre-1.0 `>=` pin in pyproject.toml has either an
upper-bound cap (e.g. `<1`) or the exact keep-as-is marker comment. These tests
exercise the rule offline (no PyPI network) via `--check-pre-1-0-only`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / ".claude" / "scripts" / "check_pypi_versions.py"

# Load the module by path so we can call _check_caps_or_marker directly — the
# script lives outside any package (same reason test_pip_audit_gate uses importlib).
_spec = importlib.util.spec_from_file_location("check_pypi_versions", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_pypi_versions = importlib.util.module_from_spec(_spec)
sys.modules["check_pypi_versions"] = check_pypi_versions
_spec.loader.exec_module(check_pypi_versions)
_check_caps_or_marker = check_pypi_versions._check_caps_or_marker
_version_is_pre_1 = check_pypi_versions._version_is_pre_1
_constraint_is_satisfiable = check_pypi_versions._constraint_is_satisfiable


def _write_pyproject(tmp_path: Path, deps: list[str]) -> Path:
    body = '[project]\nname = "x"\nversion = "0"\ndependencies = [\n'
    for d in deps:
        body += f"    {d},\n"
    body += "]\n"
    p = tmp_path / "pyproject.toml"
    p.write_text(body)
    return p


def test_pre_1_0_pin_with_cap_passes(tmp_path: Path) -> None:
    p = _write_pyproject(tmp_path, ['"striprtf>=0.0.26,<1"'])
    assert _check_caps_or_marker(p) == []


def test_pre_1_0_pin_with_marker_passes(tmp_path: Path) -> None:
    p = _write_pyproject(
        tmp_path,
        ['"ollama>=0.1.0"  # 0.x — unstable API, keep >='],
    )
    assert _check_caps_or_marker(p) == []


def test_pre_1_0_pin_without_cap_or_marker_fails(tmp_path: Path) -> None:
    p = _write_pyproject(tmp_path, ['"pydub>=0.25.0"'])
    failures = _check_caps_or_marker(p)
    assert len(failures) == 1
    assert "pydub" in failures[0]
    assert "pre-1.0" in failures[0]


def test_environment_marker_is_not_a_version_cap(tmp_path: Path) -> None:
    """Regression: `<` inside an environment marker (e.g. `; python_version < '3.12'`)
    must not be treated as an upper-bound cap.

    PR #163 review finding (chatgpt-codex-connector): the previous regex
    implementation did `has_cap = "<" in rest_of_spec`, which silently accepted
    `"foo>=0.2; python_version < '3.12'"` as capped even though the requirement
    has no upper version bound.
    """
    # Use single quotes for the marker value (real pyproject syntax — see
    # `mlx-lm>=0.0.19,<1; platform_system == 'Darwin'` in the repo).
    p = _write_pyproject(
        tmp_path,
        ["\"foo>=0.2; python_version < '3.12'\""],
    )
    failures = _check_caps_or_marker(p)
    assert len(failures) == 1, f"Env-marker `<` must not count as a cap, got: {failures}"
    assert "foo" in failures[0]


def test_environment_marker_plus_real_cap_passes(tmp_path: Path) -> None:
    """Regression: a real version cap must still be recognized when an env
    marker is also present.
    """
    p = _write_pyproject(
        tmp_path,
        ["\"foo>=0.2,<1; python_version < '3.12'\""],
    )
    assert _check_caps_or_marker(p) == []


def test_non_pre_1_0_pin_is_skipped(tmp_path: Path) -> None:
    """The rule targets only pre-1.0 pins; `>=1.0` deps are not in scope."""
    p = _write_pyproject(tmp_path, ['"requests>=2.31"'])
    assert _check_caps_or_marker(p) == []


def test_not_equal_operator_is_not_a_cap(tmp_path: Path) -> None:
    """Regression (R2 review): `!=` is not an upper-bound cap — it excludes
    one version while leaving higher versions unbounded. A pin like
    `foo>=0.2,!=0.3` must still fail the cap-or-marker check.
    """
    p = _write_pyproject(tmp_path, ['"foo>=0.2,!=0.3"'])
    failures = _check_caps_or_marker(p)
    assert len(failures) == 1, f"`!=` is not a cap; expected failure, got: {failures}"
    assert "foo" in failures[0]


def test_compatible_release_operator_counts_as_cap(tmp_path: Path) -> None:
    """`~=0.2` is equivalent to `>=0.2,<0.3` per PEP 440 — bounded, so it
    counts as a cap for the cap-or-marker rule.
    """
    p = _write_pyproject(tmp_path, ['"foo~=0.2"'])
    assert _check_caps_or_marker(p) == []


def test_single_quoted_toml_dep_string_is_checked(tmp_path: Path) -> None:
    """Regression (R3 review): TOML supports both `"..."` (basic) and
    `'...'` (literal) string forms. The cap check must inspect both — a
    single-quoted uncapped pre-1.0 pin like `'foo>=0.2'` would previously
    be skipped entirely, bypassing the rule.
    """
    # Mix both quote styles so the test exercises the regex alternation.
    body = (
        "[project]\n"
        'name = "x"\n'
        'version = "0"\n'
        "dependencies = [\n"
        "    'foo>=0.2',\n"  # single-quoted, uncapped — must fail
        '    "bar>=0.3,<1",\n'  # double-quoted, capped — must pass
        "]\n"
    )
    p = tmp_path / "pyproject.toml"
    p.write_text(body)
    failures = _check_caps_or_marker(p)
    assert len(failures) == 1, f"Single-quoted uncapped pin must fail; got: {failures}"
    assert "foo" in failures[0]


def test_script_invocation_exits_nonzero_on_failure(tmp_path: Path) -> None:
    """End-to-end: running the script as a subprocess with a failing pyproject
    exits 1 and emits the failure on stderr.
    """
    p = _write_pyproject(tmp_path, ['"pydub>=0.25.0"'])
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "--pyproject", str(p), "--check-pre-1-0-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 1
    assert "pydub" in r.stderr


# ---------------------------------------------------------------------------
# #164 — _version_is_pre_1 must catch every pre-1.0 shape, not just "0."
# ---------------------------------------------------------------------------


def test_version_is_pre_1_dotted() -> None:
    """The classic cases: dotted pre-1.0 versions."""
    assert _version_is_pre_1("0.7.2") is True
    assert _version_is_pre_1("0.0.19") is True
    assert _version_is_pre_1("0.1") is True


def test_version_is_pre_1_bare_zero() -> None:
    """Bare ``>=0`` must be treated as pre-1.0 (regression for #164).

    Previously ``version.startswith("0.")`` returned False for ``"0"``,
    silently letting ``foo>=0`` bypass the cap-or-marker rule.
    """
    assert _version_is_pre_1("0") is True


def test_version_is_pre_1_pre_release() -> None:
    """Pre-releases with major 0: 0a1, 0b0, 0rc1 (#164)."""
    assert _version_is_pre_1("0rc1") is True
    assert _version_is_pre_1("0a1") is True
    assert _version_is_pre_1("0b0") is True


def test_version_is_pre_1_false_for_post_1() -> None:
    """Anything with major >= 1 is post-1.0 and falls outside the pre-1.0 rule."""
    assert _version_is_pre_1("1.0.0") is False
    assert _version_is_pre_1("1") is False
    assert _version_is_pre_1("2.31.0") is False
    assert _version_is_pre_1("10.2.3") is False
    assert _version_is_pre_1("1rc1") is False


def test_version_is_pre_1_handles_invalid_input() -> None:
    """Non-numeric leading garbage returns False — treat as post-1.0 so the
    caller doesn't apply the pre-1.0 rule to something it can't classify.
    """
    assert _version_is_pre_1("") is False
    assert _version_is_pre_1("xyz") is False


def test_version_is_pre_1_respects_pep_440_epoch() -> None:
    """Regression for codex P2 review on PR #171: PEP 440 allows an epoch
    prefix like ``0!1.2``. The epoch (``0``) is not the release major —
    the release here is ``1.2`` (post-1.0). A naive leading-digit regex
    would misclassify this as pre-1.0 and force a cap on a legitimate
    post-1.0 pin.
    """
    # epoch 0, release 1.2 — post-1.0
    assert _version_is_pre_1("0!1.2") is False
    # epoch 1, release 0.9 — pre-1.0 despite epoch >= 1
    assert _version_is_pre_1("1!0.9") is True


def test_pre_1_bare_zero_without_cap_fails(tmp_path: Path) -> None:
    """End-to-end #164: ``foo>=0`` (bare zero, no cap) now fails the
    cap-or-marker rule. Before the fix this silently passed.
    """
    p = _write_pyproject(tmp_path, ['"foo>=0"'])
    failures = _check_caps_or_marker(p)
    assert len(failures) == 1, f"Bare `>=0` must fail cap-or-marker; got: {failures}"
    assert "foo" in failures[0]


def test_pre_1_bare_zero_with_cap_passes(tmp_path: Path) -> None:
    """End-to-end #164: ``foo>=0,<1`` is capped — acceptable."""
    p = _write_pyproject(tmp_path, ['"foo>=0,<1"'])
    assert _check_caps_or_marker(p) == []


# ---------------------------------------------------------------------------
# #165 — PyPI satisfiability applies to every >=X.Y.Z pin, not just pre-1.0.
# Network-free tests exercise the helper directly.
# ---------------------------------------------------------------------------


def test_constraint_satisfiable_when_match_exists() -> None:
    """A post-1.0 pin with a real published version is satisfiable."""
    assert _constraint_is_satisfiable("6.1.1", ["6.0.0", "6.1.0", "6.1.1", "7.0.0"]) is True


def test_constraint_satisfiable_ignores_upper_cap_for_fabricated_post_1_version() -> None:
    """Regression for #165 / C8 VERSION_FABRICATION: the helper only checks
    lower-bound existence, not caps. A ``>=6.2`` pin with no published 6.2
    but a published 7.0.0 is reported as satisfiable — the *upper-bound*
    cap (``<7`` in the real psutil case) is what prevents the fabrication
    from installing. This test documents the contract (renamed from the
    earlier "unsatisfiable"-prefixed name, which contradicted its own
    assertion and confused readers — coderabbit review on PR #171).
    """
    # `>=6.2` alone is satisfiable by 7.0.0.
    assert _constraint_is_satisfiable("6.2", ["6.0.0", "6.1.0", "6.1.1", "7.0.0"]) is True


def test_constraint_unsatisfiable_when_latest_below_minimum() -> None:
    """The real C8 catch: every published version is below the declared
    minimum. Example from rule C8 / PR #846: ``rank-bm25>=0.7.2`` when
    the latest release is 0.2.2.
    """
    assert _constraint_is_satisfiable("0.7.2", ["0.2.0", "0.2.1", "0.2.2"]) is False


def test_constraint_skips_unparseable_versions() -> None:
    """PyPI occasionally has non-PEP-440 version strings (e.g. legacy
    ``2022.01-preview``). These must not crash the helper; they're just
    skipped when checking the minimum.
    """
    assert _constraint_is_satisfiable("2.0.0", ["legacy-tag", "garbage", "2.1.0"]) is True
