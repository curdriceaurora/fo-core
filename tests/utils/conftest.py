"""NLTK-specific fixtures for text processing tests.

Ensures test hermeticity by providing comprehensive NLTK mocks that work
in isolated environments without pre-installed NLTK corpus data.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_nltk_globally(
    mock_nltk_tokenizer: MagicMock,
    mock_nltk_stopwords: MagicMock,
    mock_nltk_lemmatizer: MagicMock,
    mock_nltk_freqdist: MagicMock,
) -> None:
    """Automatically mock all NLTK components for text processing tests.

    This fixture patches NLTK imports at module level to ensure tests work
    in clean containers without host NLTK corpus data.
    """
    # NLTK was removed from utils.text_processing (replaced by snowballstemmer).
    # These patches are kept as no-ops to preserve fixture signatures used by
    # other test files that still declare these fixtures.
    yield
