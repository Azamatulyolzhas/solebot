"""Phase 2 — the brain must not re-dump the SAME catalog cards on a follow-up.

A follow-up about already-shown items ('есть бежевый?', 'из какого материала?',
'дешевле?') should be answered with text, not a verbatim repeat of the cards. The
brain's show branch now mirrors the deterministic repeat_list=False gate: identical
DB row ids vs last turn → text only. A different set (new search / real narrowing)
still renders cards, and an unchanged set with no safe text falls back to cards so we
never send an empty message.

Brain tests mock _brain_models / resolve_groq_api_key / _groq_messages (and, here,
get_last_shown_products to control the 'last shown' set), per the existing pattern.
"""
import asyncio

import ai


def _run(coro):
    return asyncio.run(coro)


def _models():
    """Fresh per call so a test never sees another test's renumbered idx."""
    return [
        {"idx": 1, "name": "Vans Old Skool", "price": 22000,
         "sizes": ["42", "43"], "colors": ["чёрный", "белый"],
         "rows": [
             {"id": 11, "name": "Vans Old Skool", "price": 22000, "quantity": 5,
              "attributes": {"размер": "42", "цвет": "чёрный"}},
             {"id": 12, "name": "Vans Old Skool", "price": 22000, "quantity": 3,
              "attributes": {"размер": "43", "цвет": "чёрный"}},
         ]},
        {"idx": 2, "name": "Nike Air Force 1", "price": 48000,
         "sizes": ["42"], "colors": ["белый"],
         "rows": [
             {"id": 21, "name": "Nike Air Force 1", "price": 48000, "quantity": 6,
              "attributes": {"размер": "42", "цвет": "белый"}},
         ]},
    ]


def _wire(monkeypatch, groq_json, shown_ids):
    monkeypatch.setattr(ai, "_brain_models", lambda sid: _models())
    monkeypatch.setattr(ai, "resolve_groq_api_key", lambda sid: "key")

    async def fake_groq(*a, **k):
        return groq_json, {}
    monkeypatch.setattr(ai, "_groq_messages", fake_groq)

    async def shown(*a, **k):
        return [{"id": i, "name": "x", "sku": None} for i in shown_ids]
    monkeypatch.setattr(ai, "get_last_shown_products", shown)

    async def noop(*a, **k):
        return None
    for fn in ("set_last_product_interest", "set_last_shown_products",
               "clear_miss_count", "save_ai_result"):
        monkeypatch.setattr(ai, fn, noop, raising=False)


def _brain(msg):
    return _run(ai._brain_reply(1, {"name": "vardly"}, "u", msg, [], [], None, "tg", 0.0))


class TestFollowupSuppressesCards:
    def test_unchanged_set_with_text_replies_text_only(self, monkeypatch):
        _wire(
            monkeypatch,
            '{"reply":"Бежевого нет, есть чёрный и белый — какой ближе?",'
            '"show":[1],"order":{"ready":false}}',
            shown_ids=[11, 12],
        )
        out = _brain("а бежевый есть?")
        assert "Бежевого нет" in out
        # The card block (catalog header + price) must be absent on the repeat.
        assert "22000" not in out
        assert "Vans Old Skool" not in out

    def test_empty_show_returns_plain_text(self, monkeypatch):
        _wire(
            monkeypatch,
            '{"reply":"Чёрный и белый в наличии, какой берём?","show":[],"order":{"ready":false}}',
            shown_ids=[11, 12],
        )
        out = _brain("какие цвета есть?")
        assert "Чёрный и белый в наличии" in out
        assert "В каталоге" not in out  # no card block


class TestRealListingStillShows:
    def test_different_set_shows_cards(self, monkeypatch):
        # Brain narrows to a DIFFERENT model (Nike id 21) than last shown (Vans 11,12)
        # — a real new set, so the cards MUST render (the gate fires only on identical ids).
        _wire(
            monkeypatch,
            '{"reply":"Вот, пожалуйста.","show":[2],"order":{"ready":false}}',
            shown_ids=[11, 12],
        )
        out = _brain("покажи найк")
        assert "Nike Air Force 1" in out
        assert "48000" in out

    def test_unchanged_set_without_safe_text_falls_back_to_cards(self, monkeypatch):
        # Same set as last turn but no usable text → show the cards, never an empty message.
        _wire(
            monkeypatch,
            '{"reply":"","show":[1],"order":{"ready":false}}',
            shown_ids=[11, 12],
        )
        out = _brain("ну покажи")
        assert "Vans Old Skool" in out
        assert "22000" in out

    def test_unchanged_set_with_unsafe_text_falls_back_to_cards(self, monkeypatch):
        # Same set, text invents a number → unsafe → drop the text, still show cards.
        _wire(
            monkeypatch,
            '{"reply":"Спецпредложение 99999","show":[1],"order":{"ready":false}}',
            shown_ids=[11, 12],
        )
        out = _brain("ну покажи")
        assert "Vans Old Skool" in out
        assert "99999" not in out
