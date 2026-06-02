"""Tests for services.copilot.rules.actions.

Covers apply_hardlink, apply_symlink, ConflictStrategy, and LinkResult for:
- Successful link creation
- Dry-run (no filesystem changes)
- All four conflict-resolution strategies (skip, overwrite, rename_new, rename_existing)
- Cross-volume failure path (EXDEV) for hardlinks
- Unsupported filesystem (EPERM) for hardlinks
- Source-is-symlink warning for symlinks
- Template variable expansion in destination
"""

from __future__ import annotations

import errno
import os
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from services.copilot.rules.actions import (
    ConflictStrategy,
    LinkResult,
    _find_free_name,
    _resolve_dest,
    apply_hardlink,
    apply_symlink,
)

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# ConflictStrategy enum
# ---------------------------------------------------------------------------


class TestConflictStrategy:
    """Test ConflictStrategy enum values."""

    def test_all_members(self) -> None:
        expected = {"skip", "overwrite", "rename_new", "rename_existing"}
        assert {s.value for s in ConflictStrategy} == expected

    def test_from_string(self) -> None:
        assert ConflictStrategy("skip") is ConflictStrategy.SKIP
        assert ConflictStrategy("overwrite") is ConflictStrategy.OVERWRITE
        assert ConflictStrategy("rename_new") is ConflictStrategy.RENAME_NEW
        assert ConflictStrategy("rename_existing") is ConflictStrategy.RENAME_EXISTING


# ---------------------------------------------------------------------------
# _resolve_dest helper
# ---------------------------------------------------------------------------


