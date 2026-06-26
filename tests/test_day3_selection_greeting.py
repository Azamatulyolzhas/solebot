"""Day 3 tests: followup narrows to ONE product, and the greeting carries no
product facts / order claims.
"""
import asyncio

import ai


def run(coro):
    return asyncio.run(coro)


class TestSelectOne:
    P = [{"id": 1, "name": "Adidas Ultraboost 22 Black"},
         {"id": 2, "name": "New Balance 990v5"}]

    def test_ordinal_digit(self):
        assert ai._select_one("2 вариант", self.P)["name"] == "New Balance 990v5"
        assert ai._select_one("1", self.P)["name"] == "Adidas Ultraboost 22 Black"

    def test_ordinal_word(self):
        assert ai._select_one("давай второй", self.P)["name"] == "New Balance 990v5"

    def test_name_match_translit(self):
        assert ai._select_one("адидас", self.P)["name"] == "Adidas Ultraboost 22 Black"
        assert ai._select_one("balance", self.P)["name"] == "New Balance 990v5"

    def test_two_digit_size_does_not_select(self):
        # '42' is a size, not an ordinal — keep the set.
        assert ai._select_one("42", self.P) is None

    def test_ambiguous_returns_none(self):
        two_nb = [{"id": 1, "name": "New Balance 574 Grey"},
                  {"id": 2, "name": "New Balance 990v5"}]
        assert ai._select_one("balance", two_nb) is None

    def test_single_product_passthrough(self):
        assert ai._select_one("да", [{"id": 9, "name": "X"}])["name"] == "X"

    def test_empty(self):
        assert ai._select_one("2", []) is None


class TestGreetingGuard:
    def _patch(self, monkeypatch, reply):
        async def _g(shop_id, messages, api_key, **kw):
            return reply, {}
        monkeypatch.setattr(ai, "_groq_messages", _g)
        monkeypatch.setattr(ai, "greeting_reply", lambda sid: "CANNED")

    def test_greeting_with_price_falls_back(self, monkeypatch):
        self._patch(monkeypatch, "Привет! У нас есть кроссовки за 55000 ₸")
        reply, _u, mode = run(ai.build_greeting_reply(1, {"name": "S"}, "привет", "key", []))
        assert reply == "CANNED" and mode == "greeting"

    def test_greeting_with_order_claim_falls_back(self, monkeypatch):
        self._patch(monkeypatch, "Здравствуйте! Ваш заказ принят 🙂")
        reply, _u, mode = run(ai.build_greeting_reply(1, {"name": "S"}, "привет", "key", []))
        assert reply == "CANNED" and mode == "greeting"

    def test_clean_greeting_kept(self, monkeypatch):
        self._patch(monkeypatch, "Здравствуйте! Что вы ищете сегодня?")
        reply, _u, mode = run(ai.build_greeting_reply(1, {"name": "S"}, "привет", "key", []))
        assert "Здравствуйте" in reply and mode == "ai_greeting"
