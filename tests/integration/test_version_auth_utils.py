"""Integration tests for version utilities.

Covers:
  - version.py                      — VersionInfo, parse_version, bump_version
"""

from __future__ import annotations

import pytest

from file_organizer.version import (
    VersionInfo,
    bump_version,
    get_version,
    get_version_info,
    parse_version,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# VersionInfo
# ---------------------------------------------------------------------------


class TestVersionInfo:
    def test_str_without_pre_release(self) -> None:
        v = VersionInfo(major=1, minor=2, patch=3)
        assert str(v) == "1.2.3"

    def test_str_with_pre_release(self) -> None:
        v = VersionInfo(major=1, minor=0, patch=0, pre_release="alpha.1")
        assert str(v) == "1.0.0-alpha.1"

    def test_is_pre_release_true(self) -> None:
        v = VersionInfo(1, 0, 0, pre_release="alpha")
        assert v.is_pre_release is True

    def test_is_pre_release_false(self) -> None:
        v = VersionInfo(1, 0, 0)
        assert v.is_pre_release is False

    def test_base_version(self) -> None:
        v = VersionInfo(2, 3, 4, pre_release="beta")
        assert v.base_version == "2.3.4"

    def test_lt_by_major(self) -> None:
        assert VersionInfo(1, 0, 0) < VersionInfo(2, 0, 0)

    def test_lt_by_minor(self) -> None:
        assert VersionInfo(1, 0, 0) < VersionInfo(1, 1, 0)

    def test_lt_by_patch(self) -> None:
        assert VersionInfo(1, 0, 0) < VersionInfo(1, 0, 1)

    def test_pre_release_lt_release(self) -> None:
        assert VersionInfo(1, 0, 0, pre_release="alpha") < VersionInfo(1, 0, 0)

    def test_release_not_lt_release(self) -> None:
        assert not (VersionInfo(1, 0, 0) < VersionInfo(1, 0, 0))

    def test_gt(self) -> None:
        assert VersionInfo(2, 0, 0) > VersionInfo(1, 0, 0)

    def test_le_equal(self) -> None:
        v = VersionInfo(1, 0, 0)
        assert v <= VersionInfo(1, 0, 0)

    def test_le_less(self) -> None:
        assert VersionInfo(1, 0, 0) <= VersionInfo(2, 0, 0)

    def test_ge_equal(self) -> None:
        assert VersionInfo(1, 0, 0) >= VersionInfo(1, 0, 0)

    def test_ge_greater(self) -> None:
        assert VersionInfo(2, 0, 0) >= VersionInfo(1, 0, 0)

    def test_lt_non_version_returns_not_implemented(self) -> None:
        v = VersionInfo(1, 0, 0)
        result = v.__lt__("other")
        assert result is NotImplemented


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_simple_version(self) -> None:
        v = parse_version("1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3

    def test_pre_release(self) -> None:
        v = parse_version("2.0.0-alpha.1")
        assert v.pre_release == "alpha.1"

    def test_no_pre_release(self) -> None:
        v = parse_version("1.0.0")
        assert v.pre_release is None

    def test_zero_version(self) -> None:
        v = parse_version("0.0.0")
        assert v.major == 0

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_version("not-a-version")

    def test_missing_patch_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_version("1.2")

    def test_strips_whitespace(self) -> None:
        v = parse_version("  1.2.3  ")
        assert v.major == 1


# ---------------------------------------------------------------------------
# get_version / get_version_info
# ---------------------------------------------------------------------------


class TestGetVersion:
    def test_returns_string(self) -> None:
        assert len(get_version()) > 0

    def test_non_empty(self) -> None:
        assert len(get_version()) > 0


class TestGetVersionInfo:
    def test_returns_version_info(self) -> None:
        assert isinstance(get_version_info(), VersionInfo)

    def test_major_is_int(self) -> None:
        info = get_version_info()
        assert info.major >= 0


# ---------------------------------------------------------------------------
# bump_version
# ---------------------------------------------------------------------------


class TestBumpVersion:
    def test_bump_patch(self) -> None:
        assert bump_version("1.2.3", "patch") == "1.2.4"

    def test_bump_minor(self) -> None:
        assert bump_version("1.2.3", "minor") == "1.3.0"

    def test_bump_major(self) -> None:
        assert bump_version("1.2.3", "major") == "2.0.0"

    def test_bump_drops_pre_release(self) -> None:
        result = bump_version("1.0.0-alpha", "patch")
        assert "-" not in result

    def test_invalid_part_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            bump_version("1.0.0", "build")
