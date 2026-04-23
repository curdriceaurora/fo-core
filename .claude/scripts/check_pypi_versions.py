#!/usr/bin/env python3
"""Verify `>=` pins in pyproject.toml are satisfiable and bounded.

Two independent checks are bundled into one script (both run by default):

1. **PyPI satisfiability** (the original check):
   A constraint ``package>=X.Y.Z`` is satisfiable when at least one published
   version of *package* is >= X.Y.Z.  This catches cases like
   ``rank-bm25>=0.7.2`` where the latest published version is 0.2.2 — no
   installation is possible, so the requirement is effectively broken.
   Runs on **every** ``>=`` pin (pre-1.0 and post-1.0) — the ``psutil>=6.2``
   fabrication that originally motivated this script was post-1.0 and was
   missed by the earlier pre-1.0-only gate (#165).

2. **Pre-1.0 cap-or-marker** (E3 of the hardening roadmap, #158):
   Any pre-1.0 ``>=`` pin must have either an upper bound (e.g. ``<1``) or
   the exact marker comment ``# 0.x — unstable API, keep >=`` on the same
   line. Without one of the two, a minor-version bump can break consumers
   without warning. The marker is the explicit opt-out; the cap is the
   default. This check reads the file as text so it sees inline comments.

Flags:
  --pyproject PATH           — path to pyproject.toml (default: ./pyproject.toml)
  --check-pre-1-0-only       — skip the PyPI network check (for offline tests)
  --skip-pre-1-0-check       — skip the cap-or-marker check (rarely needed)

Exit codes:
  0 — all checks pass (or network unavailable for the PyPI check)
  1 — at least one check failed
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any
from urllib import error, parse, request


def _version_is_pre_1(version: str) -> bool:
    """Return True when the PEP 440 *release* major component is 0.

    Covers every pre-1.0 shape: ``0.7.2``, ``0.0.19``, bare ``0``, and
    pre-releases like ``0rc1`` / ``0a1`` / ``0b0``. Previously only
    ``"0."`` was recognised, which silently let uncapped ``foo>=0`` pins
    bypass the E3 cap-or-marker rule (#164).

    Uses ``packaging.version.Version`` so PEP 440 epoch syntax is
    handled correctly — ``0!1.2`` has epoch ``0`` and major release ``1``,
    i.e. *post-1.0*. A naive leading-digit regex would read the epoch
    and misclassify it (codex P2 review on PR #171).
    """
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:  # pragma: no cover — packaging is pinned in pyproject.toml
        # Fallback to the regex path if packaging somehow isn't available.
        match = re.match(r"^(\d+)", version)
        return match is not None and int(match.group(1)) == 0
    try:
        return Version(version).major == 0
    except InvalidVersion:
        # Non-PEP-440 garbage — treat as post-1.0 so the pre-1.0 rule
        # doesn't enforce a cap on something we can't classify.
        return False


def _version_tuple(version: str) -> tuple[int, ...]:
    """Return a comparable integer tuple for a version string."""
    parts = re.split(r"[^0-9]+", version.split("+")[0].split("a")[0].split("b")[0].split("rc")[0])
    return tuple(int(p) for p in parts if p.isdigit())


def _get_all_versions(package: str) -> list[str] | None:
    """Return all published versions for *package* from PyPI.

    Returns None on network error; the caller should skip the check.
    """
    url = f"https://pypi.org/pypi/{parse.quote(package, safe='')}/json"
    try:
        with request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return list(data.get("releases", {}).keys())
    except error.HTTPError as exc:
        if exc.code == 404:
            return []  # package not found — let the caller report the error
        return None  # unexpected HTTP error — don't block commit
    except OSError:
        return None  # network unavailable


def _constraint_is_satisfiable(minimum: str, available: list[str]) -> bool:
    """Return True if any version in *available* is >= *minimum*."""
    min_tuple = _version_tuple(minimum)
    for ver in available:
        try:
            if _version_tuple(ver) >= min_tuple:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _collect_deps(data: dict[str, Any]) -> list[str]:
    deps: list[str] = []
    deps.extend(data.get("project", {}).get("dependencies", []))
    for group in data.get("project", {}).get("optional-dependencies", {}).values():
        deps.extend(group)
    return deps


_CONSTRAINT_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)"  # package name
    r"[^;]*"  # extras / markers prefix (skip semicolons)
    r">=\s*([0-9][0-9a-zA-Z._-]*)"  # >= version
)


_KEEP_MARKER = "# 0.x — unstable API, keep >="
# Extracts a quoted dependency string from a TOML list plus any trailing comment.
# Matches lines like `    "name[extra]>=0.x.y,<1; marker",  # comment` — accepts
# both TOML basic (double-quoted) and literal (single-quoted) strings so the rule
# can't be bypassed by swapping quote style.
_DEP_LINE_RE = re.compile(r'^\s*(?:"([^"]+)"|\'([^\']+)\')(.*)$')


def _check_caps_or_marker(pyproject_path: Path) -> list[str]:
    """Enforce E3: every pre-1.0 `>=` pin needs a `<` cap OR the keep marker.

    Reads the file as text so inline comments are visible (tomllib strips them),
    then delegates specifier parsing to `packaging.requirements.Requirement` so
    we correctly distinguish version specifiers from environment markers — a
    `"pkg>=0.2; python_version < '3.12'"` string has no version cap even though
    `<` appears in the marker.
    """
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except ImportError:  # pragma: no cover — packaging is pinned in pyproject.toml
        return []

    failures: list[str] = []
    for lineno, raw_line in enumerate(pyproject_path.read_text().splitlines(), start=1):
        match = _DEP_LINE_RE.match(raw_line)
        if not match:
            continue
        # group(1) is the double-quoted body, group(2) the single-quoted body;
        # exactly one will be non-None. group(3) is everything after the closing
        # quote (inline comment, comma, etc.).
        dep_str = match.group(1) or match.group(2)
        trailing = match.group(3)
        try:
            req = Requirement(dep_str)
        except InvalidRequirement:
            continue  # not a PEP 508 requirement — skip silently

        # Only audit bare `>=0.X` pins. `~=0.X` is already bounded by PEP 440
        # semantics (`~=0.18` means `>=0.18,<0.19`), and `==0.X` is pinned exactly.
        # Those forms are safe; this rule targets unbounded `>=`.
        #
        # `_version_is_pre_1` catches every pre-1.0 shape — `0`, `0rc1`,
        # `0.0.19`, `0.7.2` — not just `"0."`-prefixed strings (#164).
        has_pre_1_lower = any(
            s.operator == ">=" and _version_is_pre_1(s.version) for s in req.specifier
        )
        if not has_pre_1_lower:
            continue

        # A cap is a true upper bound. `<`, `<=` are strict upper bounds.
        # `==` pins exactly (no versions above are allowed). `~=X.Y` is a
        # compatible-release cap (`>=X.Y,<X.(Y+1)`). `!=` is NOT a cap — it
        # only excludes one specific version and leaves higher versions
        # unbounded (`foo>=0.2,!=0.3` still allows 0.4, 1.0, etc.).
        has_cap = any(s.operator in ("<", "<=", "==", "~=") for s in req.specifier)
        has_marker = _KEEP_MARKER in trailing
        if has_cap or has_marker:
            continue

        failures.append(
            f"  pyproject.toml:{lineno}: {req.name} is a pre-1.0 pin without "
            f"an upper-bound cap; either add `<1` (or a tighter bound) or add "
            f"the comment marker `{_KEEP_MARKER}` on the same line."
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    """Entry point. Dispatches the two checks based on CLI flags.

    Complexity noqa: this is a linear CLI dispatcher — splitting into helpers
    would add indirection without reducing conceptual complexity.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml",
    )
    ap.add_argument(
        "--check-pre-1-0-only",
        action="store_true",
        help="Run only the cap-or-marker check (skip PyPI network calls).",
    )
    ap.add_argument(
        "--skip-pre-1-0-check",
        action="store_true",
        help="Run only the PyPI satisfiability check.",
    )
    args = ap.parse_args(argv)

    pyproject = args.pyproject
    if not pyproject.exists():
        return 0

    # Cap-or-marker check (E3) — offline, purely textual.
    if not args.skip_pre_1_0_check:
        caps_failures = _check_caps_or_marker(pyproject)
        if caps_failures:
            print(
                "pypi-version-check: pre-1.0 pins missing an upper-bound "
                "cap or the keep-as-is marker:"
            )
            for line in caps_failures:
                print(line, file=sys.stderr)
            return 1

    # Early exit when only the E3 check was requested.
    if args.check_pre_1_0_only:
        return 0

    with open(pyproject, "rb") as fh:
        data = tomllib.load(fh)

    failures: list[str] = []
    network_ok = True
    # Cache results to avoid duplicate PyPI calls for the same package
    _version_cache: dict[str, list[str] | None] = {}

    for dep in _collect_deps(data):
        dep = dep.strip()
        dep_no_marker = dep.split(";")[0].strip()
        match = _CONSTRAINT_RE.match(dep_no_marker)
        if not match:
            continue

        package = match.group(1)
        version = match.group(2)

        # PyPI satisfiability applies to every ``>=X.Y.Z`` pin — not just
        # pre-1.0. The ``psutil>=6.2,<7`` fabrication that originally
        # motivated this script (PR #846, rule C8) was post-1.0 and got
        # through under the old pre-1.0-only gate. The cap-or-marker rule
        # still scopes to pre-1.0 (that's the E3 policy); PyPI
        # satisfiability runs on everything (#165).

        pkg_norm = package.lower().replace("_", "-")

        if pkg_norm not in _version_cache:
            _version_cache[pkg_norm] = _get_all_versions(pkg_norm)

        available = _version_cache[pkg_norm]
        if available is None:
            network_ok = False
            break  # network unavailable — skip remaining checks

        if not available:
            failures.append(
                f"  {package}>={version} — package {package!r} not found on PyPI at all"
            )
        elif not _constraint_is_satisfiable(version, available):
            latest = max(available, key=_version_tuple, default="?")
            failures.append(
                f"  {package}>={version} — no published version >= {version} (latest is {latest})"
            )

    if not network_ok:
        print("pypi-version-check: network unavailable, skipping version validation.")
        return 0

    if failures:
        print("pypi-version-check: unsatisfiable version constraints in pyproject.toml:")
        for line in failures:
            print(line)
        print(
            "\nFix: verify the package version with "
            "`pip index versions <package>` before updating pyproject.toml."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
