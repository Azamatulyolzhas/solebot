"""Direct unit tests for ai.sandbox_reply.

These complement tests/test_sandbox_chat.py (which mocks sandbox_reply at the
endpoint boundary). Here we exercise the function itself: greeting routing,
no-API-key short-circuit, catalog-not-found fallback.
"""
import asyncio

import ai


def _run(coro):
    return asyncio.run(coro)


class TestSandboxReplyRouting:

    def test_empty_message_short_circuits(self, monkeypatch):
        result = _run(ai.sandbox_reply(1, "   "))
        assert result["mode"] == "empty"
        assert result["products"] == []
        assert "Введите" in result["reply"]

    def test_no_api_key_returns_hint(self, monkeypatch):
        monkeypatch.setattr(ai, "resolve_groq_api_key", lambda sid: None)
        monkeypatch.setattr(ai, "get_shop_by_id", lambda sid: {"id": 1, "name": "S"})
        monkeypatch.setattr(ai, "is_greeting", lambda m: False)
        monkeypatch.setattr(ai, "is_browse_query", lambda m: False)

        result = _run(ai.sandbox_reply(1, "хочу кроссовки"))
        assert result["mode"] == "no_api_key"
        assert "Groq API" in result["reply"]
        assert result["products"] == []

    def test_catalog_not_found(self, monkeypatch):
        async def empty_search(*a, **kw):
            return []

        monkeypatch.setattr(ai, "resolve_groq_api_key", lambda sid: "fake-key")
        monkeypatch.setattr(ai, "get_shop_by_id", lambda sid: {"id": 1, "name": "S"})
        monkeypatch.setattr(ai, "is_greeting", lambda m: False)
        monkeypatch.setattr(ai, "is_browse_query", lambda m: False)
        monkeypatch.setattr(ai, "get_relevant_products", empty_search)

        result = _run(ai.sandbox_reply(1, "ищу что-то странное"))
        assert result["mode"] == "catalog_not_found"
        assert result["products"] == []

    def test_product_match_returns_metadata(self, monkeypatch):
        async def match_search(*a, **kw):
            return [
                {"name": "Кроссовки X", "price": 15000, "category": "shoes", "quantity": 5},
                {"name": "Кроссовки Y", "price": 18000, "category": "shoes", "quantity": 3},
            ]

        async def fake_reply(shop_id, shop, products, query, key, **kw):
            return ("Есть две модели за 15-18 тысяч.", {"total_tokens": 100}, "ai_product")

        monkeypatch.setattr(ai, "resolve_groq_api_key", lambda sid: "fake-key")
        monkeypatch.setattr(ai, "get_shop_by_id", lambda sid: {"id": 1, "name": "S"})
        monkeypatch.setattr(ai, "is_greeting", lambda m: False)
        monkeypatch.setattr(ai, "is_browse_query", lambda m: False)
        monkeypatch.setattr(ai, "get_relevant_products", match_search)
        monkeypatch.setattr(ai, "build_product_reply", fake_reply)

        result = _run(ai.sandbox_reply(1, "хочу кроссовки"))
        assert result["mode"] == "ai_product"
        assert "две модели" in result["reply"]
        assert len(result["products"]) == 2
        assert result["products"][0]["name"] == "Кроссовки X"
        assert result["products"][0]["price"] == 15000
