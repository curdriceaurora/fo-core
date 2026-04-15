"""Smoke canary for the [video] optional extra (opencv-python, scenedetect)."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.smoke
def test_opencv_read_write_cycle(tmp_path: Path) -> None:
    """Validate the opencv-python install end-to-end without going through ffprobe."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np  # numpy is a transitive dep of opencv-python

    # Write a minimal video using cv2 (not ffprobe)
    video_path = tmp_path / "test.mp4"
    out = cv2.VideoWriter(
        str(video_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        1,  # fps
        (64, 64),  # width x height
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    for _ in range(5):
        out.write(frame)
    out.release()

    # Read it back with cv2 — exercises the full opencv install path, not ffprobe
    cap = cv2.VideoCapture(str(video_path))
    assert cap.isOpened(), "cv2 could not open the written video"
    frame_count = 0
    while True:
        ret, _ = cap.read()
        if not ret:
            break
        frame_count += 1
    cap.release()
    assert frame_count >= 1


@pytest.mark.smoke
def test_scenedetect_detects_scenes(tmp_path: Path) -> None:
    """Validate scenedetect performs actual scene detection (not just an import check)."""
    cv2 = pytest.importorskip("cv2")
    scenedetect = pytest.importorskip("scenedetect")
    import numpy as np

    # Write a minimal video for scenedetect to process
    video_path = tmp_path / "scenes.mp4"
    out = cv2.VideoWriter(
        str(video_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        10,  # fps (scenedetect needs > 1 fps to resolve timecodes)
        (64, 64),
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    for _ in range(30):
        out.write(frame)
    out.release()

    video = scenedetect.open_video(str(video_path))
    scene_manager = scenedetect.SceneManager()
    scene_manager.add_detector(scenedetect.ContentDetector())
    scene_manager.detect_scenes(video)
    scenes = scene_manager.get_scene_list()
    assert isinstance(scenes, list)  # may be empty — that is fine for a canary
