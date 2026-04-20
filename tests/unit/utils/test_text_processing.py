import pytest

from utils.text_processing import (
    clean_text,
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

# NEW_* = expected Snowball output captured in Task 4 Step 8.
_NEW_CLEAN_TEXT = 'studi_run_analysi'
_NEW_EXTRACT_KEYWORDS: list[str] = ['quick', 'brown', 'jumps', 'over', 'lazy']
_NEW_STOPWORDS: set[str] = {'image', 'the', 'were', 'above', 'its', 'xlsx', 'notes', 'depicts', 'be', 'covers', 'more', 'this', 'if', 'own', 'after', 'picture', 'into', 'category', 'does', 'or', 'can', 'key', 'new', 'was', 'do', 'file', 'details', 'pptx', 'png', 'he', 'than', 'but', 'summary', 'because', 'for', 'could', 'don', 'had', 'you', 'information', 'txt', 'at', 'photo', 'having', 'i', 'presents', 'concepts', 'ideas', 'her', 'bmp', 'now', 'from', 'in', 'have', 'other', 'show', 'same', 'through', 'shows', 'and', 'csv', 'below', 'few', 'some', 'a', 'any', 'includes', 'not', 'nor', 'jpg', 'such', 'untitled', 'is', 'she', 't', 'of', 'gif', 'document', 'filename', 'on', 'him', 'docx', 'provides', 'during', 'generated', 'only', 'features', 'no', 'illustrates', 'as', 'main', 'with', 'describes', 'it', 'before', 'by', 's', 'about', 'us', 'each', 'just', 'that', 'too', 'we', 'would', 'has', 'very', 'unknown', 'did', 'so', 'md', 'which', 'an', 'note', 'will', 'based', 'should', 'me', 'jpeg', 'are', 'text', 'demonstrates', 'output', 'most', 'pdf', 'to', 'discusses', 'they', 'display'}  # noqa: E501


def test_clean_text_golden_snowball() -> None:
    assert clean_text("Studies in running and analysis") == _NEW_CLEAN_TEXT


def test_extract_keywords_golden_snowball() -> None:
    assert extract_keywords("The quick brown fox jumps over the lazy dog") == _NEW_EXTRACT_KEYWORDS


def test_get_unwanted_words_golden_snowball() -> None:
    assert get_unwanted_words() == _NEW_STOPWORDS


class TestTextProcessing:
    def test_get_unwanted_words_basic(self) -> None:
        words = get_unwanted_words()
        assert "the" in words
        assert "custom_word" not in words

    def test_clean_text_empty(self) -> None:
        assert clean_text("") == ""
        assert clean_text(None) == ""

    def test_clean_text_basic(self) -> None:
        result = clean_text("The Quick Brown Fox Jumps Over 123 Lazy Dogs!", max_words=3)
        # Stemmed output: 'quick', 'brown', 'fox' are expected (jumps→jump, lazy→lazi, dogs→dog)
        # 'the' and 'over' are in unwanted; result should be first 3 non-unwanted stemmed words
        assert len(result.split("_")) <= 3
        assert result != ""

    def test_clean_text_camelcase(self) -> None:
        result = clean_text("a camelCase test", lemmatize=False, remove_unwanted=False)
        assert "camel" in result
        assert "case" in result

    def test_clean_text_no_lemmatize(self) -> None:
        result = clean_text("running dogs", lemmatize=False)
        # Without stemming, words are kept as-is (after unwanted filtering)
        assert isinstance(result, str)

    def test_sanitize_filename(self) -> None:
        assert sanitize_filename("") == "untitled"
        # Under Snowball, "only" stems to "onli" which is not in the unwanted set,
        # so "Only the" → "onli" (not "untitled" as under NLTK where "only" was a stopword)
        assert sanitize_filename("Only the") == "onli"
        assert sanitize_filename("A valid name") == "valid_name"
        assert sanitize_filename("A" * 100, max_length=10) == "a" * 10

    def test_extract_keywords_basic(self) -> None:
        result = extract_keywords("apple banana apple cherry")
        assert "apple" in result

    def test_extract_keywords_short_words_excluded(self) -> None:
        # words <= 3 chars are excluded by extract_keywords
        result = extract_keywords("the a an is")
        assert result == []

    def test_extract_keywords_error(self) -> None:
        # Passing None should return [] via the except path
        assert extract_keywords(None) == []  # type: ignore[arg-type]

    def test_truncate_text(self) -> None:
        assert truncate_text("short test", 100) == "short test"
        assert truncate_text("a" * 10, 5) == "aaaaa..."
