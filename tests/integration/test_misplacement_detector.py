import pytest

from file_organizer.services.misplacement_detector import MisplacementDetector


@pytest.fixture
def misplacement_dir_tree(tmp_path):
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


def test_misplacement_detector_detects_misplaced_file(misplacement_dir_tree):
    detector = MisplacementDetector(min_mismatch_score=40.0)

    # We test the docs directory
    docs_dir = misplacement_dir_tree / "documents"
    misplaced = detector.detect_misplaced(docs_dir)

    # We expect `image1.jpg` to be flagged as misplaced
    assert len(misplaced) >= 1
    misplaced_file = next((m for m in misplaced if m.file_path.name == "image1.jpg"), None)

    assert misplaced_file is not None
    assert misplaced_file.current_location == docs_dir
    assert misplaced_file.mismatch_score >= 40.0


def test_misplacement_detector_analyze_context(misplacement_dir_tree):
    detector = MisplacementDetector()
    docs_dir = misplacement_dir_tree / "documents"

    context = detector.analyze_context(docs_dir / "image1.jpg")

    assert context.file_type == ".jpg"
    assert context.directory == docs_dir
    assert len(context.sibling_files) == 3
    assert ".txt" in context.sibling_types
    assert ".pdf" in context.sibling_types
    assert context.parent_category == "images"


def test_misplacement_detector_type_mismatch_score(misplacement_dir_tree):
    detector = MisplacementDetector()
    docs_dir = misplacement_dir_tree / "documents"

    context = detector.analyze_context(docs_dir / "image1.jpg")
    type_score = detector._calculate_type_mismatch(context)

    # High mismatch since it's an image among docs
    assert type_score == 80.0


def test_misplacement_detector_naming_mismatch(misplacement_dir_tree):
    detector = MisplacementDetector()
    docs_dir = misplacement_dir_tree / "documents"

    (docs_dir / "outlier-name_weird").touch()
    context = detector.analyze_context(docs_dir / "outlier-name_weird")
    naming_score = detector._calculate_naming_mismatch(docs_dir / "outlier-name_weird", context)

    assert naming_score > 0
