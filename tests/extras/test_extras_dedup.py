"""Smoke canary for the [dedup] optional extra (imagededup, scikit-learn)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.smoke
def test_image_deduplicator_finds_identical_images(tmp_path: Path) -> None:
    pytest.importorskip("imagededup")
    from PIL import Image  # Pillow is a dep of imagededup

    from file_organizer.services.deduplication.image_dedup import ImageDeduplicator

    # Create two identical small images — deduplicator should flag them
    img = Image.new("RGB", (64, 64), color=(128, 64, 32))
    img.save(tmp_path / "img1.jpg")
    img.save(tmp_path / "img2.jpg")

    deduplicator = ImageDeduplicator()
    result = deduplicator.find_duplicates(tmp_path)

    assert result is not None
    assert isinstance(result, dict)
    assert len(result) >= 1  # at least one duplicate group found from identical images


@pytest.mark.smoke
def test_sklearn_importable() -> None:
    pytest.importorskip("sklearn")
