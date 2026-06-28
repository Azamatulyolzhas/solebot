"""Unit tests for pure bot-logic helpers in ai.py.

These are the highest-bug-density functions in the hot path: intent detection
and the anti-hallucination reply validator. No DB / network involved.
"""
import asyncio

import ai


def _run(coro):
    return asyncio.run(coro)


# ── is_greeting ────────────────────────────────────────────────────────────────

class TestIsGreeting:
    def test_single_greeting_word(self):
        assert ai.is_greeting("привет")
        assert ai.is_greeting("Здравствуйте")
        assert ai.is_greeting("hello")
        assert ai.is_greeting("салем")

    def test_greeting_phrase(self):
        assert ai.is_greeting("добрый день")
        assert ai.is_greeting("Доброе утро")

    def test_greeting_with_punctuation(self):
        assert ai.is_greeting("привет!")

    def test_not_a_greeting(self):
        assert not ai.is_greeting("хочу купить найк")
        assert not ai.is_greeting("есть ли кроссовки 42 размера")

    def test_single_word_typo_is_greeting(self):
        # 'пивет'/'привт' must NOT fall through to the OFF_TOPIC classifier.
        assert ai.is_greeting("пивет")
        assert ai.is_greeting("привт")

    def test_product_word_is_not_a_greeting(self):
        assert not ai.is_greeting("найки")
        assert not ai.is_greeting("кроссовки")

    def test_empty_or_too_long(self):
        assert not ai.is_greeting("")
        assert not ai.is_greeting("   ")
        assert not ai.is_greeting("привет " * 20)  # > 50 chars


# ── is_rejection ───────────────────────────────────────────────────────────────

class TestIsRejection:
    def test_simple_rejections(self):
        assert ai.is_rejection("нет")
        assert ai.is_rejection("не хочу")
        assert ai.is_rejection("no")
        assert ai.is_rejection("Нет, спасибо")

    def test_not_rejection(self):
        assert not ai.is_rejection("да, хочу")
        assert not ai.is_rejection("покажите красные кроссовки")

    def test_no_word_inside_a_request_is_not_rejection(self):
        # 'нет' starts a request for OTHER items — this is what made the bot reply
        # the nonsensical 'Хорошо, понял…' and loop.
        assert not ai.is_rejection("нет другие модели кроме этих")
        assert not ai.is_rejection("нет, покажи ещё варианты")

    def test_yes_no_question_is_not_rejection(self):
        # A yes/no question wants an answer, not a goodbye.
        assert not ai.is_rejection("так есть или нет?")
        assert not ai.is_rejection("есть или нет")

    def test_empty(self):
        assert not ai.is_rejection("")


class TestWantsMoreOptions:
    def test_detects_other_more_besides(self):
        assert ai._wants_more_options("есть другие модели")
        assert ai._wants_more_options("дай мне модели найки другиеее")
        assert ai._wants_more_options("есть что-то ещё?")
        assert ai._wants_more_options("кроме айр макс есть?")

    def test_plain_query_is_not_more(self):
        assert not ai._wants_more_options("кроссы от найк")
        assert not ai._wants_more_options("для бега")
        assert not ai._wants_more_options("давай айр макс черный")


# ── is_followup_question ───────────────────────────────────────────────────────

class TestIsFollowup:
    def test_marker_phrases(self):
        assert ai.is_followup_question("а сколько стоит?")
        assert ai.is_followup_question("расскажи подробнее")
        assert ai.is_followup_question("есть скидка?")

    def test_short_question_mark(self):
        assert ai.is_followup_question("а размеры?")

    def test_matches_last_interest(self):
        # A >=4-char word from the remembered interest reappears in the query.
        assert ai.is_followup_question("а кроссовки ещё есть", last_interest="красные кроссовки")

    def test_interest_match_is_script_sensitive(self):
        # Substring match is lowercase-only, NOT transliterated: a Cyrillic
        # query does not match a Latin-script remembered interest.
        assert not ai.is_followup_question("а самба есть", last_interest="Adidas Samba OG")

    def test_plain_statement_is_not_followup(self):
        assert not ai.is_followup_question("хочу заказать новые кроссовки сегодня вечером")

    def test_empty(self):
        assert not ai.is_followup_question("")


