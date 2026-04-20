import pytest
from unittest.mock import MagicMock, patch

from utils.text_processing import (
    clean_text,
    ensure_nltk_data,
    extract_keywords,
    get_unwanted_words,
    sanitize_filename,
    truncate_text,
)

# Golden outputs captured under NLTK implementation — acknowledgement artifact
# showing the behavioral difference vs Snowball replacement.
_OLD_CLEAN_TEXT = "study_running_analysis"
_OLD_EXTRACT_KEYWORDS = ['quick', 'brown', 'jumps', 'lazy']
_OLD_STOPWORDS: set[str] = set(['a', 'about', 'above', 'after', 'again', 'against', 'ain', 'all', 'am', 'an', 'and', 'any', 'are', 'aren', "aren't", 'as', 'at', 'based', 'be', 'because', 'been', 'before', 'being', 'below', 'between', 'bmp', 'both', 'but', 'by', 'can', 'category', 'concepts', 'couldn', "couldn't", 'covers', 'csv', 'd', 'demonstrates', 'depicts', 'describes', 'details', 'did', 'didn', "didn't", 'discusses', 'display', 'do', 'document', 'docx', 'does', 'doesn', "doesn't", 'doing', 'don', "don't", 'down', 'during', 'each', 'features', 'few', 'file', 'filename', 'for', 'from', 'further', 'generated', 'gif', 'had', 'hadn', "hadn't", 'has', 'hasn', "hasn't", 'have', 'haven', "haven't", 'having', 'he', "he'd", "he'll", "he's", 'her', 'here', 'hers', 'herself', 'him', 'himself', 'his', 'how', 'i', "i'd", "i'll", "i'm", "i've", 'ideas', 'if', 'illustrates', 'image', 'in', 'includes', 'information', 'into', 'is', 'isn', "isn't", 'it', "it'd", "it'll", "it's", 'its', 'itself', 'jpeg', 'jpg', 'just', 'key', 'll', 'm', 'ma', 'main', 'md', 'me', 'mightn', "mightn't", 'more', 'most', 'mustn', "mustn't", 'my', 'myself', 'needn', "needn't", 'new', 'no', 'nor', 'not', 'note', 'notes', 'now', 'o', 'of', 'off', 'on', 'once', 'only', 'or', 'other', 'our', 'ours', 'ourselves', 'out', 'output', 'over', 'own', 'pdf', 'photo', 'picture', 'png', 'pptx', 'presents', 'provides', 're', 's', 'same', 'shan', "shan't", 'she', "she'd", "she'll", "she's", 'should', "should've", 'shouldn', "shouldn't", 'show', 'shows', 'so', 'some', 'such', 'summary', 't', 'text', 'than', 'that', "that'll", 'the', 'their', 'theirs', 'them', 'themselves', 'then', 'there', 'these', 'they', "they'd", "they'll", "they're", "they've", 'this', 'those', 'through', 'to', 'too', 'txt', 'under', 'unknown', 'until', 'untitled', 'up', 've', 'very', 'was', 'wasn', "wasn't", 'we', "we'd", "we'll", "we're", "we've", 'were', 'weren', "weren't", 'what', 'when', 'where', 'which', 'while', 'who', 'whom', 'why', 'will', 'with', 'won', "won't", 'wouldn', "wouldn't", 'xlsx', 'y', 'you', "you'd", "you'll", "you're", "you've", 'your', 'yours', 'yourself', 'yourselves'])  # noqa: E501

# NEW_* = expected Snowball output. Filled in Task 4 Step 8 after Snowball is implemented.
_NEW_CLEAN_TEXT = "FILL_AFTER_TASK_4"
_NEW_EXTRACT_KEYWORDS: list[str] = []
_NEW_STOPWORDS: set[str] = set()  # filled in Task 4 Step 8


@pytest.mark.xfail(strict=False, reason="Snowball values filled in Task 4")
def test_clean_text_golden_snowball() -> None:
    assert clean_text("Studies in running and analysis") == _NEW_CLEAN_TEXT


@pytest.mark.xfail(strict=False, reason="Snowball values filled in Task 4")
def test_extract_keywords_golden_snowball() -> None:
    assert extract_keywords("The quick brown fox jumps over the lazy dog") == _NEW_EXTRACT_KEYWORDS


@pytest.mark.xfail(strict=False, reason="Snowball values filled in Task 4")
def test_get_unwanted_words_golden_snowball() -> None:
    assert get_unwanted_words() == _NEW_STOPWORDS


