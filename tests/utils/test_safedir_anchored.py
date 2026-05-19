"""Tests for anchored-traversal SafeDir helpers (#286).

Exercises ``SafeDir.open_anchored_reader(relative_path)`` and the
module-level ``read_file_via_safedir_anchored`` wrapper. Both walk a
relative path one component at a time via ``open_subdir`` so an
intermediate-component symlink is refused with ``SymlinkRejected``
rather than dereferenced — closes the nested-ancestor TOCTOU window
documented in #286, separate from the final-component protection that
``SafeDir.open_for_reader`` already provides.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from utils.readers import read_file_via_safedir_anchored
from utils.safedir import SafeDir, SymlinkRejected

# Mirrors tests/utils/test_readers_safedir.py: the suite exercises the
# anchored-traversal primitive end-to-end through real filesystem syscalls,
# so it counts as integration coverage for ``src/utils/safedir.py`` and
# ``src/utils/readers/__init__.py``. Without ``integration``, the per-module
# floor check in pr-integration.yml drops below the baseline whenever this
# file's source coverage isn't seen in the integration run.
pytestmark = [
    pytest.mark.ci,
    pytest.mark.unit,
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only"),
]

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="SafeDir requires POSIX dir_fd / O_NOFOLLOW",
)


# ---------------------------------------------------------------------------
# SafeDir.open_anchored_reader
# ---------------------------------------------------------------------------


@posix_only
class TestOpenAnchoredReader:
    """Direct tests for the SafeDir.open_anchored_reader primitive."""

    def test_walks_simple_relative_path(self, tmp_path: Path) -> None:
        """Single-component relative_path opens the leaf via open_for_reader."""
        (tmp_path / "doc.txt").write_text("hello")
        with SafeDir.open_root(tmp_path) as root:
            fd = root.open_anchored_reader(Path("doc.txt"))
            try:
                with os.fdopen(fd, "rb", closefd=True) as f:
                    assert f.read() == b"hello"
            except BaseException:
                os.close(fd)
                raise

    def test_walks_nested_relative_path(self, tmp_path: Path) -> None:
        """Multi-component relative_path walks each intermediate via open_subdir."""
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        (tmp_path / "a" / "b" / "c" / "doc.txt").write_text("nested")
        with SafeDir.open_root(tmp_path) as root:
            fd = root.open_anchored_reader(Path("a/b/c/doc.txt"))
            try:
                with os.fdopen(fd, "rb", closefd=True) as f:
                    assert f.read() == b"nested"
            except BaseException:
                os.close(fd)
                raise

    def test_intermediate_symlink_refused(self, tmp_path: Path) -> None:
        """Ancestor swapped to symlink between enumeration and read is refused.

        This is the core anchored-traversal protection. The walk opens
        ``a`` first, sees it's a symlink, and raises SymlinkRejected
        before any subsequent component is opened.
        """
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("attacker content")

        inside = tmp_path / "inside"
        inside.mkdir()
        # Originally a directory; gets swapped to a symlink to `outside`
        (inside / "a").symlink_to(outside)
        # The "victim" leaf the caller intended to read
        (inside / "doc.txt").write_text("legitimate")

        with SafeDir.open_root(inside) as root:
            # Walking 'a/secret.txt' should refuse at 'a' (the symlink),
            # not dereference and open 'outside/secret.txt'.
            with pytest.raises(SymlinkRejected):
                root.open_anchored_reader(Path("a/secret.txt"))

    def test_final_component_symlink_refused(self, tmp_path: Path) -> None:
        """Leaf symlink is refused too (the existing final-component guard)."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("attacker content")
        inside = tmp_path / "inside"
        inside.mkdir()
        (inside / "doc.txt").symlink_to(outside / "secret.txt")

        with SafeDir.open_root(inside) as root:
            with pytest.raises(SymlinkRejected):
                root.open_anchored_reader(Path("doc.txt"))

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        """Absolute relative_path is a programmer error — reject early."""
        with SafeDir.open_root(tmp_path) as root:
            with pytest.raises(ValueError, match="relative"):
                root.open_anchored_reader(Path("/etc/passwd"))

    def test_parent_traversal_rejected(self, tmp_path: Path) -> None:
        """``..`` components would escape — must be refused before any open."""
        (tmp_path / "child").mkdir()
        (tmp_path / "doc.txt").write_text("data")
        with SafeDir.open_root(tmp_path / "child") as root:
            with pytest.raises(ValueError, match=r"\.\."):
                root.open_anchored_reader(Path("../doc.txt"))

    def test_empty_path_rejected(self, tmp_path: Path) -> None:
        """Empty relative_path doesn't identify any file."""
        with SafeDir.open_root(tmp_path) as root:
            with pytest.raises(ValueError):
                root.open_anchored_reader(Path(""))


# ---------------------------------------------------------------------------
# read_file_via_safedir_anchored
# ---------------------------------------------------------------------------