# ── validate_groq_reply (anti-hallucination guard) ─────────────────────────────

class TestValidateGroqReply:
    products = [{"name": "Nike Air Force 1", "price": 45000, "sku": "AF1"}]

    def test_valid_reply_mentions_real_price_and_name(self):
        reply = "У нас есть Nike Air Force 1 за 45000 тенге, отличный выбор."
        assert ai.validate_groq_reply(reply, self.products, require_product=True)

    def test_rejects_hallucinated_price(self):
        reply = "Nike Air Force 1 стоит 99999 тенге."
        assert not ai.validate_groq_reply(reply, self.products, require_product=True)

    def test_rejects_forbidden_discount_words(self):
        reply = "Nike Air Force 1 за 45000, сегодня скидка!"
        assert not ai.validate_groq_reply(reply, self.products, require_product=True)

    def test_require_product_fails_without_name(self):
        reply = "У нас отличные товары за 45000 тенге."
        assert not ai.validate_groq_reply(reply, self.products, require_product=True)

    def test_empty_reply_or_products(self):
        assert not ai.validate_groq_reply("", self.products)
        assert not ai.validate_groq_reply("что-то", [])

    def test_short_numbers_under_3_digits_are_ignored(self):
        # "42" (size) has < 3 digits, must not count as a hallucinated price
        reply = "Nike Air Force 1, размер 42, цена 45000 тенге."
        assert ai.validate_groq_reply(reply, self.products, require_product=True)

    def test_allows_number_inside_product_name(self):
        # 'Forerunner 265' — the 265 in the model name must NOT read as a fake price
        products = [{"name": "Garmin Forerunner 265", "price": 290000, "sku": "WCH-GM-FR265"}]
        reply = "Garmin Forerunner 265 — отличные часы за 290000 тенге."
        assert ai.validate_groq_reply(reply, products, require_product=True)

    def test_still_rejects_invented_price_with_name_numbers(self):
        products = [{"name": "Galaxy A55", "price": 180000, "sku": "PH-SS-A55"}]
        reply = "Samsung Galaxy A55 за 999000 тенге."  # 999000 invented
        assert not ai.validate_groq_reply(reply, products, require_product=True)


# ── _clean_reply ───────────────────────────────────────────────────────────────

class TestCleanReply:
    def test_strips_bold_and_italic(self):
        assert ai._clean_reply("**Жирный** и *курсив*") == "Жирный и курсив"

    def test_strips_headings(self):
        assert ai._clean_reply("# Заголовок\nтекст") == "Заголовок\nтекст"

    def test_strips_sku_in_parens(self):
        assert ai._clean_reply("Nike Air (SKU:AF1-42)") == "Nike Air"

    def test_strips_bare_sku_label(self):
        assert ai._clean_reply("SKU:AF1-42 в наличии") == "в наличии"

    def test_plain_text_unchanged(self):
        assert ai._clean_reply("Обычный текст") == "Обычный текст"


# ── _parse_sku_response ────────────────────────────────────────────────────────

class TestParseSkuResponse:
    def test_comma_separated(self):
        assert ai._parse_sku_response("AF1, SAMBA, NB550") == ["AF1", "SAMBA", "NB550"]

    def test_none_sentinel(self):
        assert ai._parse_sku_response("NONE") == []
        assert ai._parse_sku_response("none") == []

    def test_takes_first_line_only(self):
        assert ai._parse_sku_response("AF1, SAMBA\nignored line") == ["AF1", "SAMBA"]

    def test_empty(self):
        assert ai._parse_sku_response("") == []
        assert ai._parse_sku_response(None) == []


# ── _append_order_hint ─────────────────────────────────────────────────────────

