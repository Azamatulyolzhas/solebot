"""Regression tests for P3: product facts are rendered deterministically, the LLM
only writes a product-agnostic tone line.

The bug: the LLM authored the product list as free prose and invented items not in
the catalog ('Nike Air Max 90 за 42000'), which then bound a wrong order. Now the
LLM can only contribute a tone sentence with no product facts — a fabricated
name/price/size never reaches the customer.
"""
import asyncio

import ai


def run(coro):
    return asyncio.run(coro)


PRODUCTS = [
    {"id": 1, "name": "Adidas Ultraboost 22 Black", "price": 55000, "quantity": 2},
    {"id": 2, "name": "New Balance 574 Grey", "price": 36000, "quantity": 4},
]


class TestToneSafety:
    def test_rejects_numbers(self):
        assert not ai._tone_is_safe("Ещё есть Nike Air Max за 42000 ₸!")
        assert not ai._tone_is_safe("Есть размер 42")

    def test_rejects_currency_and_discount(self):
        assert not ai._tone_is_safe("всего 5000 тенге")
        assert not ai._tone_is_safe("Сегодня действует скидка!")  # no digits, still unsafe

    def test_accepts_plain_question(self):
        assert ai._tone_is_safe("Подскажите размер — проверю наличие 🙂")

    def test_rejects_empty(self):
        assert not ai._tone_is_safe("")

    def test_rejects_order_completion_claim(self):
        assert not ai._tone_is_safe("Ваш заказ принят, спасибо за покупку!")
        assert not ai._tone_is_safe("Заказ оформлен, ждите доставку")
        assert not ai._tone_is_safe("Спасибо за заказ!")

    def test_allows_order_invitation(self):
        # Inviting to order is fine — only CLAIMING it's done is rejected.
        assert ai._tone_is_safe("Хотите оформить заказ?")
        assert ai._tone_is_safe("Подсказать что-то ещё или оформляем заказ?")


class TestBuildProductReply:
    def _patch_tone(self, monkeypatch, tone_reply):
        async def _grok(shop_id, messages, api_key, **kw):
            return tone_reply, {"total_tokens": 5}
        monkeypatch.setattr(ai, "_groq_messages", _grok)

    def test_hallucinated_tone_dropped_facts_stay_real(self, monkeypatch):
        # LLM tries to smuggle a fake product + price into the tone line.
        self._patch_tone(monkeypatch, "Ещё есть Nike Air Max 90 за 42000 ₸, бери!")
        reply, _usage, mode = run(
            ai.build_product_reply(1, {"name": "S"}, PRODUCTS, "найк", "key")
        )
        assert mode == "ai_product"
        # Real facts from the deterministic renderer:
        assert "Adidas Ultraboost 22 Black" in reply
        assert "55000" in reply
        # The fabricated product and its price never appear:
        assert "Air Max" not in reply
        assert "42000" not in reply
        # Tone fell back to a fixed safe question:
        assert ai._TONE_FALLBACKS["default"] in reply

    def test_order_completion_claim_in_tone_dropped(self, monkeypatch):
        # The LLM tries to claim the order is done before the flow created one.
        self._patch_tone(monkeypatch, "Готово, ваш заказ принят! Спасибо за покупку 🙂")
        reply, _u, _m = run(
            ai.build_product_reply(1, {"name": "S"}, PRODUCTS, "беру", "key")
        )
        assert "заказ принят" not in reply.lower()
        assert "спасибо за покупку" not in reply.lower()
        assert ai._TONE_FALLBACKS["default"] in reply

    def test_safe_tone_is_kept(self, monkeypatch):
        self._patch_tone(monkeypatch, "Подскажите ваш размер — подберу 🙂")
        reply, _u, _m = run(
            ai.build_product_reply(1, {"name": "S"}, PRODUCTS, "найк", "key")
        )
        assert "Подскажите ваш размер" in reply
        assert "New Balance 574 Grey" in reply and "36000" in reply

    def test_no_api_key_real_cards_fixed_tone(self):
        reply, _u, _m = run(
            ai.build_product_reply(1, {"name": "S"}, PRODUCTS, "найк", None)
        )
        assert "Adidas Ultraboost 22 Black" in reply
        assert ai._TONE_FALLBACKS["default"] in reply

    def test_objection_leads_cheapest(self, monkeypatch):
        self._patch_tone(monkeypatch, "На какой бюджет ориентируетесь? 🙂")
        reply, _u, mode = run(
            ai.build_product_reply(1, {"name": "S"}, PRODUCTS, "дорого", "key", objection=True)
        )
        assert mode == "ai_objection"
        # cheapest (New Balance 36000) is rendered before Adidas (55000)
        assert reply.index("New Balance 574 Grey") < reply.index("Adidas Ultraboost 22 Black")

    def test_empty_products_not_found(self):
        reply, _u, mode = run(ai.build_product_reply(1, {"name": "S"}, [], "x", "key"))
        assert mode == "catalog_not_found"
        assert reply == ai.product_not_found_reply()
