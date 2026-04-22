"""CI gate for pip-audit — enforces an accepted-risks allowlist.

Usage (from CI):

    pip-audit . --format=json > audit.json || true
    python3 scripts/pip_audit_gate.py --audit audit.json \\
        --allowlist .github/accepted-risks.yml

Exits 0 if every reported vulnerability is allowlisted and every allowlist
entry is still valid (package installed, version spec satisfied, not expired,
actually matched by a current vulnerability). Exits 1 otherwise with a
human-readable report on stderr.

Part of epic-e-deps (hardening roadmap #158 / #161, finding E2).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class AllowlistEntry:
    """One row of the YAML allowlist.

    The `package` field is stored in PEP 503 canonical form (lowercase with
    `_` and `.` replaced by `-`) so matches are robust to distribution-name
    variants (`typing_extensions` vs `typing-extensions`).
    """

    advisory_id: str
    package: str
    version_spec: SpecifierSet
    reason: str
    expires_on: date


@dataclass
class GateResult:
    """Outcome of evaluating the audit against the allowlist."""

    failed: bool
    unknown_vulns: list[tuple[str, str, str]] = field(default_factory=list)
    stale_entries: list[str] = field(default_factory=list)
    unused_entries: list[str] = field(default_factory=list)


_REQUIRED_FIELDS = ("advisory_id", "package", "version_spec", "reason", "expires_on")


def load_allowlist(path: Path) -> list[AllowlistEntry]:
    """Parse the YAML allowlist into typed entries.

    Raises ValueError if the file is malformed or a required field is missing.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or "allowlist" not in raw:
        raise ValueError(f"{path}: missing top-level 'allowlist' key")
    entries: list[AllowlistEntry] = []
    for idx, item in enumerate(raw["allowlist"]):
        missing = [f for f in _REQUIRED_FIELDS if f not in item]
        if missing:
            raise ValueError(
                f"{path}: allowlist entry {idx} is missing required field(s): {', '.join(missing)}"
            )
        entries.append(
            AllowlistEntry(
                advisory_id=item["advisory_id"],
                package=str(canonicalize_name(item["package"])),
                version_spec=SpecifierSet(item["version_spec"]),
                reason=item["reason"],
                expires_on=_parse_date(item["expires_on"]),
            )
        )
    return entries


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _version_satisfies(spec: SpecifierSet, raw_version: str) -> bool:
    """Return True if the installed version provably satisfies the spec.

    If `raw_version` is not a valid PEP 440 version (pip-audit occasionally
    emits non-PEP-440 strings for git-installed packages), return False —
    the strict stance: if we can't prove satisfaction, treat the match as
    not-satisfied. A lenient fallback would silently let unknown version
    strings pass the allowlist, which is the wrong direction for a security
    gate (prior reviewer feedback, round 2).
    """
    try:
        return spec.contains(Version(raw_version), prereleases=True)
    except InvalidVersion:
        return False


