"""Regression tests for the confirm-step guard (order #3 phantom-product fix).

A customer confirming 'найк айр макс вы же не оформили заказ' must NOT have the
order bound to an unrelated top search hit (a brown 'Куртка Carhartt' matched via
the filler 'не' inside 'коричНЕвый'). The confirm step now (a) drops filler
particles from the search and (b) only trusts a hit that actually matches the
customer's words, else keeps their literal text.
"""
import asyncio

import orders
import products


def run(coro):
    return asyncio.run(coro)


class TestHitValidator:
    def test_rejects_unrelated_hit(self):
        carhartt = {"name": "Куртка Carhartt Detroit Jacket"}
        assert orders._hit_matches_query("найк айр макс вы же не оформили заказ", carhartt) is False

    def test_accepts_clean_translit(self):
        adidas = {"name": "Adidas Ultraboost 22 Black"}
        assert orders._hit_matches_query("адидас", adidas) is True

    def test_accepts_latin_match(self):
        nb = {"name": "New Balance 990v5"}
        assert orders._hit_matches_query("new balance", nb) is True

    def test_empty_name(self):
        assert orders._hit_matches_query("адидас", {"name": ""}) is False


class TestConfirmResolution:
    def test_falls_back_to_literal_on_unrelated_hit(self, monkeypatch):
        monkeypatch.setattr(orders, "resolve_shop_id", lambda s: s or 1)

        async def _carhartt(*a, **k):
            return [{"id": 9, "name": "Куртка Carhartt Detroit Jacket",
                     "attributes": {"размер": "L", "цвет": "коричневый"}}]
        monkeypatch.setattr(products, "get_relevant_products", _carhartt)

        text = "найк айр макс вы же не оформили заказ"
        out = run(orders._resolve_confirmed_product("tg_1_x", text, 1))
        assert "Carhartt" not in out
        # literal kept, but trailing filler trimmed off
        assert out == "найк айр макс"

    def test_uses_label_on_matching_hit(self, monkeypatch):
        monkeypatch.setattr(orders, "resolve_shop_id", lambda s: s or 1)

        async def _adidas(*a, **k):
            return [{"id": 1, "name": "Adidas Ultraboost 22 Black", "attributes": {}}]
        monkeypatch.setattr(products, "get_relevant_products", _adidas)

        out = run(orders._resolve_confirmed_product("tg_1_y", "адидас", 1))
        assert out == "Adidas Ultraboost 22 Black"

    def test_returns_none_on_no_catalog_hit(self, monkeypatch):
        # Pure garbage ('Роло') matches nothing → None, so the flow re-asks
        # instead of binding the order to junk.
        monkeypatch.setattr(orders, "resolve_shop_id", lambda s: s or 1)

        async def _empty(*a, **k):
            return []
        monkeypatch.setattr(products, "get_relevant_products", _empty)

        assert run(orders._resolve_confirmed_product("tg_1_z", "Роло", 1)) is None


class TestFillerStopwords:
    def test_particles_filtered_from_search(self):
        words = products.extract_query_words("красные не кожаные же ещё")
        for filler in ("не", "же", "ещё", "еще"):
            assert filler not in words


class TestTrimProductPhrase:
    def test_trims_trailing_filler(self):
        assert orders._trim_to_product_phrase(
            "найк айр макс вы же не оформили заказ") == "найк айр макс"
        assert orders._trim_to_product_phrase("адидас хочу купить") == "адидас"

    def test_keeps_mid_phrase_stopword(self):
        # 'для' is a stopword but mid-phrase — must not be trimmed.
        assert orders._trim_to_product_phrase("кроссовки для бега") == "кроссовки для бега"

    def test_all_filler_keeps_original(self):
        assert orders._trim_to_product_phrase("хочу купить заказ") == "хочу купить заказ"
