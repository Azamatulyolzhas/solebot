"""Order size binding — the 'ordered 42 when 43 was asked' bug.

A model shown as several size variants must bind the size the customer actually
named (or ask for it), never silently the first size in catalog order.
"""
import asyncio

import ai
import cache
import orders


def _run(coro):
    return asyncio.run(coro)


def _stan_rows():
    # One model, three size variants — exactly the live-bug shape.
    return [
        {"id": 1, "name": "Adidas Stan Smith", "price": 32000, "quantity": 1,
         "attributes": {"размер": "42"}},
        {"id": 2, "name": "Adidas Stan Smith", "price": 32000, "quantity": 1,
         "attributes": {"размер": "43"}},
        {"id": 3, "name": "Adidas Stan Smith", "price": 32000, "quantity": 1,
         "attributes": {"размер": "44"}},
    ]


def _wire_rows(monkeypatch, rows, history):
    async def _rows(*a, **kw):
        return rows

    async def _hist(*a, **kw):
        return history

    monkeypatch.setattr(ai, "resolve_followup_products", _rows)
    monkeypatch.setattr(ai, "load_session_history", _hist)


# ── resolve_selected_product ──────────────────────────────────────────────────────

class TestResolveHonorsStatedSize:
    def test_binds_the_stated_size_not_the_first_row(self, monkeypatch):
        _wire_rows(
            monkeypatch, _stan_rows(),
            [{"role": "user", "content": "мне нужны белые 43 размера"}],
        )
        # Customer said 43 → must bind the 43 row, NOT the first (42) row.
        assert _run(ai.resolve_selected_product("u", 1)) == "Adidas Stan Smith (43)"

    def test_stated_size_works_from_a_later_message(self, monkeypatch):
        _wire_rows(
            monkeypatch, _stan_rows(),
            [{"role": "user", "content": "что насчет адидас стан"},
             {"role": "user", "content": "размер 43 же"}],
        )
        assert _run(ai.resolve_selected_product("u", 1)) == "Adidas Stan Smith (43)"

    def test_no_stated_size_asks_instead_of_guessing(self, monkeypatch):
        _wire_rows(
            monkeypatch, _stan_rows(),
            [{"role": "user", "content": "его и куплю"}],
        )
        # No size anywhere → never bind the first row; signal the caller to ask.
        assert _run(ai.resolve_selected_product("u", 1)) is ai.ORDER_NEEDS_SIZE


# ── _normalize_product_interest (bare size is not a product) ───────────────────────

class TestBareSizeIsNotProduct:
    def test_buy_plus_bare_size_resolves_from_context(self):
        # 'хочу купить 43 размер' must NOT store '43 размер' as the product name.
        assert orders._normalize_product_interest("хочу купить 43 размер") == ""

    def test_real_product_still_captured(self):
        assert orders._normalize_product_interest(
            "хочу купить найк аир форс"
        ) == "найк аир форс"


# ── handle_order_flow end-to-end ──────────────────────────────────────────────────

class TestOrderFlowSize:
    def _reset(self, user):
        _run(cache.clear_order_state(user))

    def test_buy_with_stated_size_starts_order_at_right_size(self, monkeypatch):
        user = "tg_1_size_a"
        self._reset(user)
        _wire_rows(
            monkeypatch, _stan_rows(),
            [{"role": "user", "content": "белые 43 размера"}],
        )
        reply = _run(orders.handle_order_flow(user, "его и куплю", 1))
        assert "Напишите" in reply and "имя" in reply
        state = _run(cache.get_order_state(user))
        assert state["product_interest"] == "Adidas Stan Smith (43)"
        assert state["step"] == "name"

    def test_buy_without_size_asks_then_binds(self, monkeypatch):
        user = "tg_1_size_b"
        self._reset(user)
        _wire_rows(
            monkeypatch, _stan_rows(),
            [{"role": "user", "content": "его и куплю"}],
        )
        # 1) No size known → ask, list available sizes, move to order_size step.
        ask = _run(orders.handle_order_flow(user, "его и куплю", 1))
        assert "размер" in ask.lower()
        assert "43" in ask
        assert _run(cache.get_order_state(user))["step"] == "order_size"

        # 2) Customer answers '43' → bind the 43 row and proceed to name.
        reply = _run(orders.handle_order_flow(user, "43", 1))
        assert "Напишите" in reply and "имя" in reply
        state = _run(cache.get_order_state(user))
        assert state["product_interest"] == "Adidas Stan Smith (43)"
        assert state["step"] == "name"

    def test_order_size_rejects_unavailable_size(self, monkeypatch):
        user = "tg_1_size_c"
        self._reset(user)
        _wire_rows(monkeypatch, _stan_rows(), [])
        _run(cache.set_order_state(user, {"step": "order_size"}))
        reply = _run(orders.handle_order_flow(user, "50", 1))
        assert "не нашёл такой размер" in reply.lower()
        # Still waiting on a valid size — not advanced to name.
        assert _run(cache.get_order_state(user))["step"] == "order_size"
