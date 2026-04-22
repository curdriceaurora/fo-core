"""Unit tests for scripts/pip_audit_gate.py (E2 of the hardening roadmap).

The gate takes pip-audit JSON output + an allowlist YAML and decides whether
to fail the build. Every behavioral guarantee we rely on gets a test here.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from textwrap import dedent

import pytest

# Load scripts/pip_audit_gate.py by path — it isn't a package, so we can't import it
# via `from scripts.pip_audit_gate import ...` without adjusting sys.path. Loading
# explicitly keeps the test self-contained.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "scripts" / "pip_audit_gate.py"
_COMMITTED_ALLOWLIST = _REPO_ROOT / ".github" / "accepted-risks.yml"
_spec = importlib.util.spec_from_file_location("pip_audit_gate", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
pip_audit_gate = importlib.util.module_from_spec(_spec)
sys.modules["pip_audit_gate"] = pip_audit_gate
_spec.loader.exec_module(pip_audit_gate)

AllowlistEntry = pip_audit_gate.AllowlistEntry
GateResult = pip_audit_gate.GateResult
evaluate = pip_audit_gate.evaluate
load_allowlist = pip_audit_gate.load_allowlist


def _audit_payload(vulns: list[dict]) -> dict:
    """Shape a pip-audit JSON payload the way pip-audit v2.7+ emits it."""
    by_pkg: dict[tuple[str, str], list[dict]] = {}
    for v in vulns:
        by_pkg.setdefault((v["package"], v["version"]), []).append({"id": v["id"]})
    return {
        "dependencies": [
            {"name": pkg, "version": ver, "vulns": v} for (pkg, ver), v in by_pkg.items()
        ],
    }


def _audit_with_installed(pkgs: list[tuple[str, str]]) -> dict:
    """Audit payload for packages with no vulns (for allowlist-unused tests)."""
    return {"dependencies": [{"name": n, "version": v, "vulns": []} for n, v in pkgs]}


@pytest.fixture
def allowlist_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "allow.yml"
    p.write_text(
        dedent(
            """
            allowlist:
              - advisory_id: GHSA-wj6h-64fc-37mp
                package: ecdsa
                version_spec: ">=0.13.0,<1"
                reason: "HS256 only"
                expires_on: "2099-12-31"
            """
        ).strip()
    )
    return p


def test_load_allowlist_parses_each_entry(allowlist_yaml: Path) -> None:
    entries = load_allowlist(allowlist_yaml)
    assert len(entries) == 1
    e = entries[0]
    assert e.advisory_id == "GHSA-wj6h-64fc-37mp"
    assert e.package == "ecdsa"
    assert e.expires_on == date(2099, 12, 31)


def test_evaluate_passes_with_empty_allowlist_and_no_vulns(tmp_path: Path) -> None:
    """Baseline: empty allowlist + no-vulnerability audit = no failure."""
    p = tmp_path / "empty.yml"
    p.write_text("allowlist: []\n")
    audit = _audit_with_installed([("requests", "2.31.0")])
    result = evaluate(audit, load_allowlist(p), today=date(2026, 4, 22))
    assert result.failed is False
    assert result.unknown_vulns == []
    assert result.stale_entries == []
    assert result.unused_entries == []


def test_evaluate_flags_allowlist_entry_whose_advisory_is_no_longer_reported(
    allowlist_yaml: Path,
) -> None:
    """Regression (R2 review): if an allowlist entry's package is installed but
    the advisory is no longer reported (upstream fix shipped), the entry must
    fail the gate so accepted risks don't linger after remediation.

    ecdsa is in the allowlist and installed, but no vulnerability is reported
    for it — the fix has shipped upstream and the allowlist should be cleaned.
    """
    audit = _audit_with_installed([("ecdsa", "0.18.0")])
    result = evaluate(audit, load_allowlist(allowlist_yaml), today=date(2026, 4, 22))
    assert result.failed is True
    assert any("ecdsa" in e and "advisory not reported" in e for e in result.unused_entries)


def test_evaluate_passes_when_vuln_is_allowlisted(allowlist_yaml: Path) -> None:
    audit = _audit_payload([{"package": "ecdsa", "version": "0.18.0", "id": "GHSA-wj6h-64fc-37mp"}])
    result = evaluate(audit, load_allowlist(allowlist_yaml), today=date(2026, 4, 22))
    assert result.failed is False


def test_evaluate_fails_on_unknown_vuln(allowlist_yaml: Path) -> None:
    # ecdsa installed AND reports its allowlisted vuln (so that entry is used);
    # a separate unknown vuln on requests must still fail the gate.
    audit = {
        "dependencies": [
            {
                "name": "requests",
                "version": "2.31.0",
                "vulns": [{"id": "GHSA-xxxx-xxxx-xxxx"}],
            },
            {
                "name": "ecdsa",
                "version": "0.18.0",
                "vulns": [{"id": "GHSA-wj6h-64fc-37mp"}],
            },
        ],
    }
    result = evaluate(audit, load_allowlist(allowlist_yaml), today=date(2026, 4, 22))
    assert result.failed is True
    assert result.unknown_vulns == [("requests", "2.31.0", "GHSA-xxxx-xxxx-xxxx")]


def test_entry_fails_on_expires_on_date_no_grace_period(tmp_path: Path) -> None:
    """Regression (R3 review): allowlist entries must fail the gate ON the
    stated `expires_on` date, not the day after. The policy says the build
    fails "on expiry" — no one-day grace.
    """
    p = tmp_path / "allow.yml"
    p.write_text(
        dedent(
            """
            allowlist:
              - advisory_id: GHSA-boundary
                package: somepkg
                version_spec: ">=0"
                reason: "boundary test"
                expires_on: "2026-04-22"
            """
        ).strip()
    )
    # today == expires_on → entry must be reported as expired.
    audit = _audit_payload([{"package": "somepkg", "version": "1.0", "id": "GHSA-boundary"}])
    result = evaluate(audit, load_allowlist(p), today=date(2026, 4, 22))
    assert result.failed is True
    assert any("GHSA-boundary" in entry for entry in result.stale_entries), (
        f"Entry expiring today must be flagged stale, got: {result}"
    )


def test_evaluate_fails_on_expired_allowlist_entry(tmp_path: Path) -> None:
    p = tmp_path / "allow.yml"
    p.write_text(
        dedent(
            """
            allowlist:
              - advisory_id: GHSA-expired
                package: somepkg
                version_spec: ">=0"
                reason: "expired"
                expires_on: "2020-01-01"
            """
        ).strip()
    )
    audit = _audit_payload([{"package": "somepkg", "version": "1.0", "id": "GHSA-expired"}])
    result = evaluate(audit, load_allowlist(p), today=date(2026, 4, 22))
    assert result.failed is True
    assert any("GHSA-expired" in entry for entry in result.stale_entries)


def test_evaluate_flags_allowlist_entry_for_uninstalled_package(
    allowlist_yaml: Path,
) -> None:
    # ecdsa is in the allowlist but NOT in the audit payload -> unused.
    audit = _audit_with_installed([("requests", "2.31.0")])
    result = evaluate(audit, load_allowlist(allowlist_yaml), today=date(2026, 4, 22))
    assert result.failed is True
    assert any("ecdsa" in entry for entry in result.unused_entries)


def test_evaluate_fails_when_version_spec_mismatch(tmp_path: Path) -> None:
    # Entry allows only <1, but installed version is 1.2.0.
    p = tmp_path / "allow.yml"
    p.write_text(
        dedent(
            """
            allowlist:
              - advisory_id: GHSA-mismatch
                package: pkg
                version_spec: "<1"
                reason: "only pre-1.0 has the unreachable codepath"
                expires_on: "2099-12-31"
            """
        ).strip()
    )
    audit = _audit_payload([{"package": "pkg", "version": "1.2.0", "id": "GHSA-mismatch"}])
    result = evaluate(audit, load_allowlist(p), today=date(2026, 4, 22))
    assert result.failed is True
    assert any(pkg == "pkg" and adv == "GHSA-mismatch" for pkg, _ver, adv in result.unknown_vulns)


def test_committed_allowlist_passes_against_empty_audit() -> None:
    """Regression: the committed `.github/accepted-risks.yml` must pass the
    gate against a no-vulnerability base-project audit.

    Previously the committed file seeded two entries (ecdsa, diskcache) whose
    packages are not installed by `pip install -e .` — the gate correctly
    flagged both as unused-entry failures (PR #163 review finding). Fix:
    committed seeds removed; future entries must match the base audit scope.
    """
    audit = _audit_with_installed([])  # no packages installed, no vulns
    result = evaluate(audit, load_allowlist(_COMMITTED_ALLOWLIST), today=date(2026, 4, 22))
    assert result.failed is False, (
        f"Committed allowlist must be valid against base audit scope, got: "
        f"unknown={result.unknown_vulns} stale={result.stale_entries} "
        f"unused={result.unused_entries}"
    )


def test_load_allowlist_raises_valueerror_on_missing_field(tmp_path: Path) -> None:
    """Regression: malformed allowlist entries must raise ValueError, not KeyError.

    The docstring promises ValueError; bare dict access would raise KeyError
    with a cryptic message. This pins the contract (PR #163 review).
    """
    p = tmp_path / "bad.yml"
    p.write_text(
        dedent(
            """
            allowlist:
              - advisory_id: GHSA-incomplete
                package: somepkg
                # missing version_spec, reason, expires_on
            """
        ).strip()
    )
    with pytest.raises(ValueError, match="missing required field"):
        load_allowlist(p)


def test_evaluate_matches_multiple_entries_with_shared_advisory(tmp_path: Path) -> None:
    """Regression: when two allowlist entries share an advisory ID (shared CVE
    across packages, or the same package across version ranges), both must be
    considered — not silently overwritten by dict keying on advisory_id alone.
    """
    p = tmp_path / "multi.yml"
    p.write_text(
        dedent(
            """
            allowlist:
              - advisory_id: GHSA-shared
                package: pkg_a
                version_spec: ">=0,<2"
                reason: "pkg_a unreachable"
                expires_on: "2099-12-31"
              - advisory_id: GHSA-shared
                package: pkg_b
                version_spec: ">=0,<2"
                reason: "pkg_b unreachable"
                expires_on: "2099-12-31"
            """
        ).strip()
    )
    # Both packages installed + both report the shared advisory.
    audit = {
        "dependencies": [
            {"name": "pkg_a", "version": "1.0", "vulns": [{"id": "GHSA-shared"}]},
            {"name": "pkg_b", "version": "1.0", "vulns": [{"id": "GHSA-shared"}]},
        ],
    }
    result = evaluate(audit, load_allowlist(p), today=date(2026, 4, 22))
    assert result.failed is False, f"Both entries should match; got unknown={result.unknown_vulns}"


def test_seeded_out_of_scope_entry_fails_base_audit(tmp_path: Path) -> None:
    """Regression: seeding an allowlist entry for a package not installed in
    the audit scope is a gate failure by design.

    Pins down the exact CI-failure mode that PR #163 review surfaced: a
    contributor adding a seed entry for an optional-extra package will fail
    the base security audit, forcing the entry to be justified or moved to
    an extras-specific audit. Schema matches the previously-committed shape
    (two entries, one clearly-optional package).
    """
    seed = tmp_path / "accepted-risks.yml"
    seed.write_text(
        dedent(
            """
            allowlist:
              - advisory_id: GHSA-w8v5-vhqr-4h9v
                package: diskcache
                version_spec: ">=0,<6"
                reason: "Transitive via llama-cpp-python (optional [llama] extra)."
                expires_on: "2099-12-31"
            """
        ).strip()
    )
    # Base audit: diskcache is not installed because [llama] extra is absent.
    audit = _audit_with_installed([("requests", "2.31.0")])
    result = evaluate(audit, load_allowlist(seed), today=date(2026, 4, 22))
    assert result.failed is True
    assert any("diskcache" in entry for entry in result.unused_entries), (
        f"Expected the diskcache entry to be flagged unused, got: {result.unused_entries}"
    )


def test_evaluate_skips_pip_audit_entries_without_version(tmp_path: Path) -> None:
    """Regression (R2 CI crash): pip-audit emits `{name, skip_reason}` for
    packages it can't audit (e.g. editable installs); our gate must not
    raise KeyError on those, just ignore them.
    """
    p = tmp_path / "empty.yml"
    p.write_text("allowlist: []\n")
    audit = {
        "dependencies": [
            {"name": "real-pkg", "version": "1.0", "vulns": []},
            {"name": "editable-pkg", "skip_reason": "Dependency not found on PyPI"},
        ],
    }
    # Should not raise KeyError; should treat it as a clean audit.
    result = evaluate(audit, load_allowlist(p), today=date(2026, 4, 22))
    assert result.failed is False


def test_evaluate_matches_canonical_package_names(tmp_path: Path) -> None:
    """Regression (R2 review): allowlist + audit must match across PEP 503
    canonical-name equivalents (`typing_extensions` vs `typing-extensions`,
    `zope.interface` vs `zope-interface`, mixed case).
    """
    p = tmp_path / "canonical.yml"
    p.write_text(
        dedent(
            """
            allowlist:
              - advisory_id: GHSA-canonical
                package: typing_extensions
                version_spec: ">=0"
                reason: "canonical-name test"
                expires_on: "2099-12-31"
            """
        ).strip()
    )
    # Audit uses dashed form; allowlist uses underscored form → should match.
    audit = _audit_payload(
        [{"package": "typing-extensions", "version": "4.9.0", "id": "GHSA-canonical"}]
    )
    result = evaluate(audit, load_allowlist(p), today=date(2026, 4, 22))
    assert result.failed is False, f"Canonical-name match failed: {result}"


def test_evaluate_strict_on_invalid_version(tmp_path: Path) -> None:
    """Regression (R2 review): if pip-audit emits a non-PEP-440 version string
    for an allowlisted package, we MUST NOT silently treat it as satisfying
    the allowlist — the safe default for a security gate is "unknown means
    unmatched".
    """
    p = tmp_path / "strict.yml"
    p.write_text(
        dedent(
            """
            allowlist:
              - advisory_id: GHSA-gitinstall
                package: somepkg
                version_spec: ">=0,<1"
                reason: "unreachable codepath"
                expires_on: "2099-12-31"
            """
        ).strip()
    )
    audit = _audit_payload(
        [
            {
                "package": "somepkg",
                "version": "git+https://example.invalid/pkg",
                "id": "GHSA-gitinstall",
            }
        ]
    )
    result = evaluate(audit, load_allowlist(p), today=date(2026, 4, 22))
    assert result.failed is True
    assert any(adv == "GHSA-gitinstall" for _, _, adv in result.unknown_vulns), (
        f"Invalid-version vuln must be treated as unmatched, got: {result}"
    )


def test_main_converts_malformed_audit_to_concise_error(tmp_path: Path, capsys) -> None:
    """Regression (R2 review): malformed audit.json produces a concise
    'pip-audit gate FAILED: could not read audit JSON' stderr message, not
    a raw JSONDecodeError traceback.
    """
    bad_audit = tmp_path / "bad.json"
    bad_audit.write_text("{not valid json")
    allowlist = tmp_path / "empty.yml"
    allowlist.write_text("allowlist: []\n")

    exit_code = pip_audit_gate.main(["--audit", str(bad_audit), "--allowlist", str(allowlist)])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "could not read audit JSON" in captured.err
    # And no traceback keywords in stderr:
    assert "Traceback" not in captured.err
