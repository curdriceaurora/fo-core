"""Smoke canary for the [dedup] optional extra (imagededup, scikit-learn)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.smoke
def test_image_deduplicator_finds_identical_images(tmp_path: Path) -> None:
    pytest.importorskip("imagededup")
    from PIL import Image  # Pillow is a dep of imagededup

    from file_organizer.services.deduplication.image_dedup import ImageDeduplicator

    # Use PNG (lossless) so both saves produce bit-identical files and the
    # perceptual hash distance is guaranteed to be 0.  threshold=0 requires
    # exact hash match, making the assertion deterministic.
    img = Image.new("RGB", (64, 64), color=(128, 64, 32))
    img.save(tmp_path / "img1.png")
    img.save(tmp_path / "img2.png")

    deduplicator = ImageDeduplicator(threshold=0)
    result = deduplicator.find_duplicates(tmp_path)

    assert isinstance(result, dict)
    assert len(result) >= 1  # identical PNGs must produce a duplicate group


@pytest.mark.smoke
def test_sklearn_importable() -> None:
    pytest.importorskip("sklearn")
