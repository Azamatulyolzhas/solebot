"""Regression tests for the phantom-product / order-correctness fix (BUG 3).

The bug: a finished order left the running product interest live, and the next
'хочу купить' pulled that stale, multi-product string into a new binding order.
These tests pin the two invariants of the P0 fix:

  1. _resolve_product_interest never returns the running interest — it returns
     the confirmed product or "" (so the flow asks instead of guessing).
  2. Completing an order clears the product context, so order N+1 cannot see
     order N's products.
"""
import asyncio

import ai
import cache
import orders


def run(coro):
    return asyncio.run(coro)


def _reset(user_id: str) -> None:
    for d in (cache.order_states, cache.last_product_interest,
              cache.last_shown_products, cache.chat_sessions):
        d.pop(user_id, None)


class TestResolveProductInterestNoLeak:
    def test_returns_empty_when_nothing_pinned(self, monkeypatch):
        """The dangerous old fallback returned the whole running interest here."""
        monkeypatch.setattr(orders, "resolve_shop_id", lambda s: s or 1)

        async def _none(*a, **k):
            return None
        monkeypatch.setattr(ai, "resolve_selected_product", _none)
        # Even with a polluted running interest present, it must NOT be returned.
        cache.last_product_interest["tg_1_x"] = "Adidas, Nike Air Force 1 Low, Nike Air Force 1 Low"

        out = run(orders._resolve_product_interest("tg_1_x", "хочу купить", 1))
        assert out == ""
        _reset("tg_1_x")

    def test_returns_pinned_product(self, monkeypatch):
        monkeypatch.setattr(orders, "resolve_shop_id", lambda s: s or 1)

        async def _pin(*a, **k):
            return "Adidas Ultraboost 22 Black (43)"
        monkeypatch.setattr(ai, "resolve_selected_product", _pin)

        out = run(orders._resolve_product_interest("tg_1_y", "хочу купить", 1))
        assert out == "Adidas Ultraboost 22 Black (43)"

    def test_explicit_text_resolved_against_catalog(self, monkeypatch):
        # Phase 1.4: explicit product text is NOT stored verbatim — it is resolved
        # against the catalog, so the order's Товар is a real catalog label, never the
        # raw phrase (the 'тогда зару размер xl цвет белый' transcript bug).
        import products
        monkeypatch.setattr(orders, "resolve_shop_id", lambda s: s or 1)

        async def _nike(*a, **k):
            return [{"id": 5, "name": "Nike Air Force 1 Low", "attributes": {}}]
        monkeypatch.setattr(products, "get_relevant_products", _nike)

        out = run(orders._resolve_product_interest("tg_1_z", "хочу купить найк аир", 1))
        assert out == "Nike Air Force 1 Low"

    def test_explicit_text_with_no_catalog_hit_asks(self, monkeypatch):
        # Explicit text that matches nothing → "" so the flow asks which product,
        # instead of binding the order to an unrecognised phrase.
        import products
        monkeypatch.setattr(orders, "resolve_shop_id", lambda s: s or 1)

        async def _empty(*a, **k):
            return []
        monkeypatch.setattr(products, "get_relevant_products", _empty)

        out = run(orders._resolve_product_interest("tg_1_z2", "хочу купить абвгд", 1))
        assert out == ""


def _patch_order_io(monkeypatch, captured: dict):
    """Stub the side-effecting bits so the FSM can run without DB/network."""
    monkeypatch.setattr(orders, "resolve_shop_id", lambda s: s or 1)

    def _create(channel, ext, name, phone, product_interest, shop_id=None):
        captured["product_interest"] = product_interest
        return 99
    monkeypatch.setattr(orders, "create_order", _create)

    async def _notify(*a, **k):
        return None
    monkeypatch.setattr(orders, "notify_shop_owner", _notify)


class TestOrderFlowFix:
    def test_pin_fails_then_confirm_step(self, monkeypatch):
        uid = "tg_1_confirm"
        _reset(uid)
        captured: dict = {}
        _patch_order_io(monkeypatch, captured)

        async def _none(*a, **k):
            return None
        monkeypatch.setattr(ai, "resolve_selected_product", _none)

        async def _confirmed(user_id, msg, shop_id=None):
            return "New Balance 990v5"
        monkeypatch.setattr(orders, "_resolve_confirmed_product", _confirmed)

        # 1) order request, pin fails -> ask which product
        r1 = run(orders.handle_order_flow(uid, "хочу купить", 1))
        assert "какой именно товар" in r1.lower()
        assert cache.order_states[uid]["step"] == "confirm_product"

        # 2) customer names it -> resolved, move to name
        r2 = run(orders.handle_order_flow(uid, "нью баланс", 1))
        assert "имя" in r2.lower()
        assert cache.order_states[uid]["product_interest"] == "New Balance 990v5"

        # 3) name -> phone -> confirm -> order created with the CONFIRMED product
        run(orders.handle_order_flow(uid, "Иван", 1))
        r3 = run(orders.handle_order_flow(uid, "87012345678", 1))
        assert "проверьте заказ" in r3.lower()        # confirmation step, not created yet
        assert "product_interest" not in captured
        run(orders.handle_order_flow(uid, "да", 1))   # confirm -> create
        assert captured["product_interest"] == "New Balance 990v5"
        _reset(uid)

    def test_completion_clears_product_context(self, monkeypatch):
        """Order N must not leave its products visible to order N+1."""
        uid = "tg_1_iso"
        _reset(uid)
        captured: dict = {}
        _patch_order_io(monkeypatch, captured)

        async def _pin(*a, **k):
            return "Adidas Ultraboost 22 Black (43)"
        monkeypatch.setattr(ai, "resolve_selected_product", _pin)

        # Seed leftover context from a hypothetical earlier order.
        cache.last_product_interest[uid] = "Sony WH-1000XM5 Black, JBL Tune 760NC"
        cache.last_shown_products[uid] = [{"id": 1, "name": "Sony WH-1000XM5 Black"}]

        run(orders.handle_order_flow(uid, "хочу купить", 1))      # -> name
        run(orders.handle_order_flow(uid, "Иван", 1))            # -> phone
        run(orders.handle_order_flow(uid, "87012345678", 1))    # -> confirm
        run(orders.handle_order_flow(uid, "да", 1))             # -> create + clear

        assert captured["product_interest"] == "Adidas Ultraboost 22 Black (43)"
        # The isolation invariant: leftovers are gone after completion.
        assert uid not in cache.last_product_interest
        assert uid not in cache.last_shown_products
        _reset(uid)