class TestAppendOrderHint:
    def test_appends_hint(self):
        out = ai._append_order_hint("Есть Nike Air.")
        assert "хочу купить" in out.lower()

    def test_no_double_hint(self):
        once = ai._append_order_hint("Есть Nike Air.")
        twice = ai._append_order_hint(once)
        assert twice.lower().count("хочу купить") == 1

    def test_appends_even_when_tone_has_trigger_phrase(self):
        # The buy button must appear even when the tone already says 'оформить
        # заказ' — previously that suppressed the sentinel and killed the button.
        out = ai._append_order_hint("Хотите оформить заказ на эту пару?")
        assert ai._ORDER_HINT in out


# ── _trim_history ──────────────────────────────────────────────────────────────

class TestTrimHistory:
    def test_keeps_last_n_and_drops_empty(self):
        history = [{"role": "user", "content": f"m{i}"} for i in range(12)]
        trimmed = ai._trim_history(history, limit=8)
        assert len(trimmed) == 8
        assert trimmed[-1]["content"] == "m11"

    def test_drops_invalid_roles_and_blank_content(self):
        history = [
            {"role": "system", "content": "x"},
            {"role": "user", "content": "  "},
            {"role": "assistant", "content": "ok"},
        ]
        assert ai._trim_history(history) == [{"role": "assistant", "content": "ok"}]


# ── _persona_line (greeting double-word fix) ────────────────────────────────────

class TestPersonaLine:
    def test_role_with_magazin_no_double(self):
        out = ai._persona_line("консультант магазина техники", "TechnoDom")
        assert out == "консультант магазина техники «TechnoDom»"
        assert "магазина техники магазина" not in out

    def test_role_without_magazin_adds_it(self):
        assert ai._persona_line("продавец", "TechnoDom") == "продавец магазина «TechnoDom»"

    def test_blank_or_default_name_returns_role_only(self):
        assert ai._persona_line("консультант", "") == "консультант"
        assert ai._persona_line("консультант", "магазина") == "консультант"


# ── _avoid_identical_repeat ─────────────────────────────────────────────────────

class TestAvoidIdenticalRepeat:
    def test_appends_nudge_when_identical_to_last_bot_msg(self):
        history = [{"role": "assistant", "content": "Список A"}]
        out = ai._avoid_identical_repeat("Список A", history)
        assert out != "Список A"
        assert out.startswith("Список A")

    def test_unchanged_when_different(self):
        history = [{"role": "assistant", "content": "Список A"}]
        assert ai._avoid_identical_repeat("Список B", history) == "Список B"

    def test_unchanged_when_no_history(self):
        assert ai._avoid_identical_repeat("Список A", []) == "Список A"

    def test_ignores_user_messages_for_comparison(self):
        history = [{"role": "user", "content": "Список A"}]
        assert ai._avoid_identical_repeat("Список A", history) == "Список A"


# ── product_reply_fallback (non-bare list) ──────────────────────────────────────

class TestProductReplyFallback:
    def test_multi_item_adds_followup_question(self):
        products = [
            {"name": "A", "price": 100, "quantity": 1},
            {"name": "B", "price": 200, "quantity": 1},
        ]
        out = ai.product_reply_fallback(products)
        assert "подробнее" in out.lower()


# ── _finalize_product_reply (buy CTA only on a single concrete product) ──────────

class TestFinalizeProductReply:
    def test_single_product_gets_buy_hint(self):
        out = ai._finalize_product_reply("В каталоге: Nike Air — 100₸.", [{"name": "Nike Air"}])
        assert ai._ORDER_HINT in out

    def test_multi_product_has_no_buy_hint(self):
        out = ai._finalize_product_reply(
            "По каталогу нашёл:\n• A\n• B", [{"name": "A"}, {"name": "B"}]
        )
        assert ai._ORDER_HINT not in out
        assert "хочу купить" not in out.lower()

    def test_fallback_multi_item_has_no_buy_hint(self):
        products = [
            {"name": "A", "price": 100, "quantity": 1},
            {"name": "B", "price": 200, "quantity": 1},
        ]
        assert ai._ORDER_HINT not in ai.product_reply_fallback(products)

    def test_fallback_single_item_has_buy_hint(self):
        assert ai._ORDER_HINT in ai.product_reply_fallback(
            [{"name": "A", "price": 100, "quantity": 1}]
        )


