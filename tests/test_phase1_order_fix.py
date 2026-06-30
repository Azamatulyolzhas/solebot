"""Phase 1 — correct order capture.

Locks the invariants of the order-fix work:
  * brain order → Товар built ONLY from a catalog row (by id → size → colour); an
    invalid size/colour is never substituted into the order.
  * the name/phone FSM starts from the brain ONLY when the message carries buy-intent.
  * the brain's FREE text can never claim an order that didn't start this turn.
  * explicit product text in handle_order_flow is resolved against the catalog, not
    stored verbatim ('куплю тогда зару размер xl цвет белый' → a real label).
  * a buy phrase typed into the name slot is rejected.
  * letter clothing sizes (XL, размер m) are parsed without breaking numeric sizes.
"""
import asyncio

import ai
import orders
import products


def _run(coro):
    return asyncio.run(coro)


def _models():
    """Fresh per call so a test can't see another test's renumbered idx."""
    return [{
        "idx": 1, "name": "Zara Linen Shirt", "price": 15000,
        "sizes": ["M", "L", "XL"], "colors": ["белый", "синий"],
        "rows": [
            {"id": 1, "name": "Zara Linen Shirt", "price": 15000, "quantity": 5,
             "attributes": {"размер": "XL", "цвет": "белый"}},
            {"id": 2, "name": "Zara Linen Shirt", "price": 15000, "quantity": 3,
             "attributes": {"размер": "XL", "цвет": "синий"}},
            {"id": 3, "name": "Zara Linen Shirt", "price": 15000, "quantity": 2,
             "attributes": {"размер": "L", "цвет": "белый"}},
        ],
    }]


def _wire(monkeypatch, groq_json, captured=None):
    monkeypatch.setattr(ai, "_brain_models", lambda sid: _models())
    monkeypatch.setattr(ai, "resolve_groq_api_key", lambda sid: "key")

    async def fake_groq(*a, **k):
        return groq_json, {}
    monkeypatch.setattr(ai, "_groq_messages", fake_groq)

    async def noop(*a, **k):
        return None
    for fn in ("set_last_product_interest", "set_last_shown_products",
               "clear_miss_count", "save_ai_result"):
        monkeypatch.setattr(ai, fn, noop, raising=False)

    async def cap_state(uid, state):
        if captured is not None:
            captured["state"] = state
    monkeypatch.setattr("cache.set_order_state", cap_state, raising=False)


def _brain(msg):
    return _run(ai._brain_reply(1, {"name": "vardly"}, "u", msg, [], [], None, "tg", 0.0))


# ── 1.1 structural order from the brain ────────────────────────────────────────────
class TestBrainOrderCatalogBinding:
    def test_binds_row_by_size_and_color(self, monkeypatch):
        cap = {}
        _wire(monkeypatch,
              '{"reply":"","show":[],"order":{"ready":true,"id":1,"size":"XL","color":"белый"}}', cap)
        out = _brain("куплю белый xl")
        assert "ваше имя" in out.lower()
        # Товар is the catalog label (name + size + colour), never the raw LLM text.
        assert cap["state"]["product_interest"] == "Zara Linen Shirt (XL, белый)"

    def test_invalid_color_is_not_substituted(self, monkeypatch):
        cap = {}
        _wire(monkeypatch,
              '{"reply":"","show":[],"order":{"ready":true,"id":1,"size":"XL","color":"зелёный"}}', cap)
        out = _brain("беру xl зелёный")
        # invalid colour ignored → row resolved by the valid size only
        assert cap["state"]["product_interest"] == "Zara Linen Shirt (XL, белый)"
        assert "зелёный" not in cap["state"]["product_interest"]

    def test_invalid_size_reasks_and_does_not_start(self, monkeypatch):
        cap = {}
        _wire(monkeypatch,
              '{"reply":"","show":[],"order":{"ready":true,"id":1,"size":"42","color":"белый"}}', cap)
        out = _brain("куплю 42")
        assert "размер" in out.lower()
        assert "state" not in cap  # invalid size → ask, never bind the first row


