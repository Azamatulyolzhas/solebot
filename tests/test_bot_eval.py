"""End-to-end behavioural eval — golden multi-turn scenarios run through ask_ai.

This is the pre-deploy safety net: it locks in the bugs fixed in P0-P3 + the
hotfixes so they can't silently regress. It seeds a real (temp SQLite) catalog and
drives whole conversations through `ask_ai`.

Groq is stubbed by a deterministic dispatcher (`_fake_groq`) that mimics each call
type — product search returns NONE (so recall is exercised by the real keyword +
transliteration backstop), intent fails to PRODUCT_REQUEST, the selected-product
picker returns "can't decide", and the tone line is a fixed safe sentence. So the
eval is stable yet follows the SAME routing/search/order/render paths as prod.

Run before every deploy:  pytest tests/test_bot_eval.py
"""
import asyncio
import json

import pytest

import ai
import cache
import db
import orders
import schema


SHOP_ID = 1

# (name, price, qty, sku, attributes)
CATALOG = [
    ("Adidas Ultraboost 22 Black", 55000, 2, "SNK-AD-UB22-B43", {"размер": "43"}),
    ("Adidas Stan Smith", 32000, 5, "SNK-AD-SS-42", {"размер": "42"}),
    ("New Balance 574 Grey", 36000, 4, "SNK-NB-574-G42", {"размер": "42"}),
    ("New Balance 990v5", 75000, 2, "SNK-NB-990-G42", {"размер": "42"}),
    ("Nike Air Force 1 Low", 48000, 6, "SNK-NK-AF1-W42", {"размер": "42"}),
    ("Converse Chuck Taylor", 18500, 3, "SNK-CV-CT-41", {"размер": "41"}),
]

_CACHE_DICTS = (
    "chat_sessions", "order_states", "last_product_interest",
    "last_shown_products", "session_last_activity",
)


def _clear_cache():
    for name in _CACHE_DICTS:
        getattr(cache, name).clear()


async def _fake_groq(shop_id, messages, api_key, **kw):
    """Deterministic stand-in for the Groq call, dispatched by system prompt."""
    sys = messages[0]["content"] if messages else ""
    if "поисковый движок" in sys:          # _PRODUCT_SEARCH_SYSTEM
        return "NONE", {}                  # → exercise the real keyword/translit recall
    if "PRODUCT_REQUEST" in sys:           # _INTENT_SYSTEM
        return "PRODUCT_REQUEST", {}
    if "какой ОДИН товар" in sys:          # resolve_selected_product picker
        return "0", {}                     # "can't decide" → confirm step
    if "продавец-консультант" in sys:      # _TONE_SYSTEM (P3 tone line)
        return "Подскажите размер — подберу 🙂", {}
    return "ок", {}