# ── _tone_repeats_absent_term (no phantom-brand affirmation) ────────────────────

class TestToneAbsentBrand:
    _NIKE = [{"name": "Nike Air Force 1 Low"}, {"name": "Nike Air Max 90 Black"}]
    _ADIDAS = [{"name": "Adidas Ultraboost 22 Black"}]

    def test_brand_terms_drop_filler(self):
        assert ai._query_brand_terms("есть именно джорданы?") == ["джорданы"]

    def test_covered_translit(self):
        assert ai._covered_by_products("адидас", self._ADIDAS)
        assert not ai._covered_by_products("джорданы", self._NIKE)

    def test_rejects_tone_echoing_absent_brand(self):
        tone = "Ищете именно Джорданы, да, есть интересные варианты, какая расцветка ближе?"
        assert ai._tone_repeats_absent_term(tone, "есть именно джорданы?", self._NIKE)

    def test_allows_neutral_tone_for_absent_brand(self):
        # Honest neutral tone doesn't echo the absent brand → allowed.
        tone = "Подскажите, какой размер нужен — подберу точнее 🙂"
        assert not ai._tone_repeats_absent_term(tone, "есть именно джорданы?", self._NIKE)

    def test_allows_tone_for_covered_brand(self):
        tone = "Adidas — отличный выбор! Какой размер?"
        assert not ai._tone_repeats_absent_term(tone, "адидас", self._ADIDAS)

    def test_use_case_query_not_treated_as_absent_brand(self):
        # 'беговые' isn't echoed by a good tone → no false rejection.
        tone = "Отличный выбор для бега! Подскажите размер 🙂"
        assert not ai._tone_repeats_absent_term(tone, "беговые кроссовки", self._ADIDAS)


# ── is_affirmation (confirmation must not re-search) ─────────────────────────────

class TestIsAffirmation:
    def test_yes_and_selection_variants(self):
        assert ai.is_affirmation("Да")
        assert ai.is_affirmation("ок")
        assert ai.is_affirmation("давай 43")
        assert ai.is_affirmation("43")

    def test_real_product_query_is_not_affirmation(self):
        assert not ai.is_affirmation("хочу кроссовки для бега")
        assert not ai.is_affirmation("давай тогда 43")  # 3 words = a refinement
        assert not ai.is_affirmation("")


class TestIsOrderYes:
    def test_pure_yes_words(self):
        assert ai._is_order_yes("да")
        assert ai._is_order_yes("давай")
        assert ai._is_order_yes("оформляй")

    def test_number_is_not_order_yes(self):
        # A size must stay a size selection, not start the order.
        assert not ai._is_order_yes("43")
        assert not ai._is_order_yes("давай 43")

    def test_other_is_not_order_yes(self):
        assert not ai._is_order_yes("покажи ещё")
        assert not ai._is_order_yes("")