class TestResolveDest:
    """Test _resolve_dest helper."""

    def test_plain_file_dest(self, tmp_path: Path) -> None:
        src = tmp_path / "photo.jpg"
        result = _resolve_dest(src, str(tmp_path / "out" / "photo.jpg"))
        assert result == tmp_path / "out" / "photo.jpg"

    def test_directory_dest_appends_filename(self, tmp_path: Path) -> None:
        src = tmp_path / "photo.jpg"
        dest_dir = tmp_path / "out"
        dest_dir.mkdir()
        result = _resolve_dest(src, str(dest_dir))
        assert result == dest_dir / "photo.jpg"

    def test_template_name(self, tmp_path: Path) -> None:
        src = tmp_path / "report.pdf"
        result = _resolve_dest(src, "/archive/{name}")
        assert result == Path("/archive/report.pdf")

    def test_template_stem_and_ext(self, tmp_path: Path) -> None:
        src = tmp_path / "report.pdf"
        result = _resolve_dest(src, "/archive/{stem}.{ext}")
        assert result == Path("/archive/report.pdf")

    def test_tilde_expansion(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        result = _resolve_dest(src, "~/Documents/file.txt")
        assert not str(result).startswith("~")


# ---------------------------------------------------------------------------
# _find_free_name helper
# ---------------------------------------------------------------------------


class TestFindFreeName:
    """Test _find_free_name helper."""

    def test_no_existing_file_returns_counter_1(self, tmp_path: Path) -> None:
        base = tmp_path / "a.txt"
        base.write_text("original")
        result = _find_free_name(base)
        assert result == tmp_path / "a_1.txt"

    def test_skips_existing_counters(self, tmp_path: Path) -> None:
        base = tmp_path / "a.txt"
        base.write_text("original")
        (tmp_path / "a_1.txt").write_text("first")
        (tmp_path / "a_2.txt").write_text("second")
        result = _find_free_name(base)
        assert result == tmp_path / "a_3.txt"

    def test_raises_when_exhausted(self, tmp_path: Path) -> None:
        base = tmp_path / "a.txt"
        base.write_text("original")
        # All candidate paths appear occupied; exhaustion triggers OSError
        with patch("pathlib.Path.exists", return_value=True):
            with pytest.raises(OSError, match="Could not find a free name"):
                _find_free_name(base, max_attempts=1)


# ---------------------------------------------------------------------------
# LinkResult dataclass
# ---------------------------------------------------------------------------


class TestLinkResult:
    """Test LinkResult dataclass defaults and fields."""

    def test_defaults(self, tmp_path: Path) -> None:
        r = LinkResult(
            success=True,
            source=tmp_path / "src.txt",
            destination=tmp_path / "dst.txt",
            dry_run=False,
        )
        assert r.skipped is False
        assert r.message == ""

    def test_failed_result(self, tmp_path: Path) -> None:
        r = LinkResult(
            success=False,
            source=tmp_path / "src.txt",
            destination=tmp_path / "dst.txt",
            dry_run=False,
            message="oops",
        )
        assert r.success is False
        assert r.message == "oops"


# ---------------------------------------------------------------------------
# apply_hardlink — success path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="hardlink inode check differs on Windows")
class TestApplyHardlinkSuccess:
    """Test apply_hardlink creates a hardlink on the same filesystem."""

    def test_creates_hardlink(self, tmp_path: Path) -> None:
        src = tmp_path / "photo.jpg"
        src.write_bytes(b"data")
        dest_dir = tmp_path / "out"
        dest_str = str(dest_dir / "photo.jpg")

        result = apply_hardlink(src, dest_str)

        assert result.success is True
        assert result.dry_run is False
        assert result.skipped is False
        link = dest_dir / "photo.jpg"
        assert link.exists()
        assert os.stat(src).st_ino == os.stat(link).st_ino

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        src.write_text("hello")
        dest_str = str(tmp_path / "a" / "b" / "c" / "file.txt")

        result = apply_hardlink(src, dest_str)

        assert result.success is True
        assert (tmp_path / "a" / "b" / "c" / "file.txt").exists()

    def test_template_variables(self, tmp_path: Path) -> None:
        src = tmp_path / "invoice.pdf"
        src.write_bytes(b"pdf")
        dest_str = str(tmp_path / "docs" / "{name}")

        result = apply_hardlink(src, dest_str)

        assert result.success is True
        assert (tmp_path / "docs" / "invoice.pdf").exists()

    def test_string_conflict_strategy(self, tmp_path: Path) -> None:
        """String values for conflict parameter are accepted."""
        src = tmp_path / "x.txt"
        src.write_text("hi")
        result = apply_hardlink(src, str(tmp_path / "out" / "x.txt"), conflict="rename_new")
        assert result.success is True


# ---------------------------------------------------------------------------
# apply_hardlink — dry-run
# ---------------------------------------------------------------------------


class TestApplyHardlinkDryRun:
    """Dry-run must not create any files."""

    def test_dry_run_does_not_create_file(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        src.write_text("content")
        dest_str = str(tmp_path / "out" / "file.txt")

        result = apply_hardlink(src, dest_str, dry_run=True)

        assert result.success is True
        assert result.dry_run is True
        assert not (tmp_path / "out" / "file.txt").exists()

    def test_dry_run_message_contains_arrow(self, tmp_path: Path) -> None:
        src = tmp_path / "x.bin"
        src.write_bytes(b"x")
        result = apply_hardlink(src, str(tmp_path / "out" / "x.bin"), dry_run=True)
        assert "[dry-run]" in result.message


# ---------------------------------------------------------------------------
# apply_hardlink — conflict strategies
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="hardlink inode check differs on Windows")
class TestApplyHardlinkConflict:
    """Test all four conflict-resolution strategies for hardlink."""

    def test_conflict_skip(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("old")

        result = apply_hardlink(src, str(dest), conflict=ConflictStrategy.SKIP)

        assert result.skipped is True
        assert result.success is True
        assert dest.read_text() == "old"

    def test_conflict_overwrite(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("old")

        result = apply_hardlink(src, str(dest), conflict=ConflictStrategy.OVERWRITE)

        assert result.success is True
        assert result.skipped is False
        assert os.stat(src).st_ino == os.stat(dest).st_ino

    def test_conflict_rename_new(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("old")

        result = apply_hardlink(src, str(dest), conflict=ConflictStrategy.RENAME_NEW)

        assert result.success is True
        assert result.destination != dest  # link was placed elsewhere
        assert result.destination.name == "dest_1.txt"
        assert dest.read_text() == "old"  # original untouched

    def test_conflict_rename_existing(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("old")

        result = apply_hardlink(src, str(dest), conflict=ConflictStrategy.RENAME_EXISTING)

        assert result.success is True
        assert result.destination == dest
        renamed = tmp_path / "dest_1.txt"
        assert renamed.read_text() == "old"


# ---------------------------------------------------------------------------
# apply_hardlink — cross-volume failure (EXDEV)
# ---------------------------------------------------------------------------


class TestApplyHardlinkCrossVolume:
    """Test EXDEV and EPERM error handling for hardlink."""

    def test_exdev_returns_failure_with_message(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("content")
        dest_str = str(tmp_path / "out" / "src.txt")

        with patch("os.link", side_effect=OSError(errno.EXDEV, "Cross-device link")):
            result = apply_hardlink(src, dest_str)

        assert result.success is False
        assert "different filesystem" in result.message.lower() or "cross" in result.message.lower()

    def test_eperm_returns_failure_with_message(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("content")
        dest_str = str(tmp_path / "out" / "src.txt")

        with patch("os.link", side_effect=OSError(errno.EPERM, "Operation not permitted")):
            result = apply_hardlink(src, dest_str)

        assert result.success is False
        assert "filesystem" in result.message.lower()

    def test_generic_oserror_returns_failure(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("content")
        dest_str = str(tmp_path / "out" / "src.txt")

        with patch("os.link", side_effect=OSError(errno.EIO, "I/O error")):
            result = apply_hardlink(src, dest_str)

        assert result.success is False
        assert "hardlink" in result.message.lower() or "failed" in result.message.lower()


# ---------------------------------------------------------------------------
# apply_symlink — success path
# ---------------------------------------------------------------------------


class TestApplySymlinkSuccess:
    """Test apply_symlink creates a symlink."""

    def test_creates_symlink(self, tmp_path: Path) -> None:
        src = tmp_path / "original.txt"
        src.write_text("data")
        dest_str = str(tmp_path / "links" / "original.txt")

        result = apply_symlink(src, dest_str)

        assert result.success is True
        assert result.dry_run is False
        link = tmp_path / "links" / "original.txt"
        assert link.is_symlink()
        assert link.resolve() == src.resolve()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        src = tmp_path / "x.txt"
        src.write_text("hi")
        dest_str = str(tmp_path / "deep" / "nested" / "x.txt")

        result = apply_symlink(src, dest_str)

        assert result.success is True
        assert (tmp_path / "deep" / "nested" / "x.txt").is_symlink()

    def test_string_conflict_strategy(self, tmp_path: Path) -> None:
        src = tmp_path / "x.txt"
        src.write_text("hi")
        result = apply_symlink(src, str(tmp_path / "out" / "x.txt"), conflict="skip")
        assert result.success is True


# ---------------------------------------------------------------------------
# apply_symlink — dry-run
# ---------------------------------------------------------------------------


class TestApplySymlinkDryRun:
    """Dry-run must not create any files."""

    def test_dry_run_does_not_create_symlink(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        src.write_text("content")
        dest_str = str(tmp_path / "links" / "file.txt")

        result = apply_symlink(src, dest_str, dry_run=True)

        assert result.success is True
        assert result.dry_run is True
        assert not (tmp_path / "links" / "file.txt").exists()

    def test_dry_run_message_contains_indicator(self, tmp_path: Path) -> None:
        src = tmp_path / "x.txt"
        src.write_text("x")
        result = apply_symlink(src, str(tmp_path / "links" / "x.txt"), dry_run=True)
        assert "dry-run" in result.message.lower() or "would" in result.message.lower()


# ---------------------------------------------------------------------------
# apply_symlink — source-is-symlink warning
# ---------------------------------------------------------------------------


class TestApplySymlinkChainWarning:
    """apply_symlink warns when the source is itself a symlink."""

    def test_warns_on_symlink_source(self, tmp_path: Path) -> None:
        real = tmp_path / "real.txt"
        real.write_text("original")
        link_src = tmp_path / "link_src.txt"
        os.symlink(real, link_src)
        dest_str = str(tmp_path / "out" / "link_src.txt")

        with pytest.warns(UserWarning, match="symlink chain"):
            apply_symlink(link_src, dest_str)

    def test_no_warning_on_regular_source(self, tmp_path: Path) -> None:
        src = tmp_path / "regular.txt"
        src.write_text("data")
        dest_str = str(tmp_path / "out" / "regular.txt")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            apply_symlink(src, dest_str)

        user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
        assert len(user_warnings) == 0


# ---------------------------------------------------------------------------
# apply_symlink — conflict strategies
# ---------------------------------------------------------------------------


class TestApplySymlinkConflict:
    """Test all four conflict-resolution strategies for symlink."""

    def test_conflict_skip(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("old")

        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.SKIP)

        assert result.skipped is True
        assert dest.read_text() == "old"
        assert not dest.is_symlink()

    def test_conflict_overwrite(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("old")

        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.OVERWRITE)

        assert result.success is True
        assert dest.is_symlink()
        assert dest.read_text() == "new"

    def test_conflict_rename_new(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("old")

        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.RENAME_NEW)

        assert result.success is True
        assert result.destination.name == "dest_1.txt"
        assert dest.read_text() == "old"

    def test_conflict_rename_existing(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("old")

        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.RENAME_EXISTING)

        assert result.success is True
        assert result.destination == dest
        assert (tmp_path / "dest_1.txt").read_text() == "old"

    def test_conflict_skip_on_existing_symlink(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        real = tmp_path / "real.txt"
        real.write_text("original")
        dest = tmp_path / "link.txt"
        os.symlink(real, dest)

        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.SKIP)

        assert result.skipped is True
        # Original symlink target unchanged
        assert dest.read_text() == "original"


# ---------------------------------------------------------------------------
# apply_symlink — OSError handling
# ---------------------------------------------------------------------------


class TestApplySymlinkOSError:
    """Test that OSErrors during symlink creation return a failed result."""

    def test_oserror_returns_failure(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("content")
        dest_str = str(tmp_path / "out" / "src.txt")

        with patch("os.symlink", side_effect=OSError(errno.EACCES, "Permission denied")):
            result = apply_symlink(src, dest_str)

        assert result.success is False
        assert "symlink" in result.message.lower() or "failed" in result.message.lower()


# ---------------------------------------------------------------------------
# Integration: apply_hardlink and apply_symlink via ActionType enum
# ---------------------------------------------------------------------------


class TestActionTypeIntegration:
    """Verify ActionType.HARDLINK and SYMLINK exist and are usable in rules."""

    def test_hardlink_action_type_exists(self) -> None:
        from services.copilot.rules.models import ActionType

        assert ActionType("hardlink") is ActionType.HARDLINK

    def test_symlink_action_type_exists(self) -> None:
        from services.copilot.rules.models import ActionType

        assert ActionType("symlink") is ActionType.SYMLINK

    def test_rule_with_hardlink_action_roundtrip(self) -> None:
        from services.copilot.rules.models import (
            ActionType,
            ConditionType,
            Rule,
            RuleAction,
            RuleCondition,
        )

        rule = Rule(
            name="photo-view",
            conditions=[RuleCondition(ConditionType.EXTENSION, ".jpg")],
            action=RuleAction(
                action_type=ActionType.HARDLINK,
                destination="~/Organized/Photos/{name}",
                parameters={"conflict": "rename_new"},
            ),
        )
        d = rule.to_dict()
        assert d["action"]["type"] == "hardlink"
        assert d["action"]["parameters"]["conflict"] == "rename_new"

        restored = Rule.from_dict(d)
        assert restored.action.action_type == ActionType.HARDLINK
        assert restored.action.parameters["conflict"] == "rename_new"

    def test_rule_with_symlink_action_roundtrip(self) -> None:
        from services.copilot.rules.models import (
            ActionType,
            ConditionType,
            Rule,
            RuleAction,
            RuleCondition,
        )

        rule = Rule(
            name="cross-volume-view",
            conditions=[RuleCondition(ConditionType.EXTENSION, ".png")],
            action=RuleAction(
                action_type=ActionType.SYMLINK,
                destination="/mnt/nas/Photos/{name}",
                parameters={"conflict": "skip"},
            ),
        )
        d = rule.to_dict()
        assert d["action"]["type"] == "symlink"

        restored = Rule.from_dict(d)
        assert restored.action.action_type == ActionType.SYMLINK

    def test_preview_shows_hardlink_action_type(self, tmp_path: Path) -> None:
        from services.copilot.rules.models import (
            ActionType,
            ConditionType,
            Rule,
            RuleAction,
            RuleCondition,
            RuleSet,
        )
        from services.copilot.rules.preview import PreviewEngine

        f = tmp_path / "photo.jpg"
        f.write_bytes(b"jpeg")

        rule = Rule(
            name="hl-photos",
            conditions=[RuleCondition(ConditionType.EXTENSION, ".jpg")],
            action=RuleAction(
                action_type=ActionType.HARDLINK,
                destination=str(tmp_path / "view" / "{name}"),
            ),
        )
        rs = RuleSet(rules=[rule])
        engine = PreviewEngine()
        result = engine.preview(rs, tmp_path, recursive=False)

        assert result.match_count == 1
        assert result.matches[0].action_type == "hardlink"

    def test_preview_shows_symlink_action_type(self, tmp_path: Path) -> None:
        from services.copilot.rules.models import (
            ActionType,
            ConditionType,
            Rule,
            RuleAction,
            RuleCondition,
            RuleSet,
        )
        from services.copilot.rules.preview import PreviewEngine

        f = tmp_path / "photo.png"
        f.write_bytes(b"png")

        rule = Rule(
            name="sl-photos",
            conditions=[RuleCondition(ConditionType.EXTENSION, ".png")],
            action=RuleAction(
                action_type=ActionType.SYMLINK,
                destination=str(tmp_path / "view" / "{name}"),
            ),
        )
        rs = RuleSet(rules=[rule])
        engine = PreviewEngine()
        result = engine.preview(rs, tmp_path, recursive=False)

        assert result.match_count == 1
        assert result.matches[0].action_type == "symlink"