# ── 1.2 intent guard before the FSM starts ─────────────────────────────────────────
class TestBrainOrderIntentGuard:
    def test_ready_without_buy_intent_does_not_collect_name(self, monkeypatch):
        cap = {}
        _wire(monkeypatch,
              '{"reply":"Эта рубашка из льна.","show":[1],'
              '"order":{"ready":true,"id":1,"size":"XL","color":"белый"}}', cap)
        out = _brain("из какого материала рубашка?")
        assert "state" not in cap                  # no order started
        assert "ваше имя" not in out.lower()        # not asking for the name
        assert "Zara Linen Shirt" in out            # answered with the product instead

    def test_ready_with_buy_intent_starts(self, monkeypatch):
        cap = {}
        _wire(monkeypatch,
              '{"reply":"","show":[],"order":{"ready":true,"id":1,"size":"XL","color":"белый"}}', cap)
        _brain("оформи заказ")
        assert cap["state"]["step"] == "name"


# ── 1.3 order-claim guard on the brain's free text ─────────────────────────────────
class TestBrainOrderClaimGuard:
    def test_show_branch_scrubs_false_order_claim(self, monkeypatch):
        _wire(monkeypatch,
              '{"reply":"Оформляем заказ! Напишите ваше имя.","show":[1],"order":{"ready":false}}')
        out = _brain("покажи рубашку")
        low = out.lower()
        assert "оформляем заказ" not in low
        assert "ваше имя" not in low
        assert "Zara Linen Shirt" in out  # cards still shown, only the claim is dropped

    def test_plain_text_false_claim_drops_to_fallback(self, monkeypatch):
        _wire(monkeypatch,
              '{"reply":"Отлично, оформим заказ. Напишите имя.","show":[],"order":{"ready":false}}')
        # Nothing usable that we can safely send → None, so the deterministic chain
        # (which never claims a phantom order) takes over.
        assert _brain("ну давай") is None


# ── 1.4 deterministic fresh-start fallback ─────────────────────────────────────────
class TestFreshStartResolvesCatalog:
    def test_explicit_remainder_resolves_to_label(self, monkeypatch):
        monkeypatch.setattr(orders, "resolve_shop_id", lambda s: s or 1)

        async def _zara(*a, **k):
            return [{"id": 1, "name": "Zara Linen Shirt",
                     "attributes": {"размер": "XL", "цвет": "белый"}}]
        monkeypatch.setattr(products, "get_relevant_products", _zara)

        out = _run(orders._resolve_product_interest(
            "u", "куплю тогда зару размер xl цвет белый", 1))
        assert "Zara Linen Shirt" in out
        assert "тогда" not in out  # the raw sentence is never stored as Товар


# ── 1.6 name validation ────────────────────────────────────────────────────────────
class TestNameRejectsBuyPhrase:
    def test_rejects_buy_phrase_in_name_slot(self):
        assert not orders._is_valid_name("куплю тогда зару размер xl цвет белый")

    def test_accepts_real_name(self):
        assert orders._is_valid_name("Азамат")


# ── 1.7 letter sizes ───────────────────────────────────────────────────────────────
class TestLetterSizes:
    def test_prefixed_and_bare_letter_sizes(self):
        assert products.extract_attribute_filters("размер xl")["size"] == "XL"
        assert products.extract_attribute_filters("XL")["size"] == "XL"
        assert products.extract_attribute_filters("хочу размер m")["size"] == "M"

    def test_no_false_positive_on_plain_words(self):
        # bare ambiguous single letters need the 'размер' cue → not a size here
        assert "size" not in products.extract_attribute_filters("люблю спорт")

    def test_numeric_sizes_unbroken(self):
        assert products.extract_attribute_filters("размер 43")["size"] == "43"
        assert "size" not in products.extract_attribute_filters("New Balance 574")