class TestRefineWithinShown:
    _ADIDAS = [
        {"id": 1, "name": "Adidas Stan Smith", "attributes": {"size": 42}},
        {"id": 2, "name": "Adidas Stan Smith", "attributes": {"size": 43}},
        {"id": 3, "name": "Adidas Stan Smith", "attributes": {"size": 44}},
        {"id": 4, "name": "Adidas Ultraboost 22 Black", "attributes": {"size": 42}},
        {"id": 5, "name": "Adidas Ultraboost 22 Black", "attributes": {"size": 43}},
    ]
    _NIKE = [
        {"id": 10, "name": "Nike Air Max 90 Black", "attributes": {"size": 42}},
        {"id": 11, "name": "Nike Air Force 1 Low", "attributes": {"size": 43}},
    ]

    def test_names_shown_model_narrows_to_family(self):
        # 'давайте стан смив' → Stan Smith only (no unrelated Converse/Ultraboost).
        out = ai._refine_within_shown("хорошо давайте тогда стан смив", self._ADIDAS)
        assert out is not None
        assert {p["name"] for p in out} == {"Adidas Stan Smith"}

    def test_model_plus_size_narrows_to_one_row(self):
        out = ai._refine_within_shown("стан смит 43", self._ADIDAS)
        assert [p["id"] for p in out] == [2]

    def test_bare_size_narrows_size(self):
        out = ai._refine_within_shown("43", self._ADIDAS)
        assert out and all(str((p["attributes"]).get("size")) == "43" for p in out)

    def test_order_word_plus_size_is_refinement(self):
        out = ai._refine_within_shown("оформляем 43 размер", self._ADIDAS)
        assert out and all(str((p["attributes"]).get("size")) == "43" for p in out)

    def test_new_brand_is_not_refinement(self):
        # 'адидас 43' / 'давайте адидас' while Nike is shown → new search, not refine.
        assert ai._refine_within_shown("адидас 43", self._NIKE) is None
        assert ai._refine_within_shown("или давайте адидас", self._NIKE) is None

    def test_select_family_ambiguous_returns_empty(self):
        assert ai._select_family("адидас", self._ADIDAS) == []

    def test_family_sizes_sorted(self):
        assert ai._family_sizes(self._ADIDAS[:3]) == ["42", "43", "44"]


# ── LLM brain ────────────────────────────────────────────────────────────────────

class TestBrainHelpers:
    def test_parse_plain_json(self):
        d = ai._parse_brain_json('{"reply":"hi","show":[1,2],"order":{"ready":false}}')
        assert d["reply"] == "hi" and d["show"] == [1, 2]

    def test_parse_fenced_and_prose_wrapped(self):
        assert ai._parse_brain_json('```json\n{"reply":"hi","show":[]}\n```') == {"reply": "hi", "show": []}
        assert ai._parse_brain_json('Конечно! {"reply":"ok","show":[3]} вот')["show"] == [3]

    def test_parse_garbage_is_none(self):
        assert ai._parse_brain_json("просто текст без json") is None
        assert ai._parse_brain_json("") is None

    def test_coerce_int_list(self):
        assert ai._coerce_int_list([1, "2", 2, "x"]) == [1, 2]
        assert ai._coerce_int_list("3") == [3]
        assert ai._coerce_int_list(None) == []

    def test_catalog_text_format(self):
        models = [{"idx": 1, "name": "Vans Old Skool Black", "price": 22000,
                   "sizes": ["42", "43"], "colors": ["чёрный"], "rows": []}]
        out = ai._brain_catalog_text(models)
        assert out.startswith("[1] Vans Old Skool Black — 22000₸")
        assert "размеры: 42,43" in out and "цвет: чёрный" in out

    def test_reply_safe_rejects_invented_price_and_discount(self):
        products = [{"name": "Vans Old Skool Black", "price": 22000}]
        assert ai._brain_reply_safe("Есть Vans за 22000₸", products)
        assert not ai._brain_reply_safe("Только сегодня скидка 19999₸!", products)

    def test_reply_safe_allows_text_without_numbers(self):
        assert ai._brain_reply_safe("Есть чёрные и белые, какой берёте?", [{"name": "x", "price": 1}])

    def test_row_for_size(self):
        rows = [{"attributes": {"size": 42}}, {"attributes": {"size": 43}}]
        assert ai._row_for_size(rows, "43") is rows[1]
        assert ai._row_for_size(rows, "99") is rows[0]  # no match → first


