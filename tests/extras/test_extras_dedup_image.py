"""Smoke canary for the dedup-image extra (torch + imagededup)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def _require_dedup_image() -> None:
    # Hard imports — missing package means the extra is broken, not skippable.
    import imagededup  # noqa: F401
    import sklearn  # noqa: F401  verifies fo-core[dedup-text] self-dep resolved
    import torch  # noqa: F401


def test_imagededup_importable() -> None:
    from imagededup.methods import PHash  # noqa: F401


def test_image_deduplicator_importable() -> None:
    from services.deduplication.image_dedup import ImageDeduplicator  # noqa: F401


def test_dedup_text_stack_available() -> None:
    """dedup-image declares fo-core[dedup-text]; verify sklearn came in."""
    from services.deduplication.embedder import DocumentEmbedder  # noqa: F401