def evaluate(  # noqa: C901 — gate policy is inherently multi-rule; splitting would hide the fail-path logic
    audit: dict[str, Any],
    allowlist: list[AllowlistEntry],
    *,
    today: date,
) -> GateResult:
    """Decide whether the audit + allowlist pair should fail the build.

    Rules (enforced in order):

    1. Every reported vulnerability must match a live allowlist entry (by
       canonical-package-name, satisfying version_spec, non-expired).
       Unmatched vulns → `unknown_vulns` (hard fail).
    2. Every expired allowlist entry → `stale_entries` (hard fail), even if
       no current vulnerability matches — prevents accepted risks lingering
       after remediation.
    3. Every allowlist entry whose package is not installed → `unused_entries`
       (hard fail) — prevents scope drift.
    4. Every allowlist entry whose package IS installed but whose advisory is
       no longer reported → `unused_entries` with "advisory not reported"
       reason (hard fail) — prevents accepted risks lingering after the
       upstream vulnerability is fixed.
    """
    result = GateResult(failed=False)

    # pip-audit emits `{name, version, vulns}` for audited packages and
    # `{name, skip_reason}` for entries it couldn't audit (e.g. editable
    # installs). Only the former carry a `version` key; the latter must be
    # filtered out before our per-vuln loop touches them.
    reported: list[tuple[str, str, str]] = []
    audited_names: set[str] = set()
    for dep in audit.get("dependencies", []):
        if "version" not in dep:
            continue
        pkg = str(canonicalize_name(dep["name"]))
        ver = dep["version"]
        audited_names.add(pkg)
        for vuln in dep.get("vulns", []):
            reported.append((pkg, ver, vuln["id"]))

    # Fail every expired entry up-front, regardless of whether any current
    # vulnerability matches. This is the "stale after remediation" guard.
    # `<= today` (not `< today`) means the entry expires *on* the stated date,
    # not the day after — the allowlist policy text says the build should fail
    # on expiry, so no one-day grace.
    used_indexes: set[int] = set()
    expired_indexes: set[int] = set()
    for idx, entry in enumerate(allowlist):
        if entry.expires_on <= today:
            expired_indexes.add(idx)
            result.stale_entries.append(
                f"{entry.advisory_id} ({entry.package}) expired {entry.expires_on}"
            )

    # Index allowlist by advisory_id → list of (idx, entry). One advisory ID
    # can legitimately appear more than once (shared CVE across packages, or
    # the same package across disjoint version ranges).
    entries_by_id: dict[str, list[tuple[int, AllowlistEntry]]] = {}
    for idx, entry in enumerate(allowlist):
        entries_by_id.setdefault(entry.advisory_id, []).append((idx, entry))

    # Match each reported vuln against a live (non-expired) entry.
    for pkg, ver, adv_id in reported:
        matched_idx: int | None = None
        for idx, entry in entries_by_id.get(adv_id, []):
            if idx in expired_indexes:
                continue
            if entry.package != pkg:
                continue
            if not _version_satisfies(entry.version_spec, ver):
                continue
            matched_idx = idx
            break
        if matched_idx is None:
            result.unknown_vulns.append((pkg, ver, adv_id))
        else:
            used_indexes.add(matched_idx)

    # Surface every allowlist entry that wasn't actually used. Two shapes:
    #   - package not installed in this audit scope
    #   - package installed but advisory not reported anymore (fix shipped)
    for idx, entry in enumerate(allowlist):
        if idx in expired_indexes:
            continue  # already counted as stale
        if entry.package not in audited_names:
            result.unused_entries.append(
                f"{entry.advisory_id} ({entry.package}) — package not installed"
            )
        elif idx not in used_indexes:
            result.unused_entries.append(
                f"{entry.advisory_id} ({entry.package}) — advisory not reported"
            )

    result.failed = bool(result.unknown_vulns or result.stale_entries or result.unused_entries)
    return result


def _render(result: GateResult) -> str:
    lines: list[str] = []
    if result.unknown_vulns:
        lines.append("Unknown vulnerabilities (not in allowlist):")
        for pkg, ver, adv in result.unknown_vulns:
            lines.append(f"  - {adv}  {pkg} {ver}")
    if result.stale_entries:
        lines.append("Expired allowlist entries:")
        for entry in result.stale_entries:
            lines.append(f"  - {entry}")
    if result.unused_entries:
        lines.append("Unused allowlist entries:")
        for entry in result.unused_entries:
            lines.append(f"  - {entry}")
    if not lines:
        return "pip-audit gate: OK"
    return "pip-audit gate FAILED:\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Translates malformed inputs into concise stderr messages
    rather than raw tracebacks.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit", required=True, type=Path, help="pip-audit JSON output")
    ap.add_argument("--allowlist", required=True, type=Path, help="Accepted-risks YAML")
    args = ap.parse_args(argv)

    try:
        audit = json.loads(args.audit.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"pip-audit gate FAILED: could not read audit JSON: {exc}", file=sys.stderr)
        return 1

    try:
        allowlist = load_allowlist(args.allowlist)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"pip-audit gate FAILED: could not load allowlist: {exc}", file=sys.stderr)
        return 1

    # Using system-local date is intentional: the allowlist's expires_on is
    # a calendar date, not a timezone-aware timestamp.
    result = evaluate(
        audit,
        allowlist,
        today=date.today(),  # noqa: DTZ011
    )

    msg = _render(result)
    stream = sys.stderr if result.failed else sys.stdout
    print(msg, file=stream)
    return 1 if result.failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