class TestBrainReply:
    _MODELS = [{
        "idx": 1, "name": "Vans Old Skool Black", "price": 22000,
        "sizes": ["42", "43"], "colors": ["чёрный"],
        "rows": [
            {"id": 1, "name": "Vans Old Skool Black", "price": 22000, "quantity": 5, "attributes": {"size": 42}},
            {"id": 2, "name": "Vans Old Skool Black", "price": 22000, "quantity": 3, "attributes": {"size": 43}},
        ],
    }]

    def _wire(self, monkeypatch, groq_json):
        monkeypatch.setattr(ai, "_brain_models", lambda sid: self._MODELS)
        monkeypatch.setattr(ai, "resolve_groq_api_key", lambda sid: "key")

        async def fake_groq(*a, **k):
            return groq_json, {}
        monkeypatch.setattr(ai, "_groq_messages", fake_groq)

        async def noop(*a, **k):
            return None
        for fn in ("set_last_product_interest", "set_last_shown_products",
                   "clear_miss_count", "save_ai_result", "set_order_state"):
            monkeypatch.setattr(ai, fn, noop, raising=False)

    def test_show_path_renders_real_cards(self, monkeypatch):
        self._wire(monkeypatch, '{"reply":"Отличные кеды!","show":[1],"order":{"ready":false}}')
        out = _run(ai._brain_reply(1, {"name": "vardly"}, "u", "ванс", [], [], None, "tg", 0.0))
        assert "Vans Old Skool Black" in out and "22000" in out
        assert "Отличные кеды" in out

    def test_order_ready_with_size_starts_order(self, monkeypatch):
        captured = {}

        async def cap_state(uid, state):
            captured["state"] = state
        self._wire(monkeypatch, '{"reply":"","show":[],"order":{"ready":true,"id":1,"size":"43"}}')
        monkeypatch.setattr("cache.set_order_state", cap_state, raising=False)
        out = _run(ai._brain_reply(1, {"name": "vardly"}, "u", "беру ванс 43", [], [], None, "tg", 0.0))
        assert "ваше имя" in out.lower()
        assert captured["state"]["product_interest"] == "Vans Old Skool Black (43)"

    def test_bad_json_falls_back(self, monkeypatch):
        self._wire(monkeypatch, "это не json")
        assert _run(ai._brain_reply(1, {"name": "vardly"}, "u", "привет", [], [], None, "tg", 0.0)) is None

    def test_rate_limited_returns_sentinel(self, monkeypatch):
        # A 429 must be distinguishable from a normal 'declined' (None) so the caller
        # can degrade deterministically instead of cascading into more Groq calls.
        monkeypatch.setattr(ai, "_brain_models", lambda sid: self._MODELS)
        monkeypatch.setattr(ai, "resolve_groq_api_key", lambda sid: "key")

        async def fake_groq(*a, **k):
            return None, {"error": "rate_limit"}
        monkeypatch.setattr(ai, "_groq_messages", fake_groq)
        out = _run(ai._brain_reply(1, {"name": "vardly"}, "u", "ванс", [], [], None, "tg", 0.0))
        assert out is ai._BRAIN_RATE_LIMITED


# ── _bound_catalog_for_llm (prompt size stays bounded as the catalog grows) ──────

class TestBoundCatalogForLLM:
    def test_noop_when_under_cap(self):
        items = [{"name": f"P{i}", "price": i} for i in range(5)]
        assert ai._bound_catalog_for_llm(items, "что есть", cap=10) is items

    def test_keeps_query_relevant_first(self):
        items = [{"name": "Adidas Stan Smith", "price": 30000}]
        items += [{"name": f"Filler {i}", "price": 1000 + i} for i in range(20)]
        out = ai._bound_catalog_for_llm(items, "адидас стан смит", cap=5)
        assert len(out) == 5
        # Cyrillic query bridges to the Latin catalog name → relevant row leads.
        assert out[0]["name"] == "Adidas Stan Smith"

    def test_pads_with_cheapest_when_no_match(self):
        items = [{"name": f"P{i}", "price": 100 - i} for i in range(20)]
        out = ai._bound_catalog_for_llm(items, "zzz nonsense", cap=3)
        assert len(out) == 3
        prices = [p["price"] for p in out]
        assert prices == sorted(prices)  # cheapest-first padding