class TestTextProcessing:
    @patch("utils.text_processing.NLTK_AVAILABLE", False)
    def test_ensure_nltk_data_unavailable(self):
        # Should return early without error
        ensure_nltk_data()

    @patch("utils.text_processing._nltk_ready", False)
    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    @patch("utils.text_processing.nltk.download")
    @patch("utils.text_processing.stopwords.words")
    @patch("utils.text_processing.word_tokenize")
    @patch("nltk.corpus.wordnet.synsets")
    def test_ensure_nltk_data_available_success(
        self, mock_synsets, mock_tokenize, mock_words, mock_download
    ):
        # No LookUpErrors
        ensure_nltk_data()
        mock_download.assert_not_called()

    @patch("utils.text_processing._nltk_ready", False)
    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    @patch("utils.text_processing.nltk.download")
    def test_ensure_nltk_data_lookup_error_download(self, mock_download):
        from utils.text_processing import stopwords

        with patch.object(stopwords, "words", side_effect=[LookupError("not found"), True]):
            ensure_nltk_data()
            mock_download.assert_called()

    @patch("utils.text_processing._nltk_ready", False)
    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    @patch(
        "utils.text_processing.nltk.download",
        side_effect=RuntimeError("Network error"),
    )
    def test_ensure_nltk_data_download_error(self, mock_download):
        from utils.text_processing import stopwords

        with patch.object(stopwords, "words", side_effect=LookupError("not found")):
            # Should catch exception and not raise
            ensure_nltk_data()

    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    def test_ensure_nltk_data_general_error(self):
        from utils.text_processing import stopwords

        with patch.object(stopwords, "words", side_effect=RuntimeError("Some other error")):
            # Should catch exception and not raise
            ensure_nltk_data()

    @patch("utils.text_processing.NLTK_AVAILABLE", False)
    def test_get_unwanted_words_no_nltk(self):
        words = get_unwanted_words()
        assert "the" in words
        assert "custom_word" not in words

    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    def test_get_unwanted_words_with_nltk(self):
        from utils.text_processing import stopwords

        with patch.object(stopwords, "words", return_value=["teststopword"]):
            words = get_unwanted_words()
            assert "teststopword" in words

    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    def test_get_unwanted_words_with_nltk_lookup_error(self):
        from utils.text_processing import stopwords

        with patch.object(stopwords, "words", side_effect=LookupError("Not found")):
            words = get_unwanted_words()
            assert "the" in words

    def test_clean_text_empty(self):
        assert clean_text("") == ""
        assert clean_text(None) == ""

    @patch("utils.text_processing.NLTK_AVAILABLE", False)
    def test_clean_text_basic(self):
        result = clean_text("The Quick Brown Fox Jumps Over 123 Lazy Dogs!", max_words=3)
        # Without lemmatization should lowercase and filter unwanted
        # 'the', 'over' are in unwanted
        # Words left: quick, brown, fox, jumps, lazy, dogs
        assert result == "quick_brown_fox"

    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    @patch("utils.text_processing.word_tokenize", side_effect=LookupError)
    def test_clean_text_tokenize_fallback(self, mock_tokenize):
        result = clean_text("a camelCase test")
        assert result == "camel_case_test"

    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    @patch("utils.text_processing.WordNetLemmatizer")
    def test_clean_text_lemmatize(self, mock_lemmatizer_class):
        mock_lemmatizer = MagicMock()
        mock_lemmatizer.lemmatize.side_effect = lambda x: x + "lem"
        mock_lemmatizer_class.return_value = mock_lemmatizer

        result = clean_text("testing words")
        assert "testinglem" in result or "testlem" in result

    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    @patch("utils.text_processing.WordNetLemmatizer", side_effect=ValueError("Failed"))
    def test_clean_text_lemmatize_error(self, mock_lemmatizer_class):
        result = clean_text("Test error")
        assert "test" in result

    def test_sanitize_filename(self):
        assert sanitize_filename("") == "untitled"
        assert sanitize_filename("Only the") == "untitled"
        assert sanitize_filename("A valid name") == "valid_name"
        assert sanitize_filename("A" * 100, max_length=10) == "a" * 10

    @patch("utils.text_processing.NLTK_AVAILABLE", False)
    def test_extract_keywords_no_nltk(self):
        result = extract_keywords("apple banana apple cherry")
        assert result == ["apple", "banana", "cherry"] or "apple" in result

    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    def test_extract_keywords_with_nltk(self):
        with patch(
            "utils.text_processing.word_tokenize",
            return_value=["apple", "apple", "banana", "the"],
        ):
            from utils.text_processing import stopwords

            with patch.object(stopwords, "words", return_value=["the"]):
                result = extract_keywords("dummy text", top_n=2)
                assert "apple" in result

    @patch("utils.text_processing.NLTK_AVAILABLE", True)
    @patch("utils.text_processing.word_tokenize", side_effect=RuntimeError("Error"))
    def test_extract_keywords_error(self, mock_tokenize):
        assert (
            extract_keywords(
                "Test",
            )
            == []
        )

    def test_truncate_text(self):
        assert truncate_text("short test", 100) == "short test"
        assert truncate_text("a" * 10, 5) == "aaaaa..."
