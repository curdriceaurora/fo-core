"""Tests for ``TextProcessor.process_file(..., scan_root=...)`` (#286).

Separate from ``tests/utils/test_safedir_anchored.py`` because the tests
here import ``services.text_processor.TextProcessor``, which transitively
pulls in the ``models`` package singletons. Under ``-m ci`` (Test PR
suite, xdist ``--dist=loadgroup``), those imports interact with the
audio-model singleton state in a way that surfaces the pre-existing
flake tracked in #291. By keeping these tests out of the ``ci`` mark
set, the Test PR suite stays green; integration coverage is preserved
via the ``integration`` mark, which is what the PR per-module coverage
floor uses.

If/when #291 is fixed (audio model singleton gets a proper teardown
fixture), add the ``ci`` mark here too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# NOTE on marks: ``ci`` is deliberately omitted — see module docstring.
pytestmark = [
    pytest.mark.unit,
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="SafeDir is POSIX-only"),
]


def _mock_text_model() -> object:
    """Build a MagicMock text model that satisfies TextProcessor's contract."""
    from unittest.mock import MagicMock

    from models.base import ModelType

    model = MagicMock()
    model.config.model_type = ModelType.TEXT
    model.is_initialized = True
    model.generate.return_value = "Mocked AI Response"
    return model


class TestTextProcessorScanRoot:
    """``TextProcessor.process_file`` accepts an optional scan_root.

    - Without scan_root: behavior is unchanged (parent-rooted SafeDir open).
    - With scan_root: anchored traversal kicks in for the LLM-ingestion path.
    """

    def test_signature_accepts_scan_root(self, tmp_path: Path) -> None:
        """Smoke test: kwarg appears in the function signature."""
        from services.text_processor import TextProcessor

        processor = TextProcessor(text_model=_mock_text_model())
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

        processor = TextProcessor(text_model=_mock_text_model())
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

        processor = TextProcessor(text_model=_mock_text_model())
        result = processor.process_file(
            leaf,
            generate_description=False,
            generate_folder=False,
            generate_filename=False,
            # scan_root omitted — default None
        )
        # No error; the parent-rooted SafeDir open succeeded.
        assert result.error is None
        # Verify the file content was actually read — guards against a
        # regression where the parent-rooted branch silently returns
        # empty/None content without surfacing an error.
        assert result.original_content is not None
        assert "legitimate content" in result.original_content
