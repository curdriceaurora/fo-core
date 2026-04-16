from pathlib import Path

import pytest

from services.misplacement_detector import MisplacementDetector


@pytest.fixture
def misplacement_dir_tree(tmp_path: Path) -> Path:
    root = tmp_path / "misplacement_test"
    root.mkdir()

    docs_dir = root / "documents"
    docs_dir.mkdir()
    (docs_dir / "doc1.txt").touch()
    (docs_dir / "doc2.txt").touch()
    (docs_dir / "doc3.pdf").touch()
    # Misplaced image in documents
    (docs_dir / "image1.jpg").touch()

    images_dir = root / "images"
    images_dir.mkdir()
    (images_dir / "pic1.jpg").touch()
    (images_dir / "pic2.png").touch()
    (images_dir / "pic3.jpg").touch()

    return root


def test_misplacement_detector_detects_misplaced_file(misplacement_dir_tree: Path) -> None:
    detector = MisplacementDetector(min_mismatch_score=40.0)

    docs_dir = misplacement_dir_tree / "documents"
    misplaced = detector.detect_misplaced(docs_dir)

    # Exactly one file is misplaced: the jpg among document files
    assert [m.file_path.name for m in misplaced] == ["image1.jpg"]
    misplaced_file = misplaced[0]
    assert misplaced_file.current_location == docs_dir
    assert misplaced_file.mismatch_score > 45.0


def test_misplacement_detector_analyze_context(misplacement_dir_tree: Path) -> None:
    detector = MisplacementDetector()
    docs_dir = misplacement_dir_tree / "documents"

    context = detector.analyze_context(docs_dir / "image1.jpg")

    assert context.file_type == ".jpg"
    assert context.directory == docs_dir
    assert len(context.sibling_files) == 3
    assert ".txt" in context.sibling_types
    assert ".pdf" in context.sibling_types
    assert context.parent_category == "images"


def test_misplacement_detector_type_mismatch_score(misplacement_dir_tree: Path) -> None:
    """Type mismatch is flagged via the public detect_misplaced API."""
    detector = MisplacementDetector(min_mismatch_score=40.0)
    docs_dir = misplacement_dir_tree / "documents"

    misplaced = detector.detect_misplaced(docs_dir)
    misplaced_file = next((m for m in misplaced if m.file_path.name == "image1.jpg"), None)

    # Image among documents → file_type_mismatch flagged in public result metadata
    assert misplaced_file is not None
    assert misplaced_file.metadata["file_type_mismatch"] is True
    assert misplaced_file.mismatch_score > 45.0


def test_misplacement_detector_naming_mismatch(misplacement_dir_tree: Path) -> None:
    detector = MisplacementDetector()

    # Build a directory with a strong underscore naming convention
    naming_dir = misplacement_dir_tree / "naming_patterns"
    naming_dir.mkdir()
    (naming_dir / "project_notes.txt").touch()
    (naming_dir / "meeting_minutes.txt").touch()
    (naming_dir / "release_summary.txt").touch()
    # This file uses dashes — breaks the prevailing underscore convention
    (naming_dir / "outlier-name.txt").touch()

    context = detector.analyze_context(naming_dir / "outlier-name.txt")
    naming_score = detector._calculate_naming_mismatch(naming_dir / "outlier-name.txt", context)

    # has_dash=True, siblings_underscore=3 > siblings_dash=0 → mismatch branch fires
    assert naming_score == 60.0


def test_misplacement_detector_rejects_invalid_directory(tmp_path: Path) -> None:
    detector = MisplacementDetector()
    missing_dir = tmp_path / "does-not-exist"

    with pytest.raises(ValueError, match="Invalid directory"):
        detector.detect_misplaced(missing_dir)