@posix_only
class TestReadFileViaSafedirAnchored:
    """Tests for the top-level anchored reader wrapper."""

    def test_reads_text_in_nested_dir(self, tmp_path: Path) -> None:
        (tmp_path / "docs" / "sub").mkdir(parents=True)
        leaf = tmp_path / "docs" / "sub" / "note.txt"
        leaf.write_text("hello anchored")

        out = read_file_via_safedir_anchored(leaf, trusted_root=tmp_path)
        assert out == "hello anchored"

    def test_intermediate_symlink_refused_via_helper(self, tmp_path: Path) -> None:
        """Same protection as the primitive, exercised through the wrapper.

        Ensures the wrapper actually uses the anchored walk (not a
        parent-rooted open of file_path.parent that would happily
        dereference the ancestor symlink).
        """
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("attacker content")

        inside = tmp_path / "inside"
        inside.mkdir()
        (inside / "evil").symlink_to(outside)

        # The leaf path the caller thinks they're reading
        victim = inside / "evil" / "secret.txt"
        with pytest.raises(SymlinkRejected):
            read_file_via_safedir_anchored(victim, trusted_root=inside)

    def test_file_outside_trusted_root_rejected(self, tmp_path: Path) -> None:
        """file_path outside trusted_root is a security violation — raise."""
        trusted = tmp_path / "trusted"
        trusted.mkdir()
        (trusted / "ok.txt").write_text("inside")
        elsewhere = tmp_path / "elsewhere.txt"
        elsewhere.write_text("outside")

        with pytest.raises(ValueError):
            read_file_via_safedir_anchored(elsewhere, trusted_root=trusted)

    def test_unsupported_extension_returns_none(self, tmp_path: Path) -> None:
        """Same contract as read_file_via_safedir: None when no reader matches."""
        leaf = tmp_path / "data.unknownext"
        leaf.write_text("payload")

        out = read_file_via_safedir_anchored(leaf, trusted_root=tmp_path)
        assert out is None


# ---------------------------------------------------------------------------
# Backward-compat: text_processor.process_file with optional scan_root
# ---------------------------------------------------------------------------


@posix_only
class TestTextProcessorScanRoot:
    """``TextProcessor.process_file`` accepts an optional scan_root.

    - Without scan_root: behavior is unchanged (parent-rooted SafeDir open).
    - With scan_root: anchored traversal kicks in for the LLM-ingestion path.
    """

    def _mock_text_model(self) -> object:
        """Build a MagicMock text model that satisfies TextProcessor's contract."""
        from unittest.mock import MagicMock

        from models.base import ModelType

        model = MagicMock()
        model.config.model_type = ModelType.TEXT
        model.is_initialized = True
        model.generate.return_value = "Mocked AI Response"
        return model

    def test_signature_accepts_scan_root(self, tmp_path: Path) -> None:
        """Smoke test: kwarg appears in the function signature."""
        from services.text_processor import TextProcessor

        processor = TextProcessor(text_model=self._mock_text_model())
        sig_params = processor.process_file.__code__.co_varnames
        assert "scan_root" in sig_params

    def test_scan_root_exercises_anchored_path(self, tmp_path: Path) -> None:
        """Calling process_file with scan_root walks intermediates anchored.

        Verifies an ancestor symlink under scan_root causes the read to
        be refused — which the parent-rooted path would silently
        dereference. Covers the new branch in process_file end-to-end.
        """
        from services.text_processor import TextProcessor

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("attacker content")

        inside = tmp_path / "inside"
        inside.mkdir()
        (inside / "evil").symlink_to(outside)

        # Caller "discovered" inside/evil/secret.txt during a walk and now
        # asks TextProcessor to read it under the anchored root `inside`.
        victim = inside / "evil" / "secret.txt"

        processor = TextProcessor(text_model=self._mock_text_model())
        result = processor.process_file(
            victim,
            generate_description=False,
            generate_folder=False,
            generate_filename=False,
            scan_root=inside,
        )
        # The anchored path refuses the read (via SymlinkRejected on the
        # 'evil' intermediate). The wrapper catches it and returns a
        # ProcessedFile with the "Refused to read symlink" error.
        assert result.error is not None
        assert "symlink" in result.error.lower()
        # Crucially, no attacker content reached the model.
        assert result.original_content is None or "attacker" not in (result.original_content or "")

    def test_scan_root_none_uses_parent_rooted_path(self, tmp_path: Path) -> None:
        """When scan_root is None (default), legacy parent-rooted SafeDir open.

        Same behaviour as PR3a–PR3i — covers the else branch.
        """
        from services.text_processor import TextProcessor

        leaf = tmp_path / "doc.txt"
        leaf.write_text("legitimate content")

        processor = TextProcessor(text_model=self._mock_text_model())
        result = processor.process_file(
            leaf,
            generate_description=False,
            generate_folder=False,
            generate_filename=False,
            # scan_root omitted — default None
        )
        # No error; the parent-rooted SafeDir open succeeded.
        assert result.error is None
