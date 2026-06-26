"""Tests for is_browse_query after the BUG 1 fix.

'товары для футбола' contains the browse noun 'товары', but it also names a topic
('футбол') — the customer wants a search, not a full catalog dump. Browse now
requires that EVERY meaningful word be browse vocabulary.
"""
import products


class TestBrowseQuery:
    def test_browse_only_words(self):
        assert products.is_browse_query("покажи каталог")
        assert products.is_browse_query("что есть")
        assert products.is_browse_query("весь ассортимент")
        assert products.is_browse_query("покажи все")
        assert products.is_browse_query("товары")
        assert products.is_browse_query("каталог")

    def test_empty_or_stopwords_is_browse(self):
        assert products.is_browse_query("")
        assert products.is_browse_query("что у вас есть")

    def test_browse_noun_plus_topic_is_search(self):
        # The BUG 1 regression: a topic word must NOT be swallowed by the dump.
        assert not products.is_browse_query("товары для футбола")
        assert not products.is_browse_query("Какие товары у вас есть для футбола")
        assert not products.is_browse_query("весь ассортимент кроссовок")

    def test_specific_query_is_search(self):
        assert not products.is_browse_query("красные найки 42 размер")
        assert not products.is_browse_query("кроссовки для бега")
