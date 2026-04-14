from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from file_organizer.services.deduplication.viewer import (
    ComparisonViewer,
)


@pytest.fixture
def duplicate_images(tmp_path: Path) -> list[Path]:
    img1_path = tmp_path / "img1.png"
    img2_path = tmp_path / "img2.png"

    img1 = Image.new("RGB", (100, 100), color="red")
    img1.save(img1_path)

    img2 = Image.new("RGB", (50, 50), color="blue")
    img2.save(img2_path)

    return [img1_path, img2_path]


@patch("file_organizer.services.deduplication.viewer.Prompt.ask")
def test_comparison_viewer_interactive_select_all(
    mock_ask: MagicMock, duplicate_images: list[Path]
) -> None:
    mock_ask.return_value = "all"
    viewer = ComparisonViewer()
    selected = viewer.interactive_select(duplicate_images)
    assert selected == duplicate_images
    mock_ask.assert_called_once_with("Your selection", default="all")


@patch("file_organizer.services.deduplication.viewer.Prompt.ask")
def test_comparison_viewer_interactive_select_specific(
    mock_ask: MagicMock, duplicate_images: list[Path]
) -> None:
    mock_ask.return_value = "1"
    viewer = ComparisonViewer()
    selected = viewer.interactive_select(duplicate_images)
    assert len(selected) == 1
    assert selected[0] == duplicate_images[0]
    mock_ask.assert_called_once_with("Your selection", default="all")


@patch("file_organizer.services.deduplication.viewer.Prompt.ask")
def test_comparison_viewer_interactive_select_ignores_invalid_entries(
    mock_ask: MagicMock, duplicate_images: list[Path]
) -> None:
    mock_ask.return_value = "1,invalid,9"
    viewer = ComparisonViewer()
    selected = viewer.interactive_select(duplicate_images)
    assert selected == [duplicate_images[0]]


@patch("file_organizer.services.deduplication.viewer.Prompt.ask")
def test_comparison_viewer_interactive_select_none(
    mock_ask: MagicMock, duplicate_images: list[Path]
) -> None:
    mock_ask.return_value = "none"
    viewer = ComparisonViewer()
    selected = viewer.interactive_select(duplicate_images)
    assert selected == []


@patch("file_organizer.services.deduplication.viewer.Prompt.ask")
def test_comparison_viewer_show_comparison_auto(
    mock_ask: MagicMock, duplicate_images: list[Path]
) -> None:
    mock_ask.return_value = "a"  # auto-select best
    viewer = ComparisonViewer()
    review = viewer.show_comparison(duplicate_images)

    # Auto-select best should pick img1 (higher max resolution)
    assert review.files_to_keep == [duplicate_images[0]]
    assert review.files_to_delete == [duplicate_images[1]]
    assert not review.skipped
    mock_ask.assert_called_once_with("\nYour choice", default="a")


@patch("file_organizer.services.deduplication.viewer.Prompt.ask")
def test_comparison_viewer_show_comparison_skip(
    mock_ask: MagicMock, duplicate_images: list[Path]
) -> None:
    mock_ask.return_value = "s"  # skip
    viewer = ComparisonViewer()
    review = viewer.show_comparison(duplicate_images)

    assert review.files_to_keep == []
    assert review.files_to_delete == []
    assert review.skipped
    mock_ask.assert_called_once_with("\nYour choice", default="a")


def test_comparison_viewer_auto_select_best(duplicate_images: list[Path]) -> None:
    viewer = ComparisonViewer()
    review = viewer._auto_select_best(duplicate_images)
    # img1 is 100x100, img2 is 50x50. img1 should win.
    assert review.files_to_keep == [duplicate_images[0]]
    assert review.files_to_delete == [duplicate_images[1]]