# ── _interest_names (no 'Nike, Nike' in orders) ─────────────────────────────────

class TestInterestNames:
    def test_dedupes_and_caps_to_three(self):
        products = [
            {"name": "Nike AF1"}, {"name": "Nike AF1"},
            {"name": "Adidas UB22"}, {"name": "Asics Gel"}, {"name": "NB 574"},
        ]
        assert ai._interest_names(products) == "Nike AF1, Adidas UB22, Asics Gel"

    def test_skips_blank_names(self):
        assert ai._interest_names([{"name": ""}, {"name": "Adidas"}]) == "Adidas"


# ── _product_label (precise order line) ─────────────────────────────────────────

class TestProductLabel:
    def test_appends_size_variant(self):
        out = ai._product_label({"name": "Adidas UB22 Black", "attributes": {"размер": "43"}})
        assert out == "Adidas UB22 Black (43)"

    def test_plain_name_without_attrs(self):
        assert ai._product_label({"name": "Adidas UB22 Black"}) == "Adidas UB22 Black"


# ── _looks_like_product_query (OFF_TOPIC override guard) ─────────────────────────

class TestLooksLikeProductQuery:
    _CATALOG = [
        {"name": "Adidas Stan Smith", "category": "Кроссовки"},
        {"name": "Nike Air Max 90 Black", "category": "Кроссовки"},
    ]

    def _patch(self, monkeypatch):
        monkeypatch.setattr(ai, "get_all_catalog_products", lambda sid: self._CATALOG)

    def test_product_class_word_matches_category(self, monkeypatch):
        # 'нью баланс' (brand absent from catalog, 'баланс' looks financial) but
        # 'кроссы'/'кроссовки' overlaps the catalog category → still shopping.
        self._patch(monkeypatch)
        assert ai._looks_like_product_query("есть кроссы от нью баланс", 1)
        assert ai._looks_like_product_query("нью баланс кроссовки", 1)

    def test_catalog_brand_word_matches_translit(self, monkeypatch):
        self._patch(monkeypatch)
        assert ai._looks_like_product_query("адидас есть?", 1)  # адидас → adidas

    def test_buy_intent_phrase_short_circuits(self):
        # No catalog read needed — a buy-intent phrase is enough on its own.
        assert ai._looks_like_product_query("сколько стоит", 1)

    def test_true_offtopic_is_not_product(self, monkeypatch):
        self._patch(monkeypatch)
        assert not ai._looks_like_product_query("какая сегодня погода", 1)
        assert not ai._looks_like_product_query("да ты заебал", 1)


# ── _is_meta_or_feedback (don't search on comments/complaints) ───────────────────

class TestIsMetaOrFeedback:
    def test_bot_directed_complaint(self):
        # A complaint that contains a brand must NOT be read as 'show me that brand'.
        assert ai._is_meta_or_feedback("почему ты показываешь адидас")
        assert ai._is_meta_or_feedback("что ты делаешь")
        assert ai._is_meta_or_feedback("ты опять одно и то же повторяешь")

    def test_assortment_comment(self):
        assert ai._is_meta_or_feedback("у вас только два можеля")
        assert ai._is_meta_or_feedback("это все модели")
        assert ai._is_meta_or_feedback("так мало вариантов")

    def test_filter_is_not_meta(self):
        # 'только' + a colour/size is a FILTER, not a comment.
        assert not ai._is_meta_or_feedback("только синие")
        assert not ai._is_meta_or_feedback("только 41 размер")

    def test_real_product_request_is_not_meta(self):
        assert not ai._is_meta_or_feedback("дай мне кроссы для бега")
        assert not ai._is_meta_or_feedback("мне нужна куртка")
        assert not ai._is_meta_or_feedback("адидас")