@pytest.fixture
def bot(monkeypatch, tmp_path):
    """Seed a temp catalog + neutralise cross-cutting deps; yield a `converse` fn."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "eval.db"))
    schema.ensure_app_tables()
    ph = db.db_placeholder()
    for name, price, qty, sku, attrs in CATALOG:
        db.execute_write(
            f"INSERT INTO products (shop_id, name, description, sku, category, "
            f"price, quantity, attributes) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (SHOP_ID, name, "", sku, "обувь", price, qty, json.dumps(attrs, ensure_ascii=False)),
        )
    _clear_cache()

    # Fake key + deterministic Groq dispatcher → prod-like paths, zero network.
    monkeypatch.setattr(ai, "resolve_groq_api_key", lambda sid: "fake-eval-key")
    monkeypatch.setattr(ai, "_groq_messages", _fake_groq)
    monkeypatch.setattr(ai, "get_shop_by_id", lambda sid: {"id": SHOP_ID, "name": "vardly"})
    monkeypatch.setattr(ai, "is_subscription_active", lambda sid: True)
    monkeypatch.setattr(ai, "check_message_quota", lambda sid: (True, 0, 500))

    async def _noop(*a, **k): return None
    async def _false(*a, **k): return False
    async def _rate(*a, **k): return True, 100
    monkeypatch.setattr(ai, "ensure_session_fresh", _noop)
    monkeypatch.setattr(ai, "clear_chat_context", _noop)
    monkeypatch.setattr(ai, "get_handoff_state", _false)
    monkeypatch.setattr(ai, "check_rate_limit", _rate)
    monkeypatch.setattr(ai, "save_ai_result", _noop)
    monkeypatch.setattr(ai, "get_or_create_conversation", lambda *a, **k: None)
    monkeypatch.setattr(ai, "save_message", lambda *a, **k: None)
    monkeypatch.setattr(ai, "load_recent_messages", lambda *a, **k: [])
    # Order flow: keep create_order real (writes the seeded DB), mute the notifier.
    monkeypatch.setattr(orders, "notify_shop_owner", _noop)

    def converse(messages, user_id="tg_1_eval"):
        return [asyncio.run(ai.ask_ai(user_id, m, shop_id=SHOP_ID)) for m in messages]

    yield converse
    _clear_cache()


def _orders():
    return orders.list_orders(shop_id=SHOP_ID)


# ── Scenario 1: order never captures a product the customer didn't choose ──────
def test_order_pins_confirmed_product_no_phantom(bot):
    replies = bot(["адидас", "хочу купить", "стэн смит", "Иван", "87010000000"])
    # >1 Adidas shown → pin is ambiguous → confirm step asks instead of guessing.
    assert "какой именно товар" in replies[1].lower()
    rows = _orders()
    assert len(rows) == 1
    product = rows[0]["product_interest"]
    assert "Stan Smith" in product
    for phantom in ("Nike", "Converse", "New Balance", "Ultraboost"):
        assert phantom not in product


# ── Scenario 2: cross-script first-touch brand is found, not dead-ended ────────
def test_cyrillic_brand_first_touch(bot):
    reply = bot(["нью баланс"])[0]
    assert "New Balance" in reply
    assert "не нашёл" not in reply.lower()


# ── Scenario 3: category we don't carry → search, not a full catalog dump ──────
def test_unstocked_category_does_not_dump_catalog(bot):
    reply = bot(["какие товары есть для футбола"])[0]
    # routed to SEARCH (no football in catalog) → honest not-found, not a dump
    assert "не нашёл" in reply.lower() or "не вижу" in reply.lower()
    listed = sum(b in reply for b in ("Adidas", "Nike", "Converse", "New Balance"))
    assert listed == 0


# ── Scenario 4: genuine browse still lists the catalog ────────────────────────
def test_browse_lists_catalog(bot):
    reply = bot(["покажи каталог"])[0]
    assert any(b in reply for b in ("Adidas", "Nike", "Converse", "New Balance"))


# ── Scenario 5: no hallucinated product — only real catalog items appear ───────
def test_no_invented_product(bot):
    # Catalog has Nike Air Force, NOT Air Max. The deterministic render can only
    # show what exists, so an invented 'Air Max' can never appear.
    reply = bot(["nike"])[0]
    assert "Air Force" in reply
    assert "Air Max" not in reply


# ── Scenario 7: a followup selection narrows to ONE product card ──────────────
def test_followup_selection_shows_single_product(bot):
    replies = bot(["адидас", "2 вариант"])
    both = replies[0]
    assert "Adidas Ultraboost 22 Black" in both and "Adidas Stan Smith" in both
    # the selection narrows to exactly one of the two Adidas
    sel = replies[1]
    shown = ("Adidas Ultraboost 22 Black" in sel, "Adidas Stan Smith" in sel)
    assert shown.count(True) == 1


# ── Scenario 6: a finished order does not bleed into the next one ──────────────
def test_order_isolation_between_orders(bot):
    bot(["стэн смит", "хочу купить", "Иван", "87010000000"], user_id="tg_1_iso")
    bot(["конверс", "хочу купить", "Пётр", "87020000000"], user_id="tg_1_iso")
    rows = _orders()
    assert len(rows) == 2
    newest = rows[0]["product_interest"]   # list_orders is DESC by id
    assert "Converse" in newest
    assert "Stan Smith" not in newest
