"""Regression tests for the selection-recall fix (BUG 2, P1).

A selection / continuation phrase ('давай нью баланс', '1 вариант') fails a fresh
catalog search (cross-script brand, ordinals), but it refers to the set the bot
JUST showed. ask_ai must fall back to that shown set instead of dead-ending in
'не нашёл' — while a genuine miss with no prior context still returns not-found.
"""
import asyncio

import ai


def _run(coro):
    return asyncio.run(coro)


def _patch_pipeline(monkeypatch):
    """Drive ask_ai down to the product-search branch with everything offline."""
    async def noop(*a, **kw): return None
    async def noop_false(*a, **kw): return False
    async def noop_history(*a, **kw): return []
    async def allowed_rate(*a, **kw): return True, 100

    monkeypatch.setattr(ai, "clear_chat_context", noop)
    monkeypatch.setattr(ai, "ensure_session_fresh", noop)
    monkeypatch.setattr(ai, "get_handoff_state", noop_false)
    monkeypatch.setattr(ai, "check_rate_limit", allowed_rate)
    monkeypatch.setattr(ai, "load_session_history", noop_history)
    monkeypatch.setattr(ai, "save_session_message", noop)
    monkeypatch.setattr(ai, "save_message", lambda *a, **kw: None)
    monkeypatch.setattr(ai, "get_or_create_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(ai, "save_ai_result", noop)

    monkeypatch.setattr(ai, "resolve_shop_id", lambda sid: sid or 1)
    monkeypatch.setattr(ai, "is_subscription_active", lambda sid: True)
    monkeypatch.setattr(ai, "check_message_quota", lambda sid: (True, 0, 500))
    monkeypatch.setattr(ai, "resolve_groq_api_key", lambda sid: "fake-key")
    monkeypatch.setattr(ai, "get_shop_by_id", lambda sid: {"id": 1, "name": "S"})
    monkeypatch.setattr(ai, "get_catalog_sample", lambda *a, **kw: [])

    monkeypatch.setattr(ai, "handle_order_flow", noop)            # not an order
    monkeypatch.setattr(ai, "is_greeting", lambda m: False)
    monkeypatch.setattr(ai, "is_browse_query", lambda m: False)

    async def _product_intent(*a, **kw): return "PRODUCT"
    monkeypatch.setattr(ai, "classify_intent", _product_intent)

    # Force the fresh-search path: not a follow-up phrase by the heuristics, and
    # the fresh catalog search misses (simulating the cross-script brand miss).
    monkeypatch.setattr(ai, "is_followup_question", lambda m, li=None: False)
    monkeypatch.setattr(ai, "is_affirmation", lambda m: False)

    async def _empty_search(*a, **kw): return []
    monkeypatch.setattr(ai, "get_relevant_products", _empty_search)

    monkeypatch.setattr(ai, "set_last_product_interest", noop)
    monkeypatch.setattr(ai, "set_last_shown_products", noop)
    monkeypatch.setattr(ai, "clear_miss_count", noop)


class TestFollowupFallback:
    def test_selection_falls_back_to_shown_set(self, monkeypatch):
        _patch_pipeline(monkeypatch)

        nb = {"id": 35, "name": "New Balance 990v5", "price": 75000, "quantity": 2}

        async def _prior(user_id, shop_id):
            return [nb]
        monkeypatch.setattr(ai, "resolve_followup_products", _prior)

        seen = {}

        async def _reply(shop_id, shop, products, query, key, **kw):
            seen["followup"] = kw.get("followup")
            seen["names"] = [p["name"] for p in products]
            return ("Это New Balance 990v5 — отличный выбор.", {}, "ai_followup")
        monkeypatch.setattr(ai, "build_product_reply", _reply)

        reply = _run(ai.ask_ai("tg_1_55", "давай нью баланс", shop_id=1))

        # Reached the followup reply over the just-shown set — NOT 'не нашёл'.
        assert reply == "Это New Balance 990v5 — отличный выбор."
        assert reply != ai.product_not_found_reply()
        assert seen["followup"] is True
        assert seen["names"] == ["New Balance 990v5"]

    def test_genuine_miss_without_context_still_not_found(self, monkeypatch):
        _patch_pipeline(monkeypatch)

        async def _no_prior(user_id, shop_id):
            return []
        monkeypatch.setattr(ai, "resolve_followup_products", _no_prior)

        async def _miss(user_id): return 1
        monkeypatch.setattr(ai, "inc_miss_count", _miss)

        called = {"build": False}

        async def _reply(*a, **kw):
            called["build"] = True
            return ("x", {}, "ai_followup")
        monkeypatch.setattr(ai, "build_product_reply", _reply)

        reply = _run(ai.ask_ai("tg_1_56", "пума хайтопы", shop_id=1))

        # No prior set to fall back to → honest not-found, no product reply built.
        assert reply == ai.product_not_found_reply()
        assert called["build"] is False
