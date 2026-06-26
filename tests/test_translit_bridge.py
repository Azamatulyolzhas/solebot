"""Tests for the cross-script transliteration bridge (BUG 2, P1.5).

A phonetic Cyrillic brand ('адидас', 'нью баланс') doesn't substring-match the
catalog's Latin words. The bridge transliterates + fuzzy-matches the query words
against the shop's OWN name tokens, so the keyword search recovers them — with
no hardcoded brand list (it only ever returns tokens that exist in the catalog).
"""
import products


CATALOG = [
    {"name": "Adidas Ultraboost 22 Black"},
    {"name": "Adidas Stan Smith"},
    {"name": "New Balance 990v5"},
    {"name": "New Balance 574 Grey"},
    {"name": "Nike Air Force 1 Low"},
    {"name": "Asics Gel-Lyte III White"},
    {"name": "Sony WH-1000XM5 Black"},
]
TOKENS = products._catalog_name_tokens(CATALOG)


class TestTranslit:
    def test_cyrillic_brand_to_latin(self):
        assert products._translit_cyr_to_lat("адидас") == "adidas"

    def test_digraph_resolves(self):
        assert products._translit_cyr_to_lat("баланс") == "balans"

    def test_latin_word_unchanged(self):
        assert products._translit_cyr_to_lat("balance") == "balance"
        assert products._translit_cyr_to_lat("new") == "new"


class TestBridge:
    def test_adidas_matches_exact_token(self):
        assert "adidas" in products._bridge_translit_to_catalog(["адидас"], TOKENS)

    def test_balans_fuzzy_matches_balance(self):
        # 'нью' is too distorted to bridge, but 'баланс' → 'balans' ≈ 'balance'.
        out = products._bridge_translit_to_catalog(["нью", "баланс"], TOKENS)
        assert "balance" in out

    def test_only_returns_catalog_tokens(self):
        # A Cyrillic word with no close catalog token bridges to nothing —
        # never invents a brand that isn't in this shop.
        assert products._bridge_translit_to_catalog(["телевизор"], TOKENS) == []

    def test_latin_query_not_bridged(self):
        assert products._bridge_translit_to_catalog(["new", "balance"], TOKENS) == []

    def test_no_catalog_tokens(self):
        assert products._bridge_translit_to_catalog(["адидас"], []) == []

    def test_does_not_duplicate_existing_word(self):
        out = products._bridge_translit_to_catalog(["adidas", "адидас"], TOKENS)
        assert out.count("adidas") == 0  # already present in input words
