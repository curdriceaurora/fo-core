"""Integration tests for hardlink and symlink actions (services.copilot.rules.actions).

These tests exercise the real filesystem functions directly — no CLI layer,
no mocks — to satisfy the per-module integration coverage gate for
src/services/copilot/rules/actions.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.copilot.rules.actions import (
    ConflictStrategy,
    apply_hardlink,
    apply_symlink,
)

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_src(tmp_path: Path, name: str = "source.txt", content: str = "data") -> Path:
    src = tmp_path / name
    src.write_text(content)
    return src


# ---------------------------------------------------------------------------
# apply_hardlink — core paths
# ---------------------------------------------------------------------------


class TestApplyHardlinkBasic:
    def test_creates_hardlink(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        result = apply_hardlink(src, str(dest_dir / "link.txt"))
        assert result.success
        assert result.destination.exists()
        assert result.destination.stat().st_ino == src.stat().st_ino

    def test_dest_is_directory_appends_filename(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path, "original.txt")
        dest_dir = tmp_path / "output"
        dest_dir.mkdir()
        result = apply_hardlink(src, str(dest_dir))
        assert result.success
        assert result.destination.name == "original.txt"
        assert result.destination.stat().st_ino == src.stat().st_ino

    def test_dry_run_does_not_create_file(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "dest" / "link.txt"
        result = apply_hardlink(src, str(dest), dry_run=True)
        assert result.success
        assert result.dry_run
        assert not dest.exists()

    def test_template_variables_in_dest(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path, "report.pdf")
        dest_str = str(tmp_path / "out_{stem}_copy.{ext}")
        result = apply_hardlink(src, dest_str)
        assert result.success
        assert result.destination.name == "out_report_copy.pdf"


class TestApplyHardlinkConflict:
    def test_conflict_skip(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        dest.write_text("existing")
        result = apply_hardlink(src, str(dest), conflict=ConflictStrategy.SKIP)
        assert result.success
        assert result.skipped
        assert dest.read_text() == "existing"

    def test_conflict_overwrite(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path, content="new")
        dest = tmp_path / "link.txt"
        dest.write_text("old")
        result = apply_hardlink(src, str(dest), conflict=ConflictStrategy.OVERWRITE)
        assert result.success
        assert not result.skipped
        assert dest.stat().st_ino == src.stat().st_ino

    def test_conflict_rename_new(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        dest.write_text("existing")
        result = apply_hardlink(src, str(dest), conflict=ConflictStrategy.RENAME_NEW)
        assert result.success
        assert result.destination != dest
        assert result.destination.name == "link_1.txt"

    def test_conflict_rename_existing(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path, content="new")
        dest = tmp_path / "link.txt"
        dest.write_text("old")
        result = apply_hardlink(src, str(dest), conflict=ConflictStrategy.RENAME_EXISTING)
        assert result.success
        assert result.destination == dest
        assert dest.stat().st_ino == src.stat().st_ino

    def test_conflict_as_string(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        result = apply_hardlink(src, str(dest), conflict="rename_new")
        assert result.success

    def test_rename_new_multiple_counter(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        dest.write_text("existing")
        (tmp_path / "link_1.txt").write_text("also existing")
        result = apply_hardlink(src, str(dest), conflict=ConflictStrategy.RENAME_NEW)
        assert result.success
        assert result.destination.name == "link_2.txt"


# ---------------------------------------------------------------------------
# apply_symlink — core paths
# ---------------------------------------------------------------------------


class TestApplySymlinkBasic:
    def test_creates_symlink(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "out" / "link.txt"
        result = apply_symlink(src, str(dest))
        assert result.success
        assert dest.is_symlink()
        assert dest.resolve() == src.resolve()

    def test_symlink_is_absolute(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        result = apply_symlink(src, str(dest))
        assert result.success
        assert os.readlink(dest) == str(src.resolve())

    def test_dry_run_does_not_create_symlink(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        result = apply_symlink(src, str(dest), dry_run=True)
        assert result.success
        assert result.dry_run
        assert not dest.exists()

    def test_dest_is_directory_appends_filename(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path, "photo.jpg")
        dest_dir = tmp_path / "gallery"
        dest_dir.mkdir()
        result = apply_symlink(src, str(dest_dir))
        assert result.success
        assert result.destination.name == "photo.jpg"

    def test_symlink_src_is_itself_symlink_warns(self, tmp_path: Path) -> None:
        real = _make_src(tmp_path, "real.txt")
        sym = tmp_path / "sym.txt"
        os.symlink(real, sym)
        dest = tmp_path / "link.txt"
        with pytest.warns(UserWarning, match="symlink chain"):
            result = apply_symlink(sym, str(dest))
        assert result.success


class TestApplySymlinkConflict:
    def test_conflict_skip(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        dest.write_text("existing")
        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.SKIP)
        assert result.success
        assert result.skipped

    def test_conflict_overwrite(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        dest.write_text("existing")
        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.OVERWRITE)
        assert result.success
        assert dest.is_symlink()

    def test_conflict_rename_new(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        dest.write_text("existing")
        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.RENAME_NEW)
        assert result.success
        assert result.destination.name == "link_1.txt"

    def test_conflict_rename_existing(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        dest.write_text("existing")
        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.RENAME_EXISTING)
        assert result.success
        assert dest.is_symlink()

    def test_conflict_broken_symlink_treated_as_occupied(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        broken_target = tmp_path / "gone.txt"
        os.symlink(broken_target, dest)
        assert not dest.exists()
        assert dest.is_symlink()
        result = apply_symlink(src, str(dest), conflict=ConflictStrategy.SKIP)
        assert result.skipped

    def test_conflict_as_string(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        dest = tmp_path / "link.txt"
        result = apply_symlink(src, str(dest), conflict="overwrite")
        assert result.success
