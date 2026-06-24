"""Unit tests for pure bot-logic helpers in ai.py.

These are the highest-bug-density functions in the hot path: intent detection
and the anti-hallucination reply validator. No DB / network involved.
"""
import ai


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

    def test_empty(self):
        assert not ai.is_rejection("")


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
