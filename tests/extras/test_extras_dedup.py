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
    # perceptual hash distance is guaranteed to be 0.
    # Use cluster_by_similarity (direct Hamming distance, <= comparison) rather
    # than find_duplicates (delegates to imagededup whose threshold semantics
    # vary by version) to keep the assertion deterministic.
    img = Image.new("RGB", (64, 64), color=(128, 64, 32))
    img.save(tmp_path / "img1.png")
    img.save(tmp_path / "img2.png")

    deduplicator = ImageDeduplicator(threshold=0)
    clusters = deduplicator.cluster_by_similarity(
        [tmp_path / "img1.png", tmp_path / "img2.png"]
    )

    assert len(clusters) == 1  # identical PNGs must form exactly one cluster
    assert len(clusters[0]) == 2  # both images in the cluster


@pytest.mark.smoke
def test_sklearn_importable() -> None:
    pytest.importorskip("sklearn")
