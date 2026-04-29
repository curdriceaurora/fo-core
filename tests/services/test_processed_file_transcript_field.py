"""Tests that ProcessedFile carries an optional transcript field.

Step 2B (organize-audio-transcription) threads transcripts from the audio
dispatcher through to the text categorization path. This requires
ProcessedFile to carry the transcript alongside metadata so existing
consumers see the same shape and new consumers can opt in.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.unit
@pytest.mark.ci
def test_processed_file_default_transcript_is_none(tmp_path: Path) -> None:
    """Default ProcessedFile construction leaves transcript as None.

    Existing call sites must not break — every audio/text/video file built
    today carries no transcript, so the field is optional and defaults to
    None. This pins the backwards-compat contract.
    """
    from services.text_processor import ProcessedFile

    pf = ProcessedFile(
        file_path=tmp_path / "x.mp3",
        description="A short audio clip",
        folder_name="Audio",
        filename="x.mp3",
    )
    assert pf.transcript is None


@pytest.mark.unit
@pytest.mark.ci
def test_processed_file_accepts_transcript(tmp_path: Path) -> None:
    """Audio dispatcher attaches transcript via the new field.

    When --transcribe-audio is set and the [media] extra is installed, the
    dispatcher transcribes the file and attaches the text here. The
    organizer's text categorization path then reads it for content-aware
    folder selection.
    """
    from services.text_processor import ProcessedFile

    pf = ProcessedFile(
        file_path=tmp_path / "podcast.mp3",
        description="News podcast",
        folder_name="News",
        filename="podcast.mp3",
        transcript="Today's news roundup covers the markets and...",
    )
    assert pf.transcript == "Today's news roundup covers the markets and..."
